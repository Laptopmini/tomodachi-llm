// Walks the entries of a loaded MSBT (no game or SDK calls), shared by the plugin and the host test
// (hook/tests/host_test.cpp). The includer provides u8/u16/u32.
//
// Layout (standard MSBT, as Msbt_GetTextByLabel 0x71005abc00 reads it, re/offsets.md):
//   LBL1 data: u32 slot count, then per slot {u32 label count, u32 offset}; at each offset, per label:
//              u8 length, the label's bytes (no terminator), u32 message index.
//   TXT2 data: u32 message count, then u32 offsets (from the start of the section data); message i runs from
//              its offset to the next one (the last one to the end of the section).
// Every read is bounds-checked against the section sizes, so a corrupt or unexpected section ends the walk.
#pragma once

#include <stddef.h>
#include <string.h>

namespace tomodachi_llm {

inline u32 Rd32(const u8* p) {
    u32 v;
    memcpy(&v, p, 4);
    return v;
}

// fn(const char* label, u32 labelLen, const u16* text, u32 textUnits) for every label; text runs to the end of
// the message (terminator included). Returns the number of labels visited.
template <typename Fn>
u32 ForEachMessage(const u8* lbl, u32 lblSize, const u8* txt, u32 txtSize, Fn fn) {
    if (!lbl || !txt || lblSize < 4 || txtSize < 4) return 0;
    u32 slots = Rd32(lbl);
    u32 msgs = Rd32(txt);
    if (slots > 0x10000 || 4ull + 8ull * slots > lblSize || msgs > 0x10000 || 4ull + 4ull * msgs > txtSize) return 0;
    u32 visited = 0;
    for (u32 s = 0; s < slots; s++) {
        u32 count = Rd32(lbl + 4 + 8 * s);
        u32 p = Rd32(lbl + 8 + 8 * s);
        for (u32 c = 0; c < count; c++) {
            if (p >= lblSize) break;
            u32 len = lbl[p];
            if ((u64)p + 1 + len + 4 > lblSize) break;
            const char* name = (const char*)lbl + p + 1;
            u32 idx = Rd32(lbl + p + 1 + len);
            p += len + 5;
            if (idx >= msgs) continue;
            u32 o0 = Rd32(txt + 4 + 4 * idx);
            u32 o1 = idx + 1 < msgs ? Rd32(txt + 8 + 4 * idx) : txtSize;
            if (o0 >= o1 || o1 > txtSize || (o0 & 1)) continue;
            visited++;
            fn(name, len, (const u16*)(txt + o0), (o1 - o0) / 2);
        }
    }
    return visited;
}

// Dump records (the plugin's copy of a script's messages): u8 label length, the label, pad to 2, u32 units,
// units * u16. Appends one at `*at` if it fits in `cap`; returns false otherwise (nothing written).
inline bool PutRecord(u8* arena, u32 cap, u32* at, const char* name, u32 len, const u16* text, u32 units) {
    if (len > 255) return false;
    u32 p = *at;
    u64 need = 1ull + len + 1 + 4 + 2ull * units;
    if (p + need > cap) return false;
    arena[p++] = (u8)len;
    memcpy(arena + p, name, len);
    p += len;
    p += p & 1;
    memcpy(arena + p, &units, 4);
    p += 4;
    memcpy(arena + p, text, units * 2);
    *at = p + units * 2;
    return true;
}

// Reads the record at `*at` (written by PutRecord) and moves past it.
inline void GetRecord(const u8* arena, u32* at, const char** name, u32* len, const u16** text, u32* units) {
    u32 p = *at;
    *len = arena[p++];
    *name = (const char*)arena + p;
    p += *len;
    p += p & 1;
    memcpy(units, arena + p, 4);
    p += 4;
    *text = (const u16*)(arena + p);
    *at = p + *units * 2;
}

}  // namespace tomodachi_llm
