// Tomodachi LLM: Skyline plugin (docs/how-it-works.md).
//
// - Reads content:/tomodachi-llm/config.ini from the mod's romfs (bridge host/port, debug log switch).
// - Hooks the MSBT label lookup and the text expansion function (re/offsets.md), after checking
//   that the first instructions match game 1.0.4. On mismatch it installs nothing.
// - Hooks only copy events into a fixed ring buffer (never block, drop when busy or full).
// - One worker thread sends the events as JSON lines to the bridge over TCP and/or to
//   svcOutputDebugString, and reads the bridge's messages with one non-blocking Recv per loop. All
//   socket I/O happens on that thread (CLAUDE.md rule 3). One thread only: Ryujinx 1.3.351 serializes
//   socket calls, and a second thread looping in Recv starved the sender (0.1.7 run).

#include <netinet/in.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>

#include "nn/fs.h"
#include "nn/os.hpp"
#include "nn/socket.h"
#include "nn/time.h"
#include "reline.h"
#include "relinestore.h"
#include "ring.h"
#include "msbt.h"

using tomodachi_llm::BuildReline;
using tomodachi_llm::JsonUnescape;
using tomodachi_llm::TemplateLen;
using tomodachi_llm::ForEachMessage;
using tomodachi_llm::GetRecord;
using tomodachi_llm::PutRecord;

extern "C" void A64HookFunction(void* const symbol, void* const replace, void** result);
extern "C" void* getRegionAddress(u8 region);  // skyline::utils::region: 0 = Text

namespace nn::socket {
s32 Fcntl(s32 socket, s32 cmd, ...);  // exported by the SDK as _ZN2nn6socket5FcntlEiiz
s32 GetLastErrno();
s64 Recv(s32 socket, void* buffer, u64 length, s32 flags);  // _ZN2nn6socket4RecvEiPvmi
}  // namespace nn::socket

