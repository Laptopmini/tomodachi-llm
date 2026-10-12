// The plugin's event queue, shared by the plugin and the host test (hook/tests/host_test.cpp). The includer provides
// u8/u32.
//
// A byte ring of variable-length records (u32 length, data, padding to 4), so an event takes the room it needs
// rather than a fixed slot (events average ~180 bytes, the largest ~1.5 KB). Producers are serialized by the caller
// (a try-lock); one consumer (the worker). Positions count bytes and wrap at 2^32, which Bytes (a power of two)
// divides. A record never wraps: when it doesn't fit before the end, the producer writes a wrap mark there and
// starts again at 0.
#pragma once

#include <stddef.h>
#include <string.h>

namespace tomodachi_llm {

template <u32 Bytes, u32 MaxRecord>
struct ByteRing {
    static_assert((Bytes & (Bytes - 1)) == 0, "Bytes must be a power of two");
    static constexpr u32 kWrap = 0xffffffffu;
    alignas(8) u8 buf[Bytes];
    u32 head = 0;  // producers
    u32 tail = 0;  // consumer
    u32 pad = 0;   // producer: bytes skipped by the pending Reserve

    static constexpr u32 Align(u32 n) { return (n + 3) & ~3u; }

    // Producer: room for a record of up to MaxRecord bytes, or nullptr when the ring is full. Commit() publishes it.
    char* Reserve() {
        u32 off = head % Bytes;
        u32 need = 4 + Align(MaxRecord);
        pad = Bytes - off < need ? Bytes - off : 0;
        if (head - __atomic_load_n(&tail, __ATOMIC_ACQUIRE) + pad + need > Bytes) return nullptr;
        return (char*)&buf[(pad ? 0 : off) + 4];
    }
    void Commit(u32 len) {
        u32 h = head;
        if (pad) {
            *(u32*)&buf[h % Bytes] = kWrap;
            h += pad;
        }
        *(u32*)&buf[h % Bytes] = len;
        __atomic_store_n(&head, h + 4 + Align(len), __ATOMIC_RELEASE);
    }

    // Consumer: the oldest record, if any. Pop() removes it.
    bool Peek(const char** data, u32* len) {
        for (;;) {
            u32 t = tail;
            if (t == __atomic_load_n(&head, __ATOMIC_ACQUIRE)) return false;
            u32 off = t % Bytes;
            u32 n = *(const u32*)&buf[off];
            if (n == kWrap) {
                __atomic_store_n(&tail, t + (Bytes - off), __ATOMIC_RELEASE);
                continue;
            }
            *data = (const char*)&buf[off + 4];
            *len = n;
            return true;
        }
    }
    void Pop() {
        u32 t = tail;
        u32 n = *(const u32*)&buf[t % Bytes];
        __atomic_store_n(&tail, t + 4 + Align(n), __ATOMIC_RELEASE);
    }

    u32 Used() const { return __atomic_load_n(&head, __ATOMIC_ACQUIRE) - __atomic_load_n(&tail, __ATOMIC_ACQUIRE); }
    u32 Free() const { return Bytes - Used(); }
};

}  // namespace tomodachi_llm
