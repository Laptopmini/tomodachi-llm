"""Flatten tomo's converted MSBT YAML into one TSV for grepping.

Usage (input and output both live in ../dumps, never in the repo):
    tomo romfs extract --convert --out ../dumps/<ver>/romfs_text/Mals ../dumps/<ver>/romfs/Mals
    python3 -I scripts/msgindex.py ../dumps/<ver>/romfs_text/Mals/USen.Product.100 ../dumps/<ver>/index/USen.tsv

Columns: file, label, attrs (JSON), text (newlines as a literal \\n).
"""
import json, os, re, sys

root, out = sys.argv[1], sys.argv[2]
lab_re = re.compile(r'^  - label: "(.*)"$')
attr_re = re.compile(r'^      "([^"]+)": "(.*)"$')

def flush(w, f, m):
    if m:
        w.write("\t".join([f, m["label"], json.dumps(m["attrs"], ensure_ascii=False),
                           "\\n".join(m["text"])]) + "\n")

with open(out, "w", encoding="utf-8") as w:
    for dp, _, fns in os.walk(root):
        for fn in sorted(fns):
            if not fn.endswith(".msbt.yml"):
                continue
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, root)
            cur, mode = None, None
            for line in open(p, encoding="utf-8"):
                line = line.rstrip("\n")
                mm = lab_re.match(line)
                if mm:
                    flush(w, rel, cur)
                    cur, mode = {"label": mm.group(1), "attrs": {}, "text": []}, None
                    continue
                if cur is None:
                    continue
                if line == "    attrs:":
                    mode = "attrs"
                elif line.startswith("    text: |") or line.startswith("    text: "):
                    if line.startswith("    text: |"):
                        mode = "text"
                    else:
                        cur["text"].append(line[len("    text: "):].strip('"'))
                        mode = None
                elif mode == "attrs":
                    am = attr_re.match(line)
                    if am:
                        cur["attrs"][am.group(1)] = am.group(2)
                elif mode == "text":
                    if line.startswith("      "):
                        cur["text"].append(line[6:])
                    elif line == "":
                        cur["text"].append("")
                    else:
                        mode = None
            flush(w, rel, cur)