namespace {

constexpr const char* kVersion = "0.7.2";
constexpr u64 kGhidraBase = 0x7100000000;

// Game 1.0.4 offsets from main's base (re/offsets.md), and the first four instruction words
// at each site (from main.elf) used as a version guard.
constexpr u64 kOffGetTextByLabel = 0x5abc00;
constexpr u64 kOffGetIndexByLabel = 0x8581f0;
constexpr u64 kOffTextExpand = 0x1508ac;
constexpr u64 kOffGetRegionLanguage = 0x74ee0c;
constexpr u64 kOffLangCodeTable = 0x319e910;
constexpr u32 kLangCodeCount = 18;
constexpr u64 kOffScenarioChatCalc = 0x192c53c;  // ScenarioChatSelector, vtable slot 40 (picks the chat, writes outputs)
constexpr u64 kOffChatMoodLabels = 0x31a0dc0;    // const char*[4]: ChatMiiNormal/Happy/Boredom/Fight
constexpr u64 kOffBlackboardNone = 0x52306e0;    // sentinel for an unbound blackboard property
constexpr u64 kOffScenarioChatStep = 0x192ce74;  // ScenarioChatSelector, vtable slot 43 (per-step child selection)
constexpr u64 kOffFocusTelopEnter = 0x82f9f0;    // SetGroupFocusTelopSelector, vtable slot 40 (shows a caption)
constexpr u64 kOffFriendUgcPick = 0x18c4048;      // GetTroubleFriendUgcTalkIndexRandomQuery, vtable slot 10 (picks the pattern)
constexpr u64 kOffFriendUgcLineLabel = 0x1a0a880; // builds "FriendUgc_<genre>_<outcome>_<pattern>_<line>"
constexpr u64 kMainTextEnd = 0x2fbd000;          // end of main's code (re/offsets.md segments), for backtraces
constexpr u32 kSigGetTextByLabel[4] = {0xf940000a, 0xb980214c, 0x3100059f, 0x540006a0};
constexpr u32 kSigGetIndexByLabel[4] = {0xb9802009, 0x3100053f, 0x54000740, 0xaa1f03e8};
constexpr u32 kSigTextExpand[4] = {0xd10443ff, 0xa90b7bfd, 0xa90c6ffc, 0xa90d67fa};
constexpr u32 kSigGetRegionLanguage[4] = {0xa9be7bfd, 0xf9000bf3, 0x910003fd, 0xf00256e8};
constexpr u32 kSigScenarioChatCalc[4] = {0xd10283ff, 0xfd0023ea, 0xf90037f9, 0x6d04a3e9};
constexpr u32 kSigScenarioChatStep[4] = {0xa9bd7bfd, 0xf9000bf5, 0xa9024ff4, 0x910003fd};
constexpr u32 kSigFocusTelopEnter[4] = {0xa9bd7bfd, 0xf9000bfc, 0x910003fd, 0xa9024ff4};
constexpr u32 kSigFriendUgcPick[4] = {0xd10203ff, 0xa9057bfd, 0xf90033f5, 0xa9074ff4};
constexpr u32 kSigFriendUgcLineLabel[4] = {0xd100c3ff, 0xa9027bfd, 0x910083fd, 0x293f13a5};

u64 g_base = 0;

// ---------------------------------------------------------------------------------------------
// Low-level helpers

void DebugOut(const char* s, size_t n) {
    register const char* x0 asm("x0") = s;
    register u64 x1 asm("x1") = n;
    asm volatile("svc 0x27" : "+r"(x0) : "r"(x1) : "memory");  // svcOutputDebugString
}

void Logf(const char* fmt, ...) {
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if ((size_t)n >= sizeof(buf)) n = sizeof(buf) - 1;
    DebugOut(buf, n);
}

u64 NowMs() {
    u64 t;
    asm volatile("mrs %0, cntpct_el0" : "=r"(t));
    return t / 19200;  // 19.2 MHz system counter
}

u64 Fnv1a(const char* s, size_t n) {
    u64 h = 0xcbf29ce484222325ull;
    for (size_t i = 0; i < n; i++) h = (h ^ (u8)s[i]) * 0x100000001b3ull;
    return h;
}

// Appends to a bounded buffer; keeps it NUL-terminated and never overflows.
struct Out {
    char* p;
    size_t cap;
    size_t n = 0;
    void Put(char c) {
        if (n + 1 < cap) p[n++] = c;
        p[n < cap ? n : cap - 1] = 0;
    }
    void Puts(const char* s) {
        while (*s) Put(*s++);
    }
    void Printf(const char* fmt, ...) {
        if (n + 1 >= cap) return;
        va_list ap;
        va_start(ap, fmt);
        int w = vsnprintf(p + n, cap - n, fmt, ap);
        va_end(ap);
        if (w > 0) n = (n + w < cap) ? n + w : cap - 1;
    }
    void JsonChar(u32 cp) {
        if (cp == '"' || cp == '\\') {
            Put('\\');
            Put((char)cp);
        } else if (cp < 0x20) {
            Printf("\\u%04x", cp);
        } else if (cp < 0x80) {
            Put((char)cp);
        } else if (cp < 0x800) {
            Put(0xC0 | (cp >> 6));
            Put(0x80 | (cp & 0x3F));
        } else if (cp < 0x10000) {
            Put(0xE0 | (cp >> 12));
            Put(0x80 | ((cp >> 6) & 0x3F));
            Put(0x80 | (cp & 0x3F));
        } else {
            Put(0xF0 | (cp >> 18));
            Put(0x80 | ((cp >> 12) & 0x3F));
            Put(0x80 | ((cp >> 6) & 0x3F));
            Put(0x80 | (cp & 0x3F));
        }
    }
    void JsonAscii(const char* s, size_t max) {
        for (size_t i = 0; i < max && s[i]; i++) JsonChar((u8)s[i]);
    }
    // MSBT UTF-16 text: tags (0x0E group type size params…) become {group.type}, tag ends (0x0F) {/}.
    void JsonMsbt(const u16* s, size_t len) {
        for (size_t i = 0; i < len && s[i];) {
            u16 c = s[i];
            if (c == 0x0E) {
                if (i + 3 >= len) break;
                Printf("{%u.%u}", s[i + 1], s[i + 2]);
                i += 4 + s[i + 3] / 2;
                continue;
            }
            if (c == 0x0F) {
                Puts("{/}");
                i += 3;
                continue;
            }
            u32 cp = c;
            i++;
            if (c >= 0xD800 && c < 0xDC00 && i < len && s[i] >= 0xDC00 && s[i] < 0xE000) {
                cp = 0x10000 + ((c - 0xD800) << 10) + (s[i] - 0xDC00);
                i++;
            }
            JsonChar(cp);
        }
    }
};

size_t U16Len(const u16* s, size_t max) {
    size_t n = 0;
    while (n < max && s[n]) n++;
    return n;
}

// ---------------------------------------------------------------------------------------------
// Config: content:/tomodachi-llm/config.ini (key=value lines, '#' comments)

struct Config {
    char host[64] = "";
    u16 port = 0;
    bool debugLog = true;   // also write each event to svcOutputDebugString (Ryujinx log)
    bool hookLabels = true;
    bool hookLines = true;
    bool rx = true;         // read the bridge's messages (welcome, pong, later result)
    bool reline = true;         // play the bridge's generated replacements for lines seen before
    bool fileTransport = true;  // talk to the bridge through files on the SD card (sd:/tomodachi-llm), not TCP
    bool backtrace = true;  // add a frame-pointer backtrace to dialogue line events (finds the code driving them)
    bool hookIndex = true;  // the MessageShuffler's label lookups (Msbt_GetIndexByLabel): whole caption files
    bool hookChat = true;   // log Mii-to-Mii chat setup (ScenarioChatSelector), observation only
    // Label prefixes of scripted scenes: the first time the game looks up such a label in a message file, report
    // every label of that file with this prefix (text and key), so the bridge knows the whole script.
    char scripts[4][32] = {};
};
Config g_cfg;

void LoadConfig() {
    nn::fs::FileHandle h;
    if (R_FAILED(nn::fs::OpenFile(&h, "content:/tomodachi-llm/config.ini", nn::fs::OpenMode_Read))) {
        Logf("[tomodachi-llm] no content:/tomodachi-llm/config.ini; logging to the debug output only");
        return;
    }
    static char buf[4096];  // the config file is read once, at load
    s64 size = 0;
    nn::fs::GetFileSize(&size, h);
    if (size > (s64)sizeof(buf) - 1) size = sizeof(buf) - 1;
    u64 got = 0;
    nn::fs::ReadFile(&got, h, 0, buf, size);
    nn::fs::CloseFile(h);
    buf[got < sizeof(buf) ? got : sizeof(buf) - 1] = 0;

    for (char* line = buf; line && *line;) {
        char* next = strchr(line, '\n');
        if (next) *next++ = 0;
        char* eq = strchr(line, '=');
        if (line[0] != '#' && eq) {
            *eq = 0;
            char* key = line;
            char* val = eq + 1;
            for (char* e = val + strlen(val); e > val && (e[-1] == '\r' || e[-1] == ' '); e--) e[-1] = 0;
            while (*key == ' ') key++;
            for (char* e = key + strlen(key); e > key && e[-1] == ' '; e--) e[-1] = 0;
            while (*val == ' ') val++;
            if (!strcmp(key, "host")) {
                strncpy(g_cfg.host, val, sizeof(g_cfg.host) - 1);
            } else if (!strcmp(key, "port")) {
                long p = 0;
                for (const char* c = val; *c >= '0' && *c <= '9'; c++) p = p * 10 + (*c - '0');
                g_cfg.port = (p > 0 && p < 65536) ? (u16)p : 0;
            } else if (!strcmp(key, "debug_log")) {
                g_cfg.debugLog = val[0] == '1';
            } else if (!strcmp(key, "hook_labels")) {
                g_cfg.hookLabels = val[0] == '1';
            } else if (!strcmp(key, "hook_lines")) {
                g_cfg.hookLines = val[0] == '1';
            } else if (!strcmp(key, "hook_index")) {
                g_cfg.hookIndex = val[0] == '1';
            } else if (!strcmp(key, "hook_chat")) {
                g_cfg.hookChat = val[0] == '1';
            } else if (!strcmp(key, "reline")) {
                g_cfg.reline = val[0] == '1';
            } else if (!strcmp(key, "transport")) {
                g_cfg.fileTransport = strcmp(val, "socket") != 0;
            } else if (!strcmp(key, "backtrace")) {
                g_cfg.backtrace = val[0] == '1';
            } else if (!strcmp(key, "scripts")) {
                int k = 0;
                for (char* p = val; *p && k < 4;) {
                    char* comma = strchr(p, ',');
                    if (comma) *comma = 0;
                    while (*p == ' ') p++;
                    if (*p) strncpy(g_cfg.scripts[k++], p, sizeof(g_cfg.scripts[0]) - 1);
                    p = comma ? comma + 1 : p + strlen(p);
                }
            } else if (!strcmp(key, "rx")) {
                g_cfg.rx = val[0] == '1';
            }
        }
        line = next;
    }
    for (auto& sp : g_cfg.scripts)
        if (sp[0]) Logf("[tomodachi-llm] config: scripts prefix %s", sp);
    Logf("[tomodachi-llm] config: reline=%d transport=%s host=%s port=%u debug_log=%d hook_labels=%d hook_lines=%d hook_chat=%d rx=%d backtrace=%d",
         g_cfg.reline, g_cfg.fileTransport ? "file" : "socket", g_cfg.host, g_cfg.port, g_cfg.debugLog, g_cfg.hookLabels, g_cfg.hookLines, g_cfg.hookChat, g_cfg.rx,
         g_cfg.backtrace);
}

// ---------------------------------------------------------------------------------------------
// Event ring: many producers (game threads, try-lock, drop on contention), one consumer (worker).

constexpr u32 kRingBytes = 512 * 1024;  // ~2,900 events of the average size; the boot burst is ~1,800
constexpr u32 kEventBytes = 1536;       // the largest event
tomodachi_llm::ByteRing<kRingBytes, kEventBytes> g_ring;
u32 g_pushed = 0;  // events queued (under g_lock)
u32 g_popped = 0;  // events taken by the worker
bool g_lock = false;
u32 g_dropped = 0;
u32 g_emitted = 0;  // under g_lock
u32 g_deduped = 0;  // under g_lock

constexpr u32 kRecent = 64;
u64 g_recentHash[kRecent];
u64 g_recentMs[kRecent];
u32 g_recentPos = 0;

// body: JSON object members without braces. Duplicates of a recent body (3 s) are skipped,
// because text boxes may re-expand the same line every frame.
// Returns false when the event was not queued: lock contention or a full ring. Those count as dropped, except
// for the worker's own paced events (`paced`), which it retries.
bool TryEmit(const char* body, size_t n, bool paced) {
    if (__atomic_test_and_set(&g_lock, __ATOMIC_ACQUIRE)) {
        if (!paced) __atomic_add_fetch(&g_dropped, 1, __ATOMIC_RELAXED);
        return false;
    }
    bool ok = true;
    u64 now = NowMs();
    u64 h = Fnv1a(body, n);
    bool dup = false;
    for (u32 i = 0; i < kRecent; i++)
        if (g_recentHash[i] == h && now - g_recentMs[i] < 3000) dup = true;
    if (dup) {
        g_deduped++;
    } else {
        g_emitted++;
        g_recentHash[g_recentPos] = h;
        g_recentMs[g_recentPos] = now;
        g_recentPos = (g_recentPos + 1) % kRecent;
        char* d = g_ring.Reserve();
        if (!d) {
            if (!paced) __atomic_add_fetch(&g_dropped, 1, __ATOMIC_RELAXED);
            ok = false;
        } else {
            Out o{d, kEventBytes};
            o.Printf("{\"ms\":%llu,", (unsigned long long)now);
            for (size_t i = 0; i < n; i++) o.Put(body[i]);
            o.Puts("}\n");
            g_ring.Commit((u32)o.n);
            g_pushed++;
        }
    }
    __atomic_clear(&g_lock, __ATOMIC_RELEASE);
    return ok;
}

void Emit(const char* body, size_t n) { TryEmit(body, n, false); }

u32 RingFree() { return g_ring.Free(); }

u64 GhidraAddr(u64 runtime) { return runtime - g_base + kGhidraBase; }

// Scratch buffers for the hooks. Hooks run on game threads, inside the game's own calls, and some of those threads
// have small stacks: plugin 0.6.3 put ~2.9 KB of buffers on the stack under Text_Expand and the game froze in one
// Mii News report (2026-10-11). Hooks take their buffers from this pool instead (lock-free; a hook that finds none
// free plays the vanilla line or drops its event, counted in `stats` as `scratch_miss`).
constexpr u32 kScratchN = 16;
constexpr u32 kScratchBytes = 2048;
alignas(8) char g_scratch[kScratchN][kScratchBytes];
u32 g_scratchUsed = 0;  // bit i: buffer i taken
u32 g_scratchMiss = 0;
struct Scratch {
    char* p = nullptr;
    u32 bit = 0;
    Scratch() {
        for (u32 i = 0; i < kScratchN; i++) {
            u32 b = 1u << i;
            if (!(__atomic_fetch_or(&g_scratchUsed, b, __ATOMIC_ACQUIRE) & b)) {
                p = g_scratch[i];
                bit = b;
                return;
            }
        }
        __atomic_add_fetch(&g_scratchMiss, 1, __ATOMIC_RELAXED);
    }
    ~Scratch() {
        if (bit) __atomic_fetch_and(&g_scratchUsed, ~bit, __ATOMIC_RELEASE);
    }
    Scratch(const Scratch&) = delete;
    Scratch& operator=(const Scratch&) = delete;
};

// ---------------------------------------------------------------------------------------------
// Hooks

u64 TemplateKey(const u16* s, u32 n);

struct TextRef {  // returned in x8 by Msbt_GetTextByLabel
    const u16* text;
    s32 len;
    s32 pad;
    s64 index;
};
TextRef (*g_origGetText)(void* msbt, const char* label) = nullptr;

// Whole message files read ahead (`scripts=`: befriend scripts, chat captions). When a game thread looks
// up a label with a configured prefix, the file is loaded and its sections are valid: it copies the messages with
// that prefix (label + template) into the dump arena. The worker then reports them as label events marked "dump",
// paced so the event ring keeps room for the game's own events (a caption file has ~1,000).
// The arena is small (the plugin's memory comes out of the game's heap), so a file that doesn't fit is read in
// pieces: each piece is one Dump entry, and the next piece is copied at a later lookup of the same file once the
// previous one has been reported and the arena freed.
// Section layout as Msbt_GetTextByLabel reads it (re/offsets.md): header+0x18 sections (0x18 bytes each: data
// pointer, size at +0xc), +0x20 LBL1 index, +0x24 TXT2 index.
constexpr u32 kMaxDumps = 24;
constexpr u32 kDumpArenaBytes = 192 * 1024;  // a befriend file takes ~100 KB, the largest caption file ~220 KB
constexpr u32 kDumpUnits = 401;  // a template over 400 units is reported cut (EmitLabel)
struct Dump {
    const void* hdr;  // the file, so each one is read once
    char prefix[32];
    u32 off, end;     // its records in g_dumpArena: u8 label length, label, pad to 2, u32 units, units * u16
    u32 labels;
    u32 next;         // matching messages read up to the end of this piece
    bool more;        // the file has more matching messages (a later piece)
    bool done;        // all reported (worker)
};
alignas(4) u8 g_dumpArena[kDumpArenaBytes];
Dump g_dumps[kMaxDumps];
u32 g_dumpsN = 0;     // entries published (release) once filled
u32 g_dumpUsed = 0;   // arena bytes in use (under g_dumpLock)
u32 g_dumpNext = 0;   // worker: first entry not done, and its read position
u32 g_dumpPos = 0;
u32 g_dumpSkipped = 0;  // files that didn't parse, or entries that ran out
bool g_dumpLock = false;

// The latest piece of `hdr`'s `prefix` messages, or nullptr. Entries are only appended, so this is safe unlocked.
const Dump* LastPiece(const u8* hdr, const char* prefix, u32 n) {
    const Dump* last = nullptr;
    for (u32 i = 0; i < n; i++)
        if (g_dumps[i].hdr == hdr && !strcmp(g_dumps[i].prefix, prefix)) last = &g_dumps[i];
    return last;
}

// Read nothing (done, or a piece is still being reported), or the index of the first message still to read.
bool DumpWanted(const u8* hdr, const char* prefix, u32 n, u32* from) {
    const Dump* last = LastPiece(hdr, prefix, n);
    if (!last) {
        *from = 0;
        return true;
    }
    if (!last->more || !__atomic_load_n(&last->done, __ATOMIC_ACQUIRE)) return false;
    *from = last->next;
    return true;
}

void DumpScripts(const u8* hdr, const char* prefix) {
    if (!hdr) return;
    u32 from = 0;
    if (!DumpWanted(hdr, prefix, __atomic_load_n(&g_dumpsN, __ATOMIC_ACQUIRE), &from)) return;
    if (__atomic_test_and_set(&g_dumpLock, __ATOMIC_ACQUIRE)) return;  // another thread is copying: next lookup
    u32 n = g_dumpsN;
    s32 li = *(const s32*)(hdr + 0x20), ti = *(const s32*)(hdr + 0x24);
    const u8* secs = *(const u8* const*)(hdr + 0x18);
    if (DumpWanted(hdr, prefix, n, &from) && n < kMaxDumps && secs && li >= 0 && ti >= 0 && li <= 16 && ti <= 16) {
        const u8* lbl = *(const u8* const*)(secs + li * 0x18);
        const u8* txt = *(const u8* const*)(secs + ti * 0x18);
        u32 lblSize = *(const u32*)(secs + li * 0x18 + 0xc), txtSize = *(const u32*)(secs + ti * 0x18 + 0xc);
        size_t pl = strlen(prefix);
        u32 start = g_dumpUsed, at = start, labels = 0, seen = 0;
        bool full = false;
        ForEachMessage(lbl, lblSize, txt, txtSize, [&](const char* name, u32 len, const u16* text, u32 units) {
            if (full || len < pl || memcmp(name, prefix, pl) != 0) return;
            if (seen++ < from) return;  // read in an earlier piece
            if (units > kDumpUnits) units = kDumpUnits;
            if (!PutRecord(g_dumpArena, kDumpArenaBytes, &at, name, len, text, units)) {
                full = true;
                return;
            }
            labels++;
        });
        if (labels || !full) {  // nothing fitted: the arena is still in use, try at a later lookup
            Dump& d = g_dumps[n];
            d = Dump{hdr, {}, start, at, labels, from + labels, full, !labels};
            strncpy(d.prefix, prefix, sizeof(d.prefix) - 1);
            if (!labels && !from) g_dumpSkipped++;  // no messages with this prefix (or the file didn't parse)
            g_dumpUsed = at;
            __atomic_store_n(&g_dumpsN, n + 1, __ATOMIC_RELEASE);
        }
    } else if (n >= kMaxDumps && DumpWanted(hdr, prefix, n, &from)) {
        g_dumpSkipped++;
    }
    __atomic_clear(&g_dumpLock, __ATOMIC_RELEASE);
}

void EmitScriptDump(const Dump& d) {
    char body[160];
    Out o{body, sizeof(body)};
    o.Puts("\"t\":\"script_dump\",\"prefix\":\"");
    o.JsonAscii(d.prefix, 32);
    o.Printf("\",\"labels\":%u", d.labels);
    TryEmit(body, o.n, true);
}

bool EmitLabel(const char* label, u32 labelLen, bool found, u64 lr, const u16* text, u32 units, bool dump);

// Worker: reports up to `budget` dumped labels while the ring has room; frees the arena once all are reported.
void PumpDumps(u32 budget) {
    u32 n = __atomic_load_n(&g_dumpsN, __ATOMIC_ACQUIRE);
    while (g_dumpNext < n && budget) {
        Dump& d = g_dumps[g_dumpNext];
        if (d.done) {
            g_dumpNext++;
            continue;
        }
        if (!g_dumpPos) g_dumpPos = d.off;
        while (g_dumpPos < d.end && budget && RingFree() > kRingBytes / 4) {
            u32 p = g_dumpPos, len, units;
            const char* name;
            const u16* text;
            GetRecord(g_dumpArena, &p, &name, &len, &text, &units);
            if (!EmitLabel(name, len, true, 0, text, units, true)) return;  // ring busy: retry this one later
            g_dumpPos = p;
            budget--;
        }
        if (g_dumpPos < d.end) return;
        EmitScriptDump(d);
        __atomic_store_n(&d.done, true, __ATOMIC_RELEASE);
        g_dumpNext++;
        g_dumpPos = 0;
    }
    if (g_dumpNext == n && g_dumpUsed && !__atomic_test_and_set(&g_dumpLock, __ATOMIC_ACQUIRE)) {
        if (__atomic_load_n(&g_dumpsN, __ATOMIC_ACQUIRE) == n) g_dumpUsed = 0;  // nothing new came in meanwhile
        __atomic_clear(&g_dumpLock, __ATOMIC_RELEASE);
    }
}

bool EmitLabel(const char* label, u32 labelLen, bool found, u64 lr, const u16* text, u32 units, bool dump) {
    Scratch body;
    if (!body.p) return false;
    Out o{body.p, 1400};
    o.Puts("\"t\":\"label\",\"label\":\"");
    o.JsonAscii(label, labelLen < 120 ? labelLen : 120);
    o.Printf("\",\"found\":%d,\"lr\":\"0x%llx\",", found ? 1 : 0, (unsigned long long)lr);
    if (dump) o.Puts("\"dump\":1,");
    u32 tl = text && units > 0 ? TemplateLen(text, units) : 0;
    // Same key as the line event Text_Expand will report for this template: lets the bridge prepare
    // relines for lines that haven't been shown yet (whole scenes).
    if (tl) o.Printf("\"key\":\"%016llx\",", (unsigned long long)TemplateKey(text, tl));
    if (tl > 400) o.Puts("\"cut\":1,");
    o.Puts("\"text\":\"");
    if (tl) o.JsonMsbt(text, tl < 400 ? tl : 400);
    o.Put('"');
    return TryEmit(body.p, o.n, dump);
}

bool MatchesScript(const char* label) {
    for (auto& sp : g_cfg.scripts)
        if (sp[0] && !strncmp(label, sp, strlen(sp))) return true;
    return false;
}

void DumpMatching(const u8* hdr, const char* label) {
    for (auto& sp : g_cfg.scripts)
        if (sp[0] && !strncmp(label, sp, strlen(sp))) DumpScripts(hdr, sp);
}

TextRef HookGetText(void* msbt, const char* label) {
    TextRef r = g_origGetText(msbt, label);
    if (label) {
        u64 lr = (u64)__builtin_return_address(0);
        EmitLabel(label, strlen(label), r.text != nullptr, GhidraAddr(lr), r.text, r.text && r.len > 0 ? (u32)r.len : 0,
                  false);
        if (r.text && msbt && MatchesScript(label)) DumpMatching(*(const u8* const*)msbt, label);
    }
    return r;
}

// Msbt_GetIndexByLabel(header, label): the MessageShuffler's lookups (variants picked by attribute, e.g. chat
// captions) go through it rather than GetTextByLabel. Hooked only to read whole files (`scripts=`); no events.
// Diagnostics (0.6.1): the first kIndexSeen distinct labels looked up this way are reported as "index_label"
// events, so a session shows which labels the captions use if the configured prefixes never match.
constexpr u32 kIndexSeen = 256;
u64 g_indexSeen[kIndexSeen];
u32 g_indexSeenN = 0;
bool g_indexLock = false;
s32 (*g_origGetIndex)(const u8* hdr, const char* label) = nullptr;
s32 HookGetIndex(const u8* hdr, const char* label) {
    s32 r = g_origGetIndex(hdr, label);
    if (!hdr || !label) return r;
    if (r >= 0 && MatchesScript(label)) DumpMatching(hdr, label);
    if (__atomic_load_n(&g_indexSeenN, __ATOMIC_RELAXED) < kIndexSeen &&
        !__atomic_test_and_set(&g_indexLock, __ATOMIC_ACQUIRE)) {
        size_t n = strnlen(label, 120);
        u64 h = Fnv1a(label, n) ^ (u64)hdr;
        bool seen = false;
        for (u32 i = 0; i < g_indexSeenN && !seen; i++) seen = g_indexSeen[i] == h;
        if (!seen && g_indexSeenN < kIndexSeen) {
            g_indexSeen[g_indexSeenN++] = h;
            char body[240];
            Out o{body, sizeof(body)};
            o.Puts("\"t\":\"index_label\",\"label\":\"");
            o.JsonAscii(label, (u32)n);
            o.Printf("\",\"found\":%d,\"file\":\"%llx\",\"lr\":\"0x%llx\"", r >= 0 ? 1 : 0, (unsigned long long)(u64)hdr,
                     (unsigned long long)GhidraAddr((u64)__builtin_return_address(0)));
            Emit(body, o.n);
        }
        __atomic_clear(&g_indexLock, __ATOMIC_RELEASE);
    }
    return r;
}

// Walks the frame-pointer chain (AArch64 frame records: [fp] = caller's fp, [fp+8] = return address) and
// writes up to `max` return addresses inside main's code as Ghidra addresses. Stops at anything that doesn't
// look like a frame on this stack.
void PutBacktrace(Out& o, int max) {
    u64 fp = (u64)__builtin_frame_address(0);
    u64 lo = fp;
    o.Puts(",\"bt\":[");
    int n = 0;
    for (int depth = 0; depth < 16 && n < max; depth++) {
        if (!fp || (fp & 7) || fp < lo || fp - lo > 0x100000) break;
        u64 next = ((const u64*)fp)[0];
        u64 lr = ((const u64*)fp)[1];
        if (lr >= g_base && lr < g_base + kMainTextEnd) {
            o.Printf(n ? ",\"0x%llx\"" : "\"0x%llx\"", (unsigned long long)GhidraAddr(lr));
            n++;
        }
        if (next <= fp) break;
        fp = next;
    }
    o.Put(']');
}

// True if the return address `ghidraTarget` is on the call stack within `maxDepth` frames (same walk as above).
bool OnStack(u64 ghidraTarget, int maxDepth) {
    u64 fp = (u64)__builtin_frame_address(0);
    u64 lo = fp;
    u64 target = ghidraTarget - kGhidraBase + g_base;
    for (int depth = 0; depth < maxDepth; depth++) {
        if (!fp || (fp & 7) || fp < lo || fp - lo > 0x100000) break;
        u64 next = ((const u64*)fp)[0];
        if (((const u64*)fp)[1] == target) return true;
        if (next <= fp) break;
        fp = next;
    }
    return false;
}

// Narrator captions (FocusTelop) are expanded by the caller below, which the conversation log, dialogs and
// tutorials share; a caption's call stack goes through the caption window's text setter (return address
// kCaptionFrame, re/offsets.md; every caption in our logs, nothing else).
constexpr u64 kCaptionCaller = 0x710045e9cc;
constexpr u64 kCaptionFrame = 0x7100643428;

// Text_Expand callers that only build UI text (re/offsets.md); no backtrace for those.
bool IsUiCaller(u64 ghidraLr) {
    return ghidraLr == 0x710045e924 || ghidraLr == 0x7100a2d864 || ghidraLr == 0x710073c090 || ghidraLr == 0x7100786d60;
}

// ---------------------------------------------------------------------------------------------
// Relines: generated replacements pushed by the bridge, keyed by a hash of the raw template.
// The worker thread stores them; Text_Expand (game threads) reads them (relinestore.h).

u64 TemplateKey(const u16* s, u32 n) {
    u64 h = 0xcbf29ce484222325ull;
    for (u32 i = 0; i < n; i++) h = (h ^ s[i]) * 0x100000001b3ull;
    return h | 1;  // never 0 (0 = empty slot)
}

constexpr u32 kRelineBytes = 600;
// 4,096 entries and 256 KB of text (~350 KB): lines saved by the bridge from earlier sessions arrive at hello
// (up to Capacity() lines and Bytes() of text). The plugin's memory comes out of the game's heap (Skyline
// allocates it), so it stays small: 0.6.1 took 3.3 MB and the game froze loading the Mii News.
tomodachi_llm::RelineStore<4096, 256 * 1024, kRelineBytes> g_relines;
u32 g_relinesUsed = 0;
u32 g_relinesBad = 0;

// Text_Expand returns a value (it ends by returning another call's result; Ghidra shows it as void). The UI text
// setter at 0x710045e924 uses it (choice menus: without it they show only their first option). Every hook passes
// its original's return value through, as u64, even where the function looks void.
using TextExpandFn = u64 (*)(void*, u16*, u32*, void*, u64, const u16*, u64, u64, u64, void*, u8*);
TextExpandFn g_origTextExpand = nullptr;

// The generated replacement for this template into `alt` (640 units) from the store, via `text` (kRelineBytes).
// Not inlined, so its frame (BuildReline's runs) is gone before the game's Text_Expand runs.
__attribute__((noinline)) u32 PrepareReline(const u16* src, u32 sl, u64 key, u16* alt, char* text) {
    u16 tlen = 0;
    if (!g_relines.Lookup(key, text, &tlen)) return 0;
    u32 altLen = BuildReline(src, sl, text, tlen, alt, 639);
    if (!altLen) {
        g_relinesBad++;
        return 0;
    }
    alt[altLen] = 0;
    if (src[sl - 1] == 0) altLen++;  // same length convention as the game's template
    g_relinesUsed++;
    return altLen;
}

__attribute__((noinline)) void LogLine(u64 lr, u64 key, u32 altLen, u16* dst, u32* outLen, u64 dstCap,
                                       const u16* src, u32 sl, u8* mp);

// Text_Expand: kept small on the stack (see Scratch): the buffers come from the pool, the event is written after
// the game's call returns.
u64 HookTextExpand(void* proc, u16* dst, u32* outLen, void* p4, u64 dstCap, const u16* src, u64 srcLen, u64 p8,
                    u64 p9, void* p10, u8* mp) {
    u64 lr = (u64)__builtin_return_address(0);
    u32 sl = (u32)srcLen;
    u64 key = (src && sl && sl < 1024) ? TemplateKey(src, TemplateLen(src, sl)) : 0;
    // A generated replacement for this template, if the bridge sent one (balloon, voice and log alike).
    u32 altLen = 0;
    Scratch buf;  // 640 units of template, then kRelineBytes of text
    u16* alt = (u16*)buf.p;
    if (key && g_cfg.reline && buf.p) altLen = PrepareReline(src, sl, key, alt, buf.p + 1280);
    u64 ret = altLen ? g_origTextExpand(proc, dst, outLen, p4, dstCap, alt, altLen, p8, p9, p10, mp)
                     : g_origTextExpand(proc, dst, outLen, p4, dstCap, src, srcLen, p8, p9, p10, mp);
    LogLine(lr, key, altLen, dst, outLen, dstCap, src, sl, mp);
    return ret;
}

void LogLine(u64 lr, u64 key, u32 altLen, u16* dst, u32* outLen, u64 dstCap, const u16* src, u32 sl, u8* mp) {
    Scratch body;
    if (!body.p) {
        __atomic_add_fetch(&g_dropped, 1, __ATOMIC_RELAXED);
        return;
    }
    Out o{body.p, 1400};
    o.Printf("\"t\":\"line\",\"lr\":\"0x%llx\",\"key\":\"%016llx\",", (unsigned long long)GhidraAddr(lr),
             (unsigned long long)key);
    if (altLen) o.Puts("\"relined\":1,");
    if (GhidraAddr(lr) == kCaptionCaller && OnStack(kCaptionFrame, 32)) o.Puts("\"caption\":1,");
    o.Puts("\"src\":\"");
    if (src) o.JsonMsbt(src, sl < 400 ? sl : 400);
    o.Put('"');
    if (sl > 400) o.Puts(",\"cut\":1");  // the bridge must not plan a reline from a cut template
    o.Puts(",\"out\":\"");
    if (dst && dstCap) {
        size_t cap = dstCap < 1024 ? dstCap : 1024;
        size_t n = (outLen && *outLen <= cap) ? *outLen : U16Len(dst, cap);
        o.JsonMsbt(dst, n < 400 ? n : 400);
    }
    o.Put('"');
    if (mp && ((u64)mp & 7) == 0) {
        const u32* ugc = (const u32*)(mp + 0x3b0);
        o.Printf(",\"ugc\":[%u,%u,%u,%u]", ugc[0], ugc[1], ugc[2], ugc[3]);
    }
    if (g_cfg.backtrace && !IsUiCaller(GhidraAddr(lr))) PutBacktrace(o, 10);
    Emit(body.p, o.n);
}

// ScenarioChatSelector (re/offsets.md). Observation only for now: after the game picks a chat, log its type,
// mood label, item/word inputs and the word slots of both MessageParams. Reads are guarded; nothing is written.
struct Ret16 {
    u64 x0, x1;
};
Ret16 (*g_origChatCalc)(u8* node) = nullptr;

// A blackboard property field holds a tagged pointer (low 2 bits are flags) to the value.
template <typename T>
const T* BbValue(const u8* node, u32 off) {
    u64 v = *(const u64*)(node + off) & ~3ull;
    if (!v || (v & (alignof(T) - 1)) || v == g_base + kOffBlackboardNone) return nullptr;
    return (const T*)v;
}

// Fields like node+0x190 point to a slot holding a MessageParam*.
const u8* MessageParamAt(const u8* node, u32 off) {
    u64 slot = *(const u64*)(node + off);
    if (!slot || (slot & 7) || slot == g_base + kOffBlackboardNone) return nullptr;
    u64 mp = *(const u64*)slot;
    return (mp && !(mp & 7)) ? (const u8*)mp : nullptr;
}

Ret16 HookChatCalc(u8* node) {
    Ret16 r = g_origChatCalc(node);
    Scratch body;
    if (!body.p) return r;
    Out o{body.p, 700};
    o.Printf("\"t\":\"chat\",\"ok\":%d,\"type\":%d,\"subtype\":%d,\"pick\":%d,\"turns\":%d,\"flag70\":%d",
             r.x1 == 0 ? 1 : 0, *(s32*)(node + 0x68), *(s32*)(node + 0x6c), *(s32*)(node + 0x80), *(s32*)(node + 0x88),
             node[0x70]);
    const u32 inOffs[3] = {0x178, 0x180, 0x188};
    o.Puts(",\"in\":[");
    for (int i = 0; i < 3; i++) {
        const s32* v = BbValue<s32>(node, inOffs[i]);
        o.Printf(i ? ",%d" : "%d", v ? *v : -9999);
    }
    o.Put(']');
    if (const u64* mood = BbValue<u64>(node, 0x1b0)) {
        const u64* table = (const u64*)(g_base + kOffChatMoodLabels);
        for (int i = 0; i < 4; i++)
            if (*mood == table[i]) {
                o.Puts(",\"mood\":\"");
                o.JsonAscii((const char*)table[i], 40);
                o.Put('"');
            }
    }
    if (const s32* rep = BbValue<s32>(node, 0x1b8)) o.Printf(",\"repeat\":%d", *rep);
    const u32 mpOffs[2] = {0x190, 0x198};
    for (int i = 0; i < 2; i++) {
        const u8* mp = MessageParamAt(node, mpOffs[i]);
        if (!mp) continue;
        const u32* ugc = (const u32*)(mp + 0x3b0);
        o.Printf(",\"mp%x_ugc\":[%u,%u,%u,%u]", mpOffs[i], ugc[0], ugc[1], ugc[2], ugc[3]);
    }
    const u32 listOffs[2] = {0x1a0, 0x1a8};  // TalkerList, ListenerList (raw pointers, for correlation)
    for (int i = 0; i < 2; i++) {
        const u64* v = BbValue<u64>(node, listOffs[i]);
        o.Printf(",\"list%x\":\"0x%llx\"", listOffs[i], (unsigned long long)(v ? *v : 0));
    }
    Emit(body.p, o.n);
    return r;
}

// ScenarioChatSelector per-step method: logs which child it is on (identical events within 3 s are deduplicated).
u64 (*g_origChatStep)(u8* node, void* ctx) = nullptr;
u64 HookChatStep(u8* node, void* ctx) {
    u8 before = node[0x1e];
    u64 ret = g_origChatStep(node, ctx);
    char body[200];
    Out o{body, sizeof(body)};
    o.Printf("\"t\":\"chat_step\",\"node\":\"0x%llx\",\"child\":%d,\"after\":%d,\"turn\":%d,\"turns\":%d",
             (unsigned long long)node, before, node[0x1e], *(s32*)(node + 0x84), *(s32*)(node + 0x88));
    Emit(body, o.n);
    return ret;
}

// SetGroupFocusTelopSelector enter: the caption's mstxt file (node+0x70 -> slot -> const char*) and message
// label (node+0x88, tagged pointer -> const char*).
Ret16 (*g_origTelopEnter)(u8* node) = nullptr;
__attribute__((noinline)) void LogTelop(u8* node) {
    Scratch body;
    if (!body.p) return;
    Out o{body.p, 600};
    o.Puts("\"t\":\"telop\"");
    if (const u64* label = BbValue<u64>(node, 0x88); label && *label) {
        o.Puts(",\"label\":\"");
        o.JsonAscii((const char*)*label, 120);
        o.Put('"');
    }
    u64 slot = *(const u64*)(node + 0x70);
    if (slot && !(slot & 7) && *(const u64*)slot) {
        o.Puts(",\"mstxt\":\"");
        o.JsonAscii((const char*)*(const u64*)slot, 120);
        o.Put('"');
    }
    if (const u8* mp = MessageParamAt(node, 0x80)) {
        const u32* ugc = (const u32*)(mp + 0x3b0);
        o.Printf(",\"ugc\":[%u,%u,%u,%u]", ugc[0], ugc[1], ugc[2], ugc[3]);
    }
    PutBacktrace(o, 8);
    Emit(body.p, o.n);
}
Ret16 HookTelopEnter(u8* node) {
    LogTelop(node);
    return g_origTelopEnter(node);
}

// Writes `"key":"<string>"` for a blackboard string output (tagged pointer -> const char*), if set.
void PutBbString(Out& o, const u8* node, u32 off, const char* key) {
    const u64* v = BbValue<u64>(node, off);
    if (!v || !*v) return;
    o.Printf(",\"%s\":\"", key);
    o.JsonAscii((const char*)*v, 64);
    o.Put('"');
}

// "Help me befriend X" conversation (Trouble FriendUgc): the query that picks the conversation pattern. Its
// outputs name the whole conversation: DemoForkStrMain (Success/Failure/...), DemoForkStrSub1 (genre),
// DemoForkStrSub2 (pattern number). The prefetch point for these conversations.
u64 (*g_origFriendUgcPick)(u8* node) = nullptr;
u64 HookFriendUgcPick(u8* node) {
    u64 ret = g_origFriendUgcPick(node);
    Scratch body;
    if (!body.p) return ret;
    Out o{body.p, 400};
    o.Printf("\"t\":\"friend_ugc\",\"genre_id\":%d,\"fork_id\":%d", *(s32*)(node + 0x28), *(s32*)(node + 0x2c));
    if (const s32* attr = BbValue<s32>(node, 0x40)) o.Printf(",\"attr\":%d", *attr);
    PutBbString(o, node, 0x50, "fork");
    PutBbString(o, node, 0x58, "genre");
    PutBbString(o, node, 0x60, "pattern");
    Emit(body.p, o.n);
    return ret;
}

// Per-line label of that conversation (called by TroubleFriendUgcTalkSelector as it advances).
struct StrBuf {
    char* p;
    s32 cap;
};
u64 (*g_origFriendUgcLineLabel)(void*, StrBuf*, u64, s32, u32, u32) = nullptr;
u64 HookFriendUgcLineLabel(void* self, StrBuf* buf, u64 genre, s32 outcome, u32 pattern, u32 line) {
    u64 ret = g_origFriendUgcLineLabel(self, buf, genre, outcome, pattern, line);
    char body[300];
    Out o{body, sizeof(body)};
    o.Printf("\"t\":\"friend_line\",\"genre_id\":%u,\"outcome_id\":%d,\"pattern\":%u,\"line\":%u", (u32)genre,
             outcome, pattern, line);
    if (buf && buf->p && buf->cap > 0) {
        o.Puts(",\"label\":\"");
        o.JsonAscii(buf->p, buf->cap < 96 ? buf->cap : 96);
        o.Put('"');
    }
    Emit(body, o.n);
    return ret;
}

bool SigMatches(u64 off, const u32 (&sig)[4]) {
    const u32* p = (const u32*)(g_base + off);
    for (int i = 0; i < 4; i++)
        if (p[i] != sig[i]) return false;
    return true;
}

// ---------------------------------------------------------------------------------------------
// Worker thread: connect to the bridge, drain the ring.

s32 g_sock = -1;
u64 g_nextConnectMs = 0;

const char* LangCode() {
    if (!SigMatches(kOffGetRegionLanguage, kSigGetRegionLanguage)) return "?";
    auto getLang = (u32 (*)())(g_base + kOffGetRegionLanguage);
    u32 idx = getLang();
    const char* const* table = (const char* const*)(g_base + kOffLangCodeTable);
    return idx < kLangCodeCount ? table[idx] : "?";
}

// ---------------------------------------------------------------------------------------------
// Transport to the bridge. Both carry the same newline-delimited JSON (docs/how-it-works.md).
// - file (default): append to sd:/tomodachi-llm/out.jsonl; read the bridge's sd:/tomodachi-llm/in.jsonl as it grows. On the
//   emulators, guest socket calls run one at a time (~1 s each on Ryujinx 1.3.351); file I/O doesn't.
// - socket: TCP to host:port from the config.

bool g_connected = false;
constexpr size_t kRxBytes = 16 * 1024;
char g_rx[kRxBytes];  // bytes received from the bridge, not yet split into lines
size_t g_rxLen = 0;
u32 g_wouldBlock = 0;
s32 g_lastErrno = 0;
constexpr const char* kSdMount = "llmsd";
constexpr const char* kOutPath = "llmsd:/tomodachi-llm/out.jsonl";
constexpr const char* kInPath = "llmsd:/tomodachi-llm/in.jsonl";
bool g_sdMounted = false;
nn::fs::FileHandle g_outFile = {};
s64 g_outOff = 0;
nn::fs::FileHandle g_inFile = {};
bool g_inOpen = false;
s64 g_inOff = 0;
u64 g_nextInOpenMs = 0;

bool SocketConnect() {
    s32 s = nn::socket::Socket(AF_INET, SOCK_STREAM, 0);
    if (s < 0) {
        Logf("[tomodachi-llm] socket() failed: %d", s);
        return false;
    }
    struct sockaddr_in addr = {};
    addr.sin_len = sizeof(addr);
    addr.sin_family = AF_INET;
    addr.sin_port = nn::socket::InetHtons(g_cfg.port);
    nn::socket::InetAton(g_cfg.host, (nn::socket::InAddr*)&addr.sin_addr);
    if (nn::socket::Connect(s, (const sockaddr*)&addr, sizeof(addr)) != 0) {
        nn::socket::Close(s);
        return false;
    }
    s32 one = 1;  // Nagle off so small messages go out at once
    nn::socket::SetSockOpt(s, 6 /* IPPROTO_TCP */, 1 /* TCP_NODELAY */, &one, sizeof(one));
    g_sock = s;
    Logf("[tomodachi-llm] connected to %s:%u", g_cfg.host, g_cfg.port);
    return true;
}

// A new out.jsonl per game start tells the bridge a new session began; the old in.jsonl is removed so
// stale replies are never read.
bool FileConnect() {
    if (!g_sdMounted) {
        Result rc = nn::fs::MountSdCard(kSdMount);
        if (R_FAILED(rc)) {
            Logf("[tomodachi-llm] MountSdCard failed: 0x%x", rc);
            return false;
        }
        g_sdMounted = true;
    }
    nn::fs::CreateDirectory("llmsd:/tomodachi-llm");  // fails harmlessly if it exists
    nn::fs::DeleteFile(kInPath);
    nn::fs::DeleteFile(kOutPath);
    Result rc = nn::fs::CreateFile(kOutPath, 0);
    if (R_SUCCEEDED(rc)) rc = nn::fs::OpenFile(&g_outFile, kOutPath, nn::fs::OpenMode_Write | nn::fs::OpenMode_Append);
    if (R_FAILED(rc)) {
        Logf("[tomodachi-llm] can't create %s: 0x%x", kOutPath, rc);
        return false;
    }
    g_outOff = 0;
    g_inOpen = false;
    g_inOff = 0;
    Logf("[tomodachi-llm] file transport: writing %s, reading %s", kOutPath, kInPath);
    return true;
}

// Bytes written (all of them for files), or -1 when the connection is gone.
s64 TxSend(const void* p, u64 n) {
    if (!g_cfg.fileTransport) {
        s64 w = (s64)nn::socket::Send(g_sock, p, n, 0);
        if (w > 0) return w;
        g_lastErrno = nn::socket::GetLastErrno();
        if (g_lastErrno == 11 /* EAGAIN */) {
            g_wouldBlock++;
            return 0;
        }
        return -1;
    }
    nn::fs::WriteOption opt = nn::fs::WriteOption::CreateOption(nn::fs::WriteOptionFlag_Flush);
    Result rc = nn::fs::WriteFile(g_outFile, g_outOff, p, n, opt);
    if (R_FAILED(rc)) {
        g_lastErrno = (s32)rc;
        return -1;
    }
    g_outOff += n;
    return (s64)n;
}

// Bytes read, 0 when nothing is waiting, -1 when the connection is gone.
s64 TxRecv(void* buf, u64 cap) {
    if (!g_cfg.fileTransport) {
        s64 r = nn::socket::Recv(g_sock, buf, cap, 0x80 /* MSG_DONTWAIT */);
        if (r > 0) return r;
        if (r == 0) return -1;  // peer closed
        s32 e = nn::socket::GetLastErrno();
        return (e == 11 /* EAGAIN */ || e == 4 /* EINTR */) ? 0 : -1;
    }
    if (!g_inOpen) {
        if (NowMs() < g_nextInOpenMs) return 0;
        g_nextInOpenMs = NowMs() + 200;  // the bridge creates in.jsonl when it sees our out.jsonl
        if (R_FAILED(nn::fs::OpenFile(&g_inFile, kInPath, nn::fs::OpenMode_Read))) return 0;
        g_inOpen = true;
        g_inOff = 0;
    }
    s64 size = 0;
    if (R_FAILED(nn::fs::GetFileSize(&size, g_inFile))) return 0;
    if (size < g_inOff) {  // the bridge restarted the file
        g_inOff = 0;
        g_rxLen = 0;
    }
    if (size == g_inOff) return 0;
    u64 want = (u64)(size - g_inOff) < cap ? (u64)(size - g_inOff) : cap;
    u64 got = 0;
    if (R_FAILED(nn::fs::ReadFile(&got, g_inFile, g_inOff, buf, want))) return 0;
    g_inOff += got;
    return (s64)got;
}

void TxClose() {
    if (!g_cfg.fileTransport) {
        nn::socket::Close(g_sock);
        g_sock = -1;
    } else {
        nn::fs::CloseFile(g_outFile);
        if (g_inOpen) nn::fs::CloseFile(g_inFile);
        g_inOpen = false;
    }
    g_connected = false;
}

void TxConnect() {
    g_connected = g_cfg.fileTransport ? FileConnect() : SocketConnect();
    if (!g_connected) return;
    g_rxLen = 0;
    char hello[256];
    Out o{hello, sizeof(hello)};
    o.Printf("{\"ms\":%llu,\"t\":\"hello\",\"ver\":\"%s\",\"lang\":\"%s\",\"base\":\"0x%llx\",\"transport\":\"%s\","
             "\"relines_max\":%u,\"relines_bytes\":%u}\n",
             (unsigned long long)NowMs(), kVersion, LangCode(), (unsigned long long)g_base,
             g_cfg.fileTransport ? "file" : "socket", g_relines.Capacity(), g_relines.Bytes());
    TxSend(hello, o.n);
}

// Counters for the periodic "stats" event (diagnoses a slow or starved worker).
u32 g_sentEvents = 0;
u32 g_sendCalls = 0;
u64 g_sendMsTotal = 0;
u64 g_sendMsMax = 0;
u32 g_loops = 0;
// Ryujinx 1.3.351: Send, Write and SendTo all take ~0.9 s per call (0.1.3), so only Send is used.
// Receive side (0.1.4): time Poll and Recv, and measure a ping round trip every 5 s.
u32 g_recvCalls = 0;
u64 g_recvMs = 0;
u64 g_recvMsMax = 0;
u64 g_recvBytes = 0;
u32 g_rxMsgs = 0;
u32 g_pingId = 0;
u64 g_pingStartMs = 0;  // when the ping's Send started; 0 = none outstanding (set before the Send)
u64 g_pingSendMs = 0;   // how long that Send took
u64 g_pingSentMs = 0;   // when that Send returned; 0 while it is still in progress
u32 g_earlyPongs = 0;   // pongs that arrived before the ping's own Send returned (always 0 with one thread)
u32 g_pongs = 0;
u32 g_pingsLost = 0;  // pings never answered (e.g. sent while the bridge was stopped)
constexpr u64 kPingTimeoutMs = 15000;  // after this, a ping counts as lost and the next one may go out
u64 g_rttLast = 0;
u64 g_rttMax = 0;
u64 g_rxLatLast = 0;    // round trip minus the ping's own Send time
u64 g_rxLatMax = 0;

constexpr size_t kBatchBytes = 32 * 1024;
char g_batch[kBatchBytes];
size_t g_pendLen = 0;  // bytes in g_batch waiting to be sent
size_t g_pendOff = 0;
u32 g_pendEvents = 0;


void CloseSocket(const char* why, s64 rc, s32 e) {
    Logf("[tomodachi-llm] connection lost (%s %lld, errno/rc 0x%x)", why, (long long)rc, e);
    TxClose();
    __atomic_add_fetch(&g_dropped, g_pendEvents, __ATOMIC_RELAXED);
    g_pendLen = g_pendOff = 0;
    g_pendEvents = 0;
    g_pingStartMs = 0;
    g_nextConnectMs = NowMs() + 2000;
}

// Finds "key": in a JSON line and returns a pointer just past the colon (no full parser needed for the
// bridge's flat messages).
const char* JsonField(const char* line, const char* key) {
    char pat[32];
    snprintf(pat, sizeof(pat), "\"%s\":", key);
    const char* p = strstr(line, pat);
    return p ? p + strlen(pat) : nullptr;
}

void HandleRx(char* line, u64 now) {
    g_rxMsgs++;
    const char* t = JsonField(line, "t");
    if (!t) return;
    if (!strncmp(t, "\"pong\"", 6)) {
        const char* idp = JsonField(line, "id");
        u32 id = 0;
        for (const char* c = idp; c && *c >= '0' && *c <= '9'; c++) id = id * 10 + (*c - '0');
        if (g_pingStartMs && id == g_pingId) {
            g_pongs++;
            if (!__atomic_load_n(&g_pingSentMs, __ATOMIC_ACQUIRE)) g_earlyPongs++;
            g_rttLast = now - g_pingStartMs;
            if (g_rttLast > g_rttMax) g_rttMax = g_rttLast;
            u64 sendMs = __atomic_load_n(&g_pingSendMs, __ATOMIC_ACQUIRE);
            g_rxLatLast = g_rttLast > sendMs ? g_rttLast - sendMs : 0;
            if (g_rxLatLast > g_rxLatMax) g_rxLatMax = g_rxLatLast;
            g_pingStartMs = 0;
        }
    } else if (!strncmp(t, "\"reline\"", 8)) {
        const char* kp = JsonField(line, "key");
        const char* tp = JsonField(line, "text");
        if (!kp || !tp || *kp != '"' || *tp != '"') return;
        u64 key = 0;
        for (const char* c = kp + 1; *c && *c != '"'; c++) {
            u32 d = (*c >= '0' && *c <= '9') ? *c - '0' : (*c >= 'a' && *c <= 'f') ? *c - 'a' + 10 : 99;
            if (d > 15) return;
            key = (key << 4) | d;
        }
        char text[kRelineBytes];
        size_t n = JsonUnescape(tp + 1, text, sizeof(text));
        if (key && n) g_relines.Store(key, text, n);
    } else if (!strncmp(t, "\"welcome\"", 9)) {
        Logf("[tomodachi-llm] bridge welcome: %.200s", line);
        char body[300];
        Out o{body, sizeof(body)};
        o.Puts("\"t\":\"rx\",\"what\":\"welcome\"");
        Emit(body, o.n);
    }
}

// One non-blocking read of whatever the bridge has sent, split into lines. Called from the
// worker loop between sends.
void ReceiveOnce() {
    if (g_rxLen >= kRxBytes - 1) g_rxLen = 0;  // a line longer than the buffer: drop it
    u64 t0 = NowMs();
    s64 r = TxRecv(g_rx + g_rxLen, kRxBytes - 1 - g_rxLen);
    u64 now = NowMs();
    u64 dt = now - t0;
    g_recvCalls++;
    g_recvMs += dt;
    if (dt > g_recvMsMax) g_recvMsMax = dt;
    if (r < 0) {
        CloseSocket("recv", r, g_lastErrno);
        return;
    }
    if (r == 0) return;
    g_recvBytes += r;
    g_rxLen += r;
    g_rx[g_rxLen] = 0;
    size_t start = 0;
    for (size_t i = 0; i < g_rxLen; i++)
        if (g_rx[i] == '\n') {
            g_rx[i] = 0;
            HandleRx(g_rx + start, now);
            start = i + 1;
        }
    memmove(g_rx, g_rx + start, g_rxLen - start);
    g_rxLen -= start;
}

// Sends a ping on its own (between batches, so it never splits a line). The round-trip clock starts before
// the Send, because the reply may arrive while Send is still returning.
void SendPing() {
    char buf[96];
    Out o{buf, sizeof(buf)};
    u32 id = g_pingId + 1;
    o.Printf("{\"ms\":%llu,\"t\":\"ping\",\"id\":%u}\n", (unsigned long long)NowMs(), id);
    u64 t0 = NowMs();
    __atomic_store_n(&g_pingSentMs, 0, __ATOMIC_RELEASE);
    g_pingStartMs = t0;
    __atomic_store_n(&g_pingId, id, __ATOMIC_RELEASE);
    s64 w = TxSend(buf, o.n);
    u64 t1 = NowMs();
    __atomic_store_n(&g_pingSendMs, t1 - t0, __ATOMIC_RELEASE);
    __atomic_store_n(&g_pingSentMs, t1, __ATOMIC_RELEASE);
    if (w != (s64)o.n) g_pingStartMs = 0;
}

void Worker(void*) {
    u64 nextStatsMs = NowMs() + 5000;
    for (;;) {
        g_loops++;
        bool wantNet = g_cfg.fileTransport || (g_cfg.port && g_cfg.host[0]);
        if (wantNet && !g_connected && NowMs() >= g_nextConnectMs) {
            TxConnect();
            if (!g_connected) g_nextConnectMs = NowMs() + 5000;
        }

        // Refill the batch only when the previous one is fully sent. Without a connection, keep events
        // queued for the bridge unless the debug log consumes them.
        u32 drained = 0;
        bool progressed = false;
        bool canDrain = g_connected || !wantNet || g_cfg.debugLog;
        if (g_pendLen == 0 && canDrain) {
            size_t used = 0;
            for (;;) {
                const char* data;
                u32 len;
                if (!g_ring.Peek(&data, &len)) break;
                if (used + len > kBatchBytes) break;
                memcpy(g_batch + used, data, len);
                used += len;
                g_ring.Pop();
                __atomic_add_fetch(&g_popped, 1, __ATOMIC_RELEASE);
                drained++;
            }
            if (used > 0 && g_cfg.debugLog) {
                for (size_t i = 0, start = 0; i < used; i++)
                    if (g_batch[i] == '\n') {
                        DebugOut(g_batch + start, i - start);
                        start = i + 1;
                    }
            }
            if (g_connected) {
                g_pendLen = used;
                g_pendOff = 0;
                g_pendEvents = drained;
            }
            progressed = drained > 0;
        }
        if (g_connected && g_pendOff < g_pendLen) {
            u64 t0 = NowMs();
            s64 w = TxSend(g_batch + g_pendOff, g_pendLen - g_pendOff);
            u64 dt = NowMs() - t0;
            g_sendCalls++;
            g_sendMsTotal += dt;
            if (dt > g_sendMsMax) g_sendMsMax = dt;
            if (w > 0) {
                g_pendOff += w;
                progressed = true;
                if (g_pendOff >= g_pendLen) {
                    g_sentEvents += g_pendEvents;
                    g_pendLen = g_pendOff = 0;
                    g_pendEvents = 0;
                }
            } else if (w < 0) {
                CloseSocket("send", w, g_lastErrno);
            }
        }
        if (g_connected && g_cfg.rx) ReceiveOnce();
        if (g_connected || g_cfg.debugLog) PumpDumps(64);

        if (NowMs() >= nextStatsMs) {
            nextStatsMs = NowMs() + 5000;
            if (g_pingStartMs && NowMs() - g_pingStartMs > kPingTimeoutMs) {  // unanswered: don't wait forever
                g_pingStartMs = 0;
                g_pingsLost++;
            }
            if (g_connected && g_cfg.rx && g_pendLen == 0 && !g_pingStartMs) SendPing();
            char body[640];
            Out o{body, sizeof(body)};
            o.Printf("\"t\":\"stats\",\"emitted\":%u,\"deduped\":%u,\"dropped\":%u,\"sent\":%u,\"queued\":%u,"
                     "\"send_calls\":%u,\"send_ms_total\":%llu,\"send_ms_max\":%llu,\"loops\":%u,\"would_block\":%u,\"errno\":%d,",
                     __atomic_load_n(&g_emitted, __ATOMIC_RELAXED), __atomic_load_n(&g_deduped, __ATOMIC_RELAXED),
                     __atomic_load_n(&g_dropped, __ATOMIC_RELAXED), g_sentEvents,
                     __atomic_load_n(&g_pushed, __ATOMIC_ACQUIRE) - g_popped, g_sendCalls,
                     (unsigned long long)g_sendMsTotal, (unsigned long long)g_sendMsMax, g_loops, g_wouldBlock, g_lastErrno);
            o.Printf("\"recv\":[%u,%llu,%llu],\"recv_bytes\":%llu,\"rx_msgs\":%u,"
                     "\"relines\":[%u,%u,%u,%u,%u],\"dumps\":[%u,%u,%u],\"scratch_miss\":%u,\"pings\":%u,\"pongs\":%u,\"pings_lost\":%u,\"early_pongs\":%u,\"rtt_ms\":[%llu,%llu],\"rx_lat_ms\":[%llu,%llu]",
                     g_recvCalls,
                     (unsigned long long)g_recvMs, (unsigned long long)g_recvMsMax, (unsigned long long)g_recvBytes,
                     g_rxMsgs, g_relines.stored, g_relinesUsed, g_relinesBad, g_relines.evicted, g_relines.Laps(),
                     __atomic_load_n(&g_dumpsN, __ATOMIC_ACQUIRE), g_dumpNext, g_dumpSkipped, __atomic_load_n(&g_scratchMiss, __ATOMIC_RELAXED), g_pingId, g_pongs, g_pingsLost, g_earlyPongs, (unsigned long long)g_rttLast, (unsigned long long)g_rttMax,
                     (unsigned long long)g_rxLatLast, (unsigned long long)g_rxLatMax);
            Emit(body, o.n);
        }
        if (!progressed) nn::os::SleepThread(nn::TimeSpan::FromNanoSeconds(20ull * 1000 * 1000));
    }
}

alignas(0x1000) u8 g_workerStack[0x8000];
alignas(16) u8 g_workerThread[sizeof(nn::os::ThreadType)];

}  // namespace

