// Host-side unit test for the plugin's pure helpers (src/reline.h, relinestore.h, ring.h, msbt.h). Run with: make test
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <string>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef uint64_t u64;
#include "../src/reline.h"
#include "../src/relinestore.h"
#include "../src/ring.h"
#include "../src/msbt.h"
#include <vector>
using namespace tomodachi_llm;
static int fails = 0;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
int main() {
    // {200.8 + 2-byte param}I want to be friends with {201.3}{200.0 + param}{200.9}.
    std::u16string t;
    t += u"\x0e"; t += (char16_t)200; t += (char16_t)8; t += (char16_t)2; t += (char16_t)0x1234;
    t += u"I want to be friends with ";
    t += u"\x0e"; t += (char16_t)201; t += (char16_t)3; t += (char16_t)0;
    t += u"\x0e"; t += (char16_t)200; t += (char16_t)0; t += (char16_t)2; t += (char16_t)0x0001;
    t += u"\x0e"; t += (char16_t)200; t += (char16_t)9; t += (char16_t)0;
    t += u".";
    Runs r; CHECK(FindRuns((const u16*)t.data(), t.size(), r));
    CHECK(r.n == 3);  // {200.8} | {201.3}{200.0} | {200.9}
    CHECK(r.len[0] == 5 && r.len[1] == 9 && r.len[2] == 4);
    const char* gen = "[1]Mock: [2] rocks, caf\xc3\xa9 time[3]!";
    u16 out[256];
    u32 n = BuildReline((const u16*)t.data(), t.size(), gen, strlen(gen), out, 255);
    CHECK(n > 0);
    std::u16string want = t.substr(0, 5) + u"Mock: " + t.substr(r.start[1], 9) + u" rocks, caf\u00e9 time" + t.substr(r.start[2], 4) + u"!";
    CHECK(n == want.size() && memcmp(out, want.data(), n * 2) == 0);
    // bad references and control characters are refused
    CHECK(BuildReline((const u16*)t.data(), t.size(), "[4]x", 4, out, 255) == 0);
    CHECK(BuildReline((const u16*)t.data(), t.size(), "[x", 2, out, 255) == 0);
    CHECK(BuildReline((const u16*)t.data(), t.size(), "a\x07", 2, out, 255) == 0);
    CHECK(BuildReline((const u16*)t.data(), t.size(), "\xf0\x9f\x98\x80", 4, out, 255) == 0);
    CHECK(BuildReline((const u16*)t.data(), t.size(), "\xc3", 1, out, 255) == 0);  // truncated UTF-8
    CHECK(BuildReline((const u16*)t.data(), t.size(), "too long for the buffer", 23, out, 5) == 0);
    // close tags {/} count as tags in a run
    std::u16string c = u"Yeaaah"; c += u"\x0f"; c += (char16_t)6; c += (char16_t)10; c += u"!";
    Runs r2; CHECK(FindRuns((const u16*)c.data(), c.size(), r2) && r2.n == 1 && r2.len[0] == 3);
    // template length stops at the terminator, not at zeros inside tag params
    std::u16string z = t; z += (char16_t)0; z += u"junk";
    CHECK(TemplateLen((const u16*)z.data(), z.size()) == t.size());
    CHECK(TemplateLen((const u16*)t.data(), t.size()) == t.size());
    std::u16string zp = u"Hi "; zp += u"\x0e"; zp += (char16_t)200; zp += (char16_t)0; zp += (char16_t)2; zp += (char16_t)0;
    zp += u"!"; size_t zl = zp.size(); zp += (char16_t)0;
    CHECK(TemplateLen((const u16*)zp.data(), zp.size()) == zl);  // {200.0} with a zero param
    // JSON unescape
    char buf[64];
    size_t m = JsonUnescape("[1]Caf\\u00e9 \\\"ok\\\"\\\\ \\u4e2d\"rest", buf, sizeof buf);
    CHECK(m > 0 && std::string(buf, m) == "[1]Caf\xc3\xa9 \"ok\"\\ \xe4\xb8\xad");
    CHECK(JsonUnescape("unterminated", buf, sizeof buf) == 0);
    CHECK(JsonUnescape("\\u12g4\"", buf, sizeof buf) == 0);
    // MSBT walk: a synthetic LBL1/TXT2 pair (2 hash slots, 3 labels, 3 messages)
    {
        auto put32 = [](std::vector<u8>& v, u32 x) { for (int i = 0; i < 4; i++) v.push_back((u8)(x >> (8 * i))); };
        auto label = [&](std::vector<u8>& v, const char* s, u32 idx) { v.push_back((u8)strlen(s)); v.insert(v.end(), s, s + strlen(s)); put32(v, idx); };
        std::vector<u8> lbl; put32(lbl, 2);
        std::vector<u8> e0; label(e0, "A_00", 1); label(e0, "A_01", 0);
        std::vector<u8> e1; label(e1, "B", 2); label(e1, "Bad", 9);       // index 9 doesn't exist: skipped
        put32(lbl, 2); put32(lbl, 4 + 16);
        put32(lbl, 2); put32(lbl, 4 + 16 + (u32)e0.size());
        lbl.insert(lbl.end(), e0.begin(), e0.end()); lbl.insert(lbl.end(), e1.begin(), e1.end());
        std::u16string m0 = u"Hi"; m0 += (char16_t)0;
        std::u16string m1 = u"Yo!"; m1 += (char16_t)0;
        std::u16string m2 = u"Last"; m2 += (char16_t)0;
        std::vector<u8> txt; put32(txt, 3);
        u32 base = 4 + 12; put32(txt, base); put32(txt, base + 2 * m0.size()); put32(txt, base + 2 * (m0.size() + m1.size()));
        for (auto* m : {&m0, &m1, &m2}) txt.insert(txt.end(), (const u8*)m->data(), (const u8*)m->data() + 2 * m->size());
        std::string seen;
        u32 n = ForEachMessage(lbl.data(), lbl.size(), txt.data(), txt.size(), [&](const char* l, u32 ll, const u16* t, u32 tu) {
            seen += std::string(l, ll) + "=" + std::to_string(TemplateLen(t, tu)) + ";";
        });
        CHECK(n == 3 && seen == "A_00=3;A_01=2;B=4;");
        CHECK(ForEachMessage(lbl.data(), 10, txt.data(), txt.size(), [](const char*, u32, const u16*, u32) {}) == 0);
        CHECK(ForEachMessage(lbl.data(), lbl.size(), txt.data(), 8, [](const char*, u32, const u16*, u32) {}) == 0);
    }
    // Dump records: odd and even label lengths (padding), reading back in order, a record that doesn't fit
    {
        alignas(4) static u8 arena[64];
        u32 at = 0;
        const u16 t1[3] = {'H', 'i', 0}, t2[1] = {'X'};
        CHECK(PutRecord(arena, sizeof arena, &at, "Abc", 3, t1, 3) && PutRecord(arena, sizeof arena, &at, "De", 2, t2, 1));
        u32 end = at;
        CHECK(!PutRecord(arena, sizeof arena, &at, "Long", 4, t1, 30) && at == end);
        u32 p = 0, len, units; const char* name; const u16* text;
        GetRecord(arena, &p, &name, &len, &text, &units);
        CHECK(len == 3 && !memcmp(name, "Abc", 3) && units == 3 && text[0] == 'H' && ((uintptr_t)text & 1) == 0);
        GetRecord(arena, &p, &name, &len, &text, &units);
        CHECK(len == 2 && !memcmp(name, "De", 2) && units == 1 && text[0] == 'X' && p == end);
    }
    // Reline store: probing, replacing a key in place, eviction when a neighbourhood is full
    {
        static tomodachi_llm::RelineStore<16, 1024, 32> st;
        char got[32]; u16 gl = 0;
        CHECK(!st.Lookup(5, got, &gl));
        CHECK(st.Store(5, "five", 4) && st.Store(21, "twenty-one", 10));  // 21 % 16 == 5: probes to slot 6
        CHECK(st.Lookup(5, got, &gl) && gl == 4 && !memcmp(got, "five", 4));
        CHECK(st.Lookup(21, got, &gl) && gl == 10 && !memcmp(got, "twenty-one", 10));
        CHECK(st.Store(5, "V", 1) && st.Lookup(5, got, &gl) && gl == 1 && got[0] == 'V');
        CHECK(st.Lookup(21, got, &gl) && gl == 10);
        CHECK(!st.Store(0, "zero", 4) && !st.Store(9, "this text is far too long for 32 bytes", 39) && !st.Store(9, "", 0));
        for (u64 k = 37; k < 37 + 16 * 8; k += 16) st.Store(k, "k", 1);  // fills slots 5..12 and beyond
        CHECK(st.evicted > 0);
        CHECK(st.Lookup(37 + 16 * 7, got, &gl));  // the newest line is always kept
        CHECK(st.Capacity() == 8 && st.Bytes() == 1024 && st.Laps() == 0);
    }
    // Reline store arena: texts never wrap; once the arena comes round, overwritten texts read as missing
    {
        static tomodachi_llm::RelineStore<64, 64, 32> st;
        char got[32]; u16 gl = 0;
        CHECK(st.Store(1, "aaaaaaaaaaaaaaaaaaaa", 20) && st.Store(2, "bbbbbbbbbbbbbbbbbbbb", 20));  // 0..40
        CHECK(st.Store(3, "ccccccccccccccccccccccccc", 25));  // doesn't fit 40..64: starts at 64 (lap 1)
        CHECK(st.Laps() == 1 && st.end == 89);
        CHECK(!st.Lookup(1, got, &gl));  // 0..20 overwritten (64..84)
        CHECK(!st.Lookup(2, got, &gl));  // 20..40: 84..88 reaches it
        CHECK(st.Lookup(3, got, &gl) && gl == 25 && got[0] == 'c' && got[24] == 'c');
        CHECK(st.Store(4, "dd", 2) && st.Lookup(3, got, &gl) && st.Lookup(4, got, &gl) && got[0] == 'd');
        for (u32 i = 0; i < 40; i++) st.Store(100 + i, "0123456789abcdef", 16);  // many laps
        CHECK(st.Lookup(139, got, &gl) && gl == 16 && !memcmp(got, "0123456789abcdef", 16));
        CHECK(!st.Lookup(100, got, &gl) && !st.Lookup(3, got, &gl));
        u32 alive = 0;
        for (u32 i = 0; i < 40; i++) alive += st.Lookup(100 + i, got, &gl);
        CHECK(alive == 4);  // 64 bytes of 16-byte texts
    }
    // Event ring: records of any size, wrap marks, full ring
    {
        static tomodachi_llm::ByteRing<256, 40> r;
        const char* d; u32 l;
        CHECK(!r.Peek(&d, &l) && r.Free() == 256);
        u32 pushed = 0, popped = 0, nextPop = 0;
        char want[64];
        for (u32 round = 0; round < 50; round++) {
            for (u32 k = 0; k < 3; k++) {  // push 3 records of varying length (some don't fit: ring full)
                char* p = r.Reserve();
                if (!p) break;
                u32 len = 1 + (pushed * 7) % 40;
                for (u32 i = 0; i < len; i++) p[i] = (char)('a' + (pushed + i) % 26);
                r.Commit(len);
                pushed++;
            }
            for (u32 k = 0; k < 2 && r.Peek(&d, &l); k++) {  // pop 2
                u32 len = 1 + (nextPop * 7) % 40;
                for (u32 i = 0; i < len; i++) want[i] = (char)('a' + (nextPop + i) % 26);
                CHECK(l == len && !memcmp(d, want, len));
                r.Pop();
                nextPop++;
                popped++;
            }
        }
        while (r.Peek(&d, &l)) { r.Pop(); popped++; }
        CHECK(pushed == popped && pushed > 60 && r.Used() == 0 && r.head > 1024);  // wrapped several times
        u32 fit = 0;
        while (r.Reserve()) { r.Commit(40); fit++; }  // 44-byte records until full
        CHECK(fit >= 4 && fit <= 5 && r.Free() < 2 * 44);
    }
    printf(fails ? "%d FAILED\n" : "all host checks passed\n", fails);
    return fails != 0;
}
