"""Summarize the game's play reports from a Ryujinx log: which AI behaviors and scenes ran.

Ryujinx logs every play report (`ServicePrepo ProcessPlayReport`) the game sends. The game's own
reports (`game_stat_miiai`, `game_stat_demo`, `game_trouble_resolve`, ...) list AI actions as
MurmurHash3 IDs (see scripts/bgyml_hash.py); this script decodes them with names taken from the dumps.

    uv run --with mmh3 python -I scripts/playreports.py ~/Library/Logs/Ryujinx/<session>.log [--dumps ../dumps/1.0.4]

Reads game data from the dumps folder (outside the repo); prints a table, writes nothing.
"""

import argparse
import glob
import os
import re
import sys

import mmh3


def entries(log_path: str):
    """Yield (room, fields) for each AI/demo/trouble entry in the game's play reports."""
    room, cur, common = None, {}, {}
    with open(log_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = re.search(r"Room: (\w+)", line)
            if m:
                room, cur = m.group(1), {}
                continue
            if not room or not room.startswith("game_"):
                continue
            m = re.search(r'"(\w+)": (-?[\w.]+),?$', line.strip())
            if not m:
                continue
            k, v = m.groups()
            if k in ("RegionLanguage", "MiiNum", "RomVersion"):
                common[k] = v
            cur[k] = v
            if k in ("NextAI", "DemoStage", "TroubleResolveKey"):
                yield room, dict(cur, **{"_" + c: x for c, x in common.items()})
                cur = {}


def name_table(dumps: str) -> dict[int, str]:
    names: set[str] = set()
    ai_dir = os.path.join(dumps, "romfs_extracted/Pack/AI.Product.100/AI")
    for f in os.listdir(ai_dir) if os.path.isdir(ai_dir) else []:
        stem = re.sub(r"\.ainb$", "", f)
        names.update([stem, re.sub(r"\.(root|module)$", "", stem)])
    for p in glob.glob(os.path.join(dumps, "romfs_text/**/*.yml"), recursive=True):
        names.add(os.path.basename(p).split(".")[0])
    elf = os.path.join(dumps, "elf/main.elf")
    if os.path.exists(elf):
        with open(elf, "rb") as f:
            for s in re.findall(rb"[\x20-\x7e]{3,80}", f.read()):
                t = s.decode()
                names.add(t)
                names.update(p for p in re.split(r"[^A-Za-z0-9_]+", t) if p)
    idx = os.path.join(dumps, "index/USen.tsv")
    if os.path.exists(idx):
        with open(idx, encoding="utf-8", errors="ignore") as f:
            for line in f:
                parts = line.split("\t")
                if len(parts) > 1:
                    names.add(parts[1])
    return {mmh3.hash(n.encode(), 0, signed=False): n for n in names if n}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--dumps", default=os.path.join(os.path.dirname(__file__), "../../dumps/1.0.4"))
    a = ap.parse_args(argv)
    names = name_table(a.dumps)

    def n(v: str) -> str:
        return names.get(int(v), v) if v and v.lstrip("-").isdigit() else v

    print("room\tevent\tsec\tenters\tid\tgraph\tlang")
    for room, e in entries(a.log):
        key = e.get("StringId") or e.get("DemoId") or e.get("TroubleId") or ""
        print(room, e.get("EventType", ""), e.get("TotalExectionSec", ""),
              e.get("PhaseModuleCoreActTotalEnterCount", ""), n(key), n(e.get("AinStringId", "")),
              e.get("_RegionLanguage", ""), sep="\t")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