// Skyline looks up the exported symbol "main" and calls it as void(); the return value is ignored.
extern "C" int main() {
    g_base = (u64)getRegionAddress(0);
    Logf("[tomodachi-llm] plugin %s loaded; main base 0x%llx", kVersion, (unsigned long long)g_base);
    LoadConfig();

    bool okLabels = SigMatches(kOffGetTextByLabel, kSigGetTextByLabel);
    bool okExpand = SigMatches(kOffTextExpand, kSigTextExpand);
    if (!okLabels || !okExpand) {
        Logf("[tomodachi-llm] game code does not match 1.0.4 (labels=%d expand=%d); no hooks installed", okLabels, okExpand);
    } else {
        if (g_cfg.hookLabels)
            A64HookFunction((void*)(g_base + kOffGetTextByLabel), (void*)HookGetText, (void**)&g_origGetText);
        if (g_cfg.hookLines)
            A64HookFunction((void*)(g_base + kOffTextExpand), (void*)HookTextExpand, (void**)&g_origTextExpand);
        bool okIndex = g_cfg.hookIndex && g_cfg.scripts[0][0] && SigMatches(kOffGetIndexByLabel, kSigGetIndexByLabel);
        if (okIndex)
            A64HookFunction((void*)(g_base + kOffGetIndexByLabel), (void*)HookGetIndex, (void**)&g_origGetIndex);
        Logf("[tomodachi-llm] index hook %d", okIndex);
        bool okChat = g_cfg.hookChat && SigMatches(kOffScenarioChatCalc, kSigScenarioChatCalc) &&
                      SigMatches(kOffScenarioChatStep, kSigScenarioChatStep) &&
                      SigMatches(kOffFocusTelopEnter, kSigFocusTelopEnter);
        if (okChat) {
            A64HookFunction((void*)(g_base + kOffScenarioChatCalc), (void*)HookChatCalc, (void**)&g_origChatCalc);
            A64HookFunction((void*)(g_base + kOffScenarioChatStep), (void*)HookChatStep, (void**)&g_origChatStep);
            A64HookFunction((void*)(g_base + kOffFocusTelopEnter), (void*)HookTelopEnter, (void**)&g_origTelopEnter);
        }
        bool okFriend = g_cfg.hookChat && SigMatches(kOffFriendUgcPick, kSigFriendUgcPick) &&
                        SigMatches(kOffFriendUgcLineLabel, kSigFriendUgcLineLabel);
        if (okFriend) {
            A64HookFunction((void*)(g_base + kOffFriendUgcPick), (void*)HookFriendUgcPick, (void**)&g_origFriendUgcPick);
            A64HookFunction((void*)(g_base + kOffFriendUgcLineLabel), (void*)HookFriendUgcLineLabel,
                            (void**)&g_origFriendUgcLineLabel);
        }
        Logf("[tomodachi-llm] hooks installed (labels=%d lines=%d chat=%d friend=%d)", g_cfg.hookLabels, g_cfg.hookLines, okChat,
             okFriend);
    }

    auto* thread = reinterpret_cast<nn::os::ThreadType*>(g_workerThread);
    Result rc = nn::os::CreateThread(thread, Worker, nullptr, g_workerStack, sizeof(g_workerStack), 16, 0);  // same as Skyline's logger thread
    if (R_FAILED(rc)) {
        Logf("[tomodachi-llm] CreateThread failed: 0x%x", rc);
        return 0;
    }
    nn::os::SetThreadNamePointer(thread, "tomodachi_llm::Worker");
    nn::os::StartThread(thread);

    return 0;
}
