"""Receive the plugin's event stream (JSON lines over TCP) and save it.

Early stand-in for the bridge (before it existed): accepts the plugin's connection, appends every line to
~/Documents/tomodachi-llm-work/logs/events-<timestamp>.jsonl and prints a short summary of each.

    python3 -I scripts/event_listen.py [--host 127.0.0.1] [--port 8765]

Stdlib only; no network access beyond the listening socket.
"""

import argparse
import datetime
import json
import os
import socket
import sys


def summarize(ev: dict) -> str:
    t = ev.get("t")
    if t == "label":
        return f"label {ev.get('label')}  <- {ev.get('lr')}  | {ev.get('text', '')[:80]}"
    if t == "line":
        return f"line  {ev.get('out', '')[:100]}  <- {ev.get('lr')}  ugc={ev.get('ugc')}"
    return json.dumps(ev)[:160]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--out-dir", default=os.path.expanduser("~/Documents/tomodachi-llm-work/logs"))
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((a.host, a.port))
    srv.listen(1)
    print(f"listening on {a.host}:{a.port}", flush=True)
    while True:
        conn, peer = srv.accept()
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(a.out_dir, f"events-{stamp}.jsonl")
        print(f"connection from {peer}; writing {path}", flush=True)
        buf = b""
        with conn, open(path, "ab") as out:
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    out.write(line + b"\n")
                    out.flush()
                    try:
                        print(summarize(json.loads(line)), flush=True)
                    except json.JSONDecodeError:
                        print(f"(bad json) {line[:120]!r}", flush=True)
        print("connection closed", flush=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
