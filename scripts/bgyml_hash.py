"""Hash or reverse-lookup the u32 enum IDs used in the game's BGYML files.

The IDs are MurmurHash3 x86_32 with seed 0 over the UTF-8 name (found 2026-10-08,
see re/ai-graphs.md). Run outside the repo's data; nothing here reads game files
unless you point it at them.

    uv run --with mmh3 python -I scripts/bgyml_hash.py hash ChatMiiHappy Good
    uv run --with mmh3 python -I scripts/bgyml_hash.py lookup <names.txt> 4288752458 1645779707

`lookup` hashes every line of names.txt (plus the identifier-like pieces of each
line) and prints the names that match each value, or `?`.
"""

import re
import sys

import mmh3


def bgyml_hash(name: str) -> int:
    return mmh3.hash(name.encode("utf-8"), 0, signed=False)


def candidates(path: str) -> set[str]:
    out: set[str] = set()
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if not s or len(s) > 120:
                continue
            out.add(s)
            for part in re.split(r"[^A-Za-z0-9_]+", s):
                if part:
                    out.add(part)
                    out.update(part.split("_"))
                    out.update(re.findall(r"[A-Z][a-z0-9]*", part))
    out.discard("")
    return out


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "hash":
        for name in argv[1:]:
            print(bgyml_hash(name), name)
        return 0
    if len(argv) >= 3 and argv[0] == "lookup":
        table: dict[int, list[str]] = {}
        for name in candidates(argv[1]):
            table.setdefault(bgyml_hash(name), []).append(name)
        for v in argv[2:]:
            print(v, " ".join(sorted(table.get(int(v), ["?"]))))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
