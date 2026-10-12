// Pure helpers for relines (no game or SDK calls), shared by the plugin and the host test
// (hook/tests/host_test.cpp). The includer provides u8/u16/u32/u64.
#pragma once

#include <stddef.h>
#include <string.h>

namespace tomodachi_llm {

// Length of a raw template up to its terminating NUL, skipping over tags (their params may contain zeros).
// Used for the template key, so a template read by label and the same template passed to Text_Expand (with or
// without its terminator counted) hash the same.
inline u32 TemplateLen(const u16* s, u32 max) {
    u32 i = 0;
    while (i < max && s[i]) {
        if (s[i] == 0x0E) {
            if (i + 3 >= max) return max;
            i += 4 + s[i + 3] / 2;
        } else if (s[i] == 0x0F) {
            i += 3;
        } else {
            i++;
        }
    }
    return i < max ? i : max;
}

// Tag runs of a raw template: maximal sequences of tags (0x0E group type size params…, or 0x0F group type),
// except that the catchphrase tags {200.8} and {200.9} always form a run of their own (same rule as the
// bridge's relines.split_template).
constexpr u32 kMaxRuns = 48;
struct Runs {
    u32 n = 0;
    u32 start[kMaxRuns];
    u32 len[kMaxRuns];
};

inline bool FindRuns(const u16* s, u32 n, Runs& r) {
    bool prevTag = false, prevSolo = false;
    for (u32 i = 0; i < n;) {
        u32 tl;
        if (s[i] == 0x0E) {
            if (i + 3 >= n) return false;
            tl = 4 + s[i + 3] / 2;
        } else if (s[i] == 0x0F) {
            tl = 3;
        } else {
            prevTag = false;
            i++;
            continue;
        }
        if (i + tl > n) return false;
        bool solo = s[i] == 0x0E && s[i + 1] == 200 && (s[i + 2] == 8 || s[i + 2] == 9);
        if (prevTag && !solo && !prevSolo) {
            r.len[r.n - 1] += tl;
        } else {
            if (r.n >= kMaxRuns) return false;
            r.start[r.n] = i;
            r.len[r.n] = tl;
            r.n++;
        }
        prevTag = true;
        prevSolo = solo;
        i += tl;
    }
    return true;
}

// Builds the replacement template: UTF-8 text to UTF-16, "[k]" replaced by the original's k-th tag run.
// Returns the length, or 0 if anything doesn't fit or refers to a missing run.
inline u32 BuildReline(const u16* src, u32 srcLen, const char* t, u16 tlen, u16* out, u32 cap) {
    Runs runs;
    if (!FindRuns(src, srcLen, runs)) return 0;
    u32 n = 0;
    for (u16 i = 0; i < tlen;) {
        u8 c = (u8)t[i];
        if (c == '[') {
            u32 k = 0, j = i + 1;
            while (j < tlen && t[j] >= '0' && t[j] <= '9') k = k * 10 + (t[j++] - '0');
            if (j >= tlen || t[j] != ']' || k == 0 || k > runs.n) return 0;
            if (n + runs.len[k - 1] > cap) return 0;
            memcpy(out + n, src + runs.start[k - 1], runs.len[k - 1] * 2);
            n += runs.len[k - 1];
            i = j + 1;
            continue;
        }
        u32 cp;
        u32 extra;
        if (c < 0x80) {
            cp = c;
            extra = 0;
        } else if ((c & 0xE0) == 0xC0) {
            cp = c & 0x1F;
            extra = 1;
        } else if ((c & 0xF0) == 0xE0) {
            cp = c & 0x0F;
            extra = 2;
        } else {
            return 0;  // 4-byte UTF-8 (emoji etc.): the bridge never sends it
        }
        if (i + extra >= tlen + 0u && extra) return 0;
        for (u32 k = 1; k <= extra; k++) cp = (cp << 6) | ((u8)t[i + k] & 0x3F);
        if (cp < 0x20 || n + 1 > cap) return 0;  // no control characters
        out[n++] = (u16)cp;
        i += 1 + extra;
    }
    return n;
}

// Decodes a JSON string body (starting after the opening quote) to UTF-8. Returns the length, or 0 if it
// doesn't fit or isn't terminated. \uXXXX escapes outside the BMP aren't needed (the bridge rejects emoji).
inline size_t JsonUnescape(const char* p, char* out, size_t cap) {
    size_t n = 0;
    for (; *p && *p != '"'; p++) {
        u32 cp = (u8)*p;
        if (*p == '\\') {
            p++;
            switch (*p) {
                case 'n': cp = '\n'; break;
                case 't': cp = '\t'; break;
                case 'u': {
                    cp = 0;
                    for (int i = 1; i <= 4; i++) {
                        char c = p[i];
                        u32 d = (c >= '0' && c <= '9') ? c - '0' : (c >= 'a' && c <= 'f') ? c - 'a' + 10
                                : (c >= 'A' && c <= 'F') ? c - 'A' + 10 : 99;
                        if (d > 15) return 0;
                        cp = (cp << 4) | d;
                    }
                    p += 4;
                    if (cp < 0x80) break;
                    if (n + 3 >= cap) return 0;
                    if (cp < 0x800) {
                        out[n++] = 0xC0 | (cp >> 6);
                    } else {
                        out[n++] = 0xE0 | (cp >> 12);
                        out[n++] = 0x80 | ((cp >> 6) & 0x3F);
                    }
                    out[n++] = 0x80 | (cp & 0x3F);
                    continue;
                }
                case 0: return 0;
                default: cp = (u8)*p; break;  // \" \\ \/
            }
        }
        if (n + 1 >= cap) return 0;
        out[n++] = (char)cp;
    }
    return *p == '"' ? n : 0;
}

}  // namespace tomodachi_llm
