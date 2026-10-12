"""Model benchmark for relines: replays Mii lines from session logs against each model and reports
latency and how many results pass the bridge's checks (validator + placeholders).

    uv run python -m bench --config config.toml --corpus ~/Documents/tomodachi-llm-work/logs/events-*.jsonl \\
        --models qwen/qwen3.6-35b-a3b,google/gemma-4-26b-a4b [--lms] [--limit 40]

--lms loads each model with LM Studio's `lms` CLI before its run and unloads it afterwards (local models only).
Uses the [lmstudio] section of the config for the endpoint and extra_body. Results go to stdout and to
<log_dir>/bench-<timestamp>.json.

--backend openrouter runs the models on OpenRouter instead (paid): the [openrouter] section's endpoint, key
(from the environment) and extra_body, and its spend cap: every call counts toward `spend_cap_usd` in the same
file as the bridge's (spend.py), and the run stops at the cap. Each model's cost is reported.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import glob
import json
import os
import statistics
import subprocess
import sys
import time

import config as config_mod
import relines
import spend
from backends.openai_compat import OpenAICompatBackend
from generator import Generator

LMS = os.path.expanduser("~/.lmstudio/bin/lms")


def load_corpus(patterns: list[str], limit: int) -> list[dict]:
    seen, out = set(), []
    for pat in patterns:
        for path in sorted(glob.glob(os.path.expanduser(pat))):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("t") != "line" or not ev.get("src"):
                        continue
                    ev.setdefault("key", ev["src"])  # logs from before plugin 0.3.0 have no key
                    if ev.get("relined") or not relines.eligible(ev) or ev["src"] in seen:
                        continue
                    seen.add(ev["src"])
                    out.append(ev)
    return out[:limit]


def lms(*args: str) -> None:
    subprocess.run([LMS, *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def backend_config(cfg_path: str, which: str, model: str) -> config_mod.BackendConfig:
    with open(cfg_path, "rb") as f:
        raw = config_mod.tomllib.load(f)
    if which == "openrouter":
        b = config_mod._backend("openrouter", raw)
        if not b.api_key:
            raise SystemExit("OpenRouter needs its API key in the environment ([openrouter] api_key_env)")
        spend_file = os.path.expanduser(b.spend_file)
        if not os.path.isabs(spend_file):
            spend_file = os.path.join(os.path.dirname(os.path.abspath(cfg_path)), spend_file)
        b.spend_file, b.model, b.timeout_s = spend_file, model, 60
        return b
    lm = raw.get("lmstudio", {})
    return config_mod.select_backends({"generation": {"backend": "lmstudio"}, "lmstudio": {
        "base_url": lm.get("base_url") or "http://127.0.0.1:1234/v1", "model": model,
        "timeout_s": 60, "extra_body": lm.get("extra_body", {"reasoning_effort": "none"})}})[0]


async def run_model(bcfg: config_mod.BackendConfig, corpus: list[dict], lang: str) -> dict:
    model = bcfg.model
    backend = OpenAICompatBackend(bcfg)
    spent0 = backend.spend.total_usd if backend.spend else 0.0
    gen = Generator([backend], deadline_s=60, cache_size=0)
    warm = relines.plan("warm", "{6.6}I love the sea!")
    await gen.run(relines.request(warm, "I love the sea!", lang))  # first call pays for prompt processing
    rows = []
    for ev in corpus:
        p = relines.plan(ev["key"], ev["src"])
        t0 = time.perf_counter()
        out = await gen.run(relines.request(p, ev.get("out", ""), lang, ev["src"], caption=bool(ev.get("caption"))))
        dt = time.perf_counter() - t0
        if out.error and "spend cap" in out.error:
            print(f"{model}: {out.error}; stopping", flush=True)
            break
        text = relines.compose(p, {ln["slot_id"]: ln["text"] for ln in out.lines}) if out.lines else None
        why = out.error or out.rejected.get("0") or (None if text else "placeholders")
        rows.append({"src": ev["src"], "secs": round(dt, 3), "ok": text is not None, "text": text,
                     "raw": out.lines[0]["text"] if out.lines else None, "why": why})
    await backend.aclose()
    secs = [r["secs"] for r in rows]
    return {
        "model": model, "backend": bcfg.name, "n": len(rows), "ok": sum(r["ok"] for r in rows),
        "cost_usd": round(backend.spend.total_usd - spent0, 6) if backend.spend else None,
        "p50_s": round(statistics.median(secs), 3) if secs else None,
        "p90_s": round(sorted(secs)[int(len(secs) * 0.9) - 1], 3) if len(secs) >= 10 else None,
        "max_s": max(secs) if secs else None, "rows": rows,
    }


async def amain(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--corpus", nargs="+", required=True)
    ap.add_argument("--models", required=True)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--lang", default="USen")
    ap.add_argument("--lms", action="store_true", help="load/unload each model with LM Studio's lms CLI")
    ap.add_argument("--backend", choices=["lmstudio", "openrouter"], default="lmstudio")
    a = ap.parse_args(argv)
    if a.backend == "openrouter" and a.lms:
        raise SystemExit("--lms is for LM Studio models only")
    cfg = config_mod.load(a.config)
    corpus = load_corpus(a.corpus, a.limit)
    print(f"corpus: {len(corpus)} distinct eligible lines", flush=True)
    results = []
    for model in [m.strip() for m in a.models.split(",") if m.strip()]:
        if a.lms:
            lms("unload", "--all")
            t0 = time.perf_counter()
            lms("load", model, "--context-length", "4096", "-y")
            print(f"{model}: loaded in {time.perf_counter() - t0:.1f}s", flush=True)
        try:
            res = await run_model(backend_config(a.config, a.backend, model), corpus, a.lang)
        except spend.SpendCapReached as e:
            print(f"{model}: {e}; stopping", flush=True)
            break
        results.append(res)
        cost = f", ${res['cost_usd']:.4f}" if res["cost_usd"] is not None else ""
        print(f"{model}: {res['ok']}/{res['n']} ok, p50 {res['p50_s']}s, p90 {res['p90_s']}s, max {res['max_s']}s"
              f"{cost}", flush=True)
        for r in res["rows"][:6]:
            print(f"    {r['src'][:60]!r} -> {r['text'] or r['why']!r}", flush=True)
        if a.lms:
            lms("unload", "--all")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(os.path.expanduser(cfg.log_dir), f"bench-{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": stamp, "lang": a.lang, "results": results}, f, ensure_ascii=False, indent=1)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain(sys.argv[1:])))
