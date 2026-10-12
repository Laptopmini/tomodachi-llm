"""Raw AArch64 scans over a converted main.elf (nx2elf output), for things Ghidra misses.

    python3 -I re/ghidra/a64scan.py <main.elf> adrp <addr>...     # ADRP+ADD pairs forming each address
    python3 -I re/ghidra/a64scan.py <main.elf> bl <addr>...       # BL / B instructions targeting each address
    python3 -I re/ghidra/a64scan.py <main.elf> bltargets          # every in-text BL target (feed to TomodachiLlmCreateAt.java)
    python3 -I re/ghidra/a64scan.py <main.elf> movz <imm16>       # MOVZ Wd/Xd, #imm16 (e.g. 0x492, the MSBT label hash)

Addresses are in Ghidra's NSO layout (base 0x7100000000); the ELF itself is based at 0.
Run it on files in ../dumps/, never on copies inside the repo.
"""

import struct
import sys

BASE = 0x7100000000


def exec_segments(f: bytes):
    phoff = struct.unpack_from("<Q", f, 0x20)[0]
    n = struct.unpack_from("<H", f, 0x38)[0]
    for i in range(n):
        typ, flags, off, va, _pa, fsz, _msz, _al = struct.unpack_from("<IIQQQQQQ", f, phoff + i * 56)
        if typ == 1 and flags & 1:
            yield va, struct.unpack_from("<%dI" % (fsz // 4), f, off)


def branch_target(pc: int, w: int):
    imm = w & 0x3FFFFFF
    if imm & (1 << 25):
        imm -= 1 << 26
    return pc + imm * 4


def addr(s: str) -> int:
    v = int(s, 16)
    return v - BASE if v >= BASE else v


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    f = open(argv[0], "rb").read()
    cmd, rest = argv[1], argv[2:]
    if cmd == "adrp":
        targets = {addr(a): a for a in rest}
        pages = {t & ~0xFFF for t in targets}
        for va, words in exec_segments(f):
            for i, w in enumerate(words):
                if (w & 0x9F000000) != 0x90000000:
                    continue
                rd = w & 31
                imm = (((w >> 5) & 0x7FFFF) << 2) | ((w >> 29) & 3)
                if imm & (1 << 20):
                    imm -= 1 << 21
                page = ((va + i * 4) & ~0xFFF) + (imm << 12)
                if page not in pages:
                    continue
                for j in range(i + 1, min(i + 12, len(words))):
                    w2 = words[j]
                    if (w2 & 0xFF800000) == 0x91000000 and ((w2 >> 5) & 31) == rd:
                        a = page + ((w2 >> 10) & 0xFFF)
                        if a in targets:
                            print(targets[a], "at", hex(BASE + va + j * 4))
                        break
    elif cmd in ("bl", "bltargets"):
        targets = {addr(a): a for a in rest}
        seen = set()
        for va, words in exec_segments(f):
            end = va + len(words) * 4
            for i, w in enumerate(words):
                op = w >> 26
                if op not in (0x25, 0x05):
                    continue
                pc = va + i * 4
                d = branch_target(pc, w)
                if cmd == "bl" and d in targets:
                    print(targets[d], "BL" if op == 0x25 else "B ", "from", hex(BASE + pc))
                elif cmd == "bltargets" and op == 0x25 and va <= d < end:
                    seen.add(d)
        for d in sorted(seen):
            print(hex(BASE + d))
    elif cmd == "movz":
        imm = int(rest[0], 0) & 0xFFFF
        for va, words in exec_segments(f):
            for i, w in enumerate(words):
                if (w & 0x7FFFFFE0) == (0x52800000 | (imm << 5)):
                    print(hex(BASE + va + i * 4))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
