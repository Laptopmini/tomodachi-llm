// The plugin's store of generated lines (relines), shared by the plugin and the host test
// (hook/tests/host_test.cpp). The includer provides u8/u16/u32/u64.
//
// One writer (the worker thread) and many readers (Text_Expand on game threads), no locks: each entry has a
// sequence number that is odd while it's being written, so a reader never uses a half-written entry.
// Open addressing: a key lives in one of kProbe slots starting at key % N. When all of them hold other keys, the
// new line replaces one of them (picked from the key's high bits), so a full neighbourhood loses one line, not the
// newest.
// Texts live in a circular arena (lines average ~50-100 bytes, so fixed slots would waste most of their room).
// Positions count bytes and wrap at 2^32, which ArenaBytes (a power of two) divides; a text never wraps. When the
// arena comes round again it overwrites the oldest texts: their entries then read as missing (vanilla line). The
// writer publishes how far it will write before writing, and a reader checks after copying that its text wasn't
// reached, so a reader never uses overwritten text.
// The bridge sends at most `Capacity()` lines, and `ArenaBytes` of text, at session start (hello reports both).
#pragma once

#include <stddef.h>
#include <string.h>

namespace tomodachi_llm {

template <u32 N, u32 ArenaBytes, u32 MaxLen>
struct RelineStore {
    static_assert((ArenaBytes & (ArenaBytes - 1)) == 0, "ArenaBytes must be a power of two");
    static_assert(MaxLen <= ArenaBytes, "a text must fit the arena");
    static constexpr u32 kProbe = 8;
    struct Entry {
        u32 seq;
        u16 len;
        u32 pos;  // text in the arena; UTF-8, "[k]" = the k-th tag run of the original template
        u64 key;  // 0 = empty
    };
    Entry e[N];
    u8 arena[ArenaBytes];
    u32 end = 0;  // arena bytes handed out so far (writer; readers load it)
    u32 stored = 0;
    u32 evicted = 0;

    static constexpr u32 Capacity() { return N / 2; }
    static constexpr u32 Bytes() { return ArenaBytes; }
    // How many times the arena came round (old texts overwritten).
    u32 Laps() const { return __atomic_load_n(&end, __ATOMIC_ACQUIRE) / ArenaBytes; }

    // Worker thread only.
    bool Store(u64 key, const char* text, size_t len) {
        if (!key || !len || len >= MaxLen) return false;
        Entry* hit = nullptr;
        Entry* empty = nullptr;
        for (u32 i = 0; i < kProbe && !hit; i++) {
            Entry* x = &e[(key + i) % N];
            u64 k = __atomic_load_n(&x->key, __ATOMIC_ACQUIRE);
            if (k == key) hit = x;
            else if (!k && !empty) empty = x;
        }
        Entry* x = hit ? hit : empty;
        if (!x) {
            x = &e[(key + (key >> 40) % kProbe) % N];
            evicted++;
        }
        u32 pos = end;
        if (ArenaBytes - pos % ArenaBytes < len) pos += ArenaBytes - pos % ArenaBytes;  // no wrapping texts
        __atomic_add_fetch(&x->seq, 1, __ATOMIC_ACQ_REL);  // odd: writing
        __atomic_store_n(&x->key, 0, __ATOMIC_RELEASE);
        __atomic_store_n(&end, pos + (u32)len, __ATOMIC_RELEASE);
        __atomic_thread_fence(__ATOMIC_SEQ_CST);  // `end` is visible before the bytes it covers change
        memcpy(&arena[pos % ArenaBytes], text, len);
        x->pos = pos;
        x->len = (u16)len;
        __atomic_store_n(&x->key, key, __ATOMIC_RELEASE);
        __atomic_add_fetch(&x->seq, 1, __ATOMIC_ACQ_REL);  // even: done
        stored++;
        return true;
    }

    // Any thread. `out` holds at least MaxLen.
    bool Lookup(u64 key, char* out, u16* outLen) const {
        if (!key) return false;
        for (u32 i = 0; i < kProbe; i++) {
            const Entry* x = &e[(key + i) % N];
            u32 s1 = __atomic_load_n(&x->seq, __ATOMIC_ACQUIRE);
            if ((s1 & 1) || __atomic_load_n(&x->key, __ATOMIC_ACQUIRE) != key) continue;
            u16 n = x->len;
            u32 pos = x->pos;
            if (!n || n >= MaxLen || pos % ArenaBytes + n > ArenaBytes) return false;
            if (__atomic_load_n(&end, __ATOMIC_ACQUIRE) - pos > ArenaBytes) return false;  // already overwritten
            memcpy(out, &arena[pos % ArenaBytes], n);
            __atomic_thread_fence(__ATOMIC_ACQUIRE);
            if (__atomic_load_n(&end, __ATOMIC_ACQUIRE) - pos > ArenaBytes) return false;  // overwritten while read
            if (__atomic_load_n(&x->seq, __ATOMIC_ACQUIRE) != s1) return false;  // rewritten while read: vanilla
            *outLen = n;
            return true;
        }
        return false;
    }
};

}  // namespace tomodachi_llm
