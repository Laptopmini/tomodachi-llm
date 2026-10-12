# Model benchmarks

How fast and how usable each model is for the bridge's work: rewriting real Mii lines from our play logs. "OK" means the line passed every check the bridge applies in the game, so it would have been played. Newest results last; models change quickly, so re-run before relying on old numbers.

**Summary (2026-10-11):** Gemma 4 26B A4B is the default both locally (40/40, 0.78 s per line on an M5 Pro) and on OpenRouter (40/40, 0.48 s, about $0.00006 per line). Qwen 3.6 35B A3B is the faster local alternative; DeepSeek V4.1 Flash the fastest cloud one. With the game running on the same Mac, Gemma stays at 40/40 and takes about 1.2 s per line (p50), with no frame drops in the game (2026-10-11).

## Re-running

From `bridge/`, with a corpus of session logs (`bridge/logs/events-*.jsonl`, or the dev logs below):

```sh
# local (LM Studio): --lms loads each model before its run and unloads it after
uv run python -m bench --config config.toml --corpus 'logs/events-*.jsonl' --models google/gemma-4-26b-a4b,qwen/qwen3.6-35b-a3b --lms --limit 40
# cloud (OpenRouter, paid: counts toward spend_cap_usd and stops at the cap)
uv run python -m bench --config config.toml --backend openrouter --corpus 'logs/events-*.jsonl' --models google/gemma-4-26b-a4b-it --limit 40
```

Each run prints one summary line per model (OK count, p50/p90/max latency, cost) and writes every line and output to `<log_dir>/bench-<time>.json`. Record new runs below with the date, hardware, setup (model and game on one machine, on two, or cloud), runtime versions and quantization. The development runs' raw results are in `~/Documents/tomodachi-llm-work/logs/bench-*.json`.

## 2026-10-09: relines, local models (LM Studio), game not running

- **Hardware:** MacBook, Apple M5 Pro, 48 GB unified memory. **Setup:** everything on the Mac, but this run was without the emulator running; a run with the game running is still to do.
- **Runtime:** LM Studio 0.4.25+1, llama.cpp engine 2.55.0 (GGUF) and the MLX engine (MLX models); server on `127.0.0.1:1234`; context length 4096; one request at a time; `temperature` 0.9; JSON-schema structured output; `reasoning_effort: "none"`.
- **Task:** 40 distinct single-page Mii lines from our own play logs (relines eligibility: balloon speech, not Mii language, not multi-page), each turned into one reline request (`relines.request`), English (`USen`). "OK" = passes the validator and the placeholder check (`relines.compose`). Latency is the whole request, measured after a warm-up request.

| Model | Quantization | Engine | OK | p50 | p90 | max | Notes |
|---|---|---|---|---|---|---|---|
| **google/gemma-4-26b-a4b** | GGUF Q8_0 | llama.cpp | **40/40** | **0.78 s** | 0.90 s | 0.97 s | **Default.** Rewrites every line (0/40 unchanged), lively, keeps names as placeholders |
| qwen/qwen3.6-35b-a3b | MLX 4-bit | MLX | 40/40 | 0.60 s | 0.71 s | 0.78 s | Fastest; 3/40 lines left unchanged. Good alternative |
| qwen/qwen3.8-27b | MLX 4-bit | MLX | 40/40 | 2.19 s | 2.62 s | 2.86 s | Dense model, ~3× slower; often keeps the original wording (earlier prompt) |
| meta/muse-glimmer | GGUF Q4_K_M | llama.cpp | 0/40 | 13.7 s | | | Ignores `reasoning_effort: "none"`: reasons until `max_tokens` and returns no content. Not usable as is |

- Qwen3.8 and Muse Glimmer were measured with the first version of the prompt; Gemma and Qwen3.6 with the current one (style-only tags moved out of placeholders, looser length limit, "never repeat the vanilla line"). With the first prompt Gemma was also 40/40 (p50 0.77 s) and Qwen3.6 37/40 (p50 0.48 s; failures: a style tag shown as a placeholder, two lines one or two characters over the old limit).
- **Thinking must be off.** Without `reasoning_effort: "none"`, Qwen3.6 spent its whole token budget on reasoning and returned an empty answer (3.8 s).
- Loading takes 5–20 s per model (from disk cache).

## 2026-10-11: relines, cloud models (OpenRouter)

- **Runner:** `uv run python -m bench --backend openrouter --models … --limit 40` (bridge 0.6.4+), from the Mac; every call counted against the spend cap (`bridge/spend.py`). **Setup:** cloud (the game wasn't involved). Request as the bridge sends it: JSON-schema output, `temperature` 0.9 where the model accepts it, `provider.require_parameters` + `sort: latency`, reasoning off (`effort: "none"`; Gemini 3.8 Flash refuses that and ran with `effort: "low"`, which the bridge now switches to by itself).
- **Task:** the same as the local run: 40 distinct eligible Mii lines from our logs (corpus now from all sessions to 2026-10-11, so not the same 40 lines as 2026-10-09), USen, the current prompt and checks (placeholders, `{200.19}` verb, lowercase start, the speaker's own name). Latency is the whole request from the Mac, after one warm-up call. Cost from OpenRouter's `usage.cost`.

| Model | OK | p50 | p90 | max | Cost (40 lines) | Notes |
|---|---|---|---|---|---|---|
| **google/gemma-4-26b-a4b-it** | **40/40** | 0.48 s | 1.25 s | 1.69 s | $0.0023 | **Recommended cloud default** (same model as local) |
| deepseek/deepseek-v4.1-flash | 38/40 | **0.21 s** | 0.30 s | 0.33 s | $0.0031 | Fastest. Rejected: one line 5 characters over, one placeholder |
| qwen/qwen3.8-flash | 38/40 | 0.91 s | 1.30 s | 2.88 s | $0.0021 | Cheapest. 2 placeholder rejects; 2 lines nearly unchanged |
| openai/gpt-5.4-mini | 40/40 | 0.95 s | 1.13 s | 1.33 s | $0.0293 | Good, ~13x Gemma's cost |
| google/gemini-3.8-flash | 36/40 | 1.41 s | 2.13 s | 7.30 s | $0.0313 | Reasoning can't be turned off; 3 malformed replies |
| anthropic/claude-haiku-5.5 | 34/40 | 1.61 s | 1.74 s | 1.95 s | $0.0065 | Writes slightly long: 5 lines over the length limit by 1-8 characters |
| mistralai/mistral-small-2603 | (2/40) | | | | $0.0003 | 37 HTTP errors from the provider; not a quality result, rerun later |

- **Quality** (read side by side): all of them keep the game's tone and the placeholders; differences are small. E.g. "Hello there, @2. I'm known as @4." → Haiku "Well, hey there, @2! Folks call me @4, nice to meet ya." / GPT "Well, hi there, @2. People call me @4." / DeepSeek "Well hey there, @2! Folks around here call me @4."
- **Two request fixes came out of this run** (bridge 0.7.0): with `provider.require_parameters`, a parameter a model doesn't accept (`temperature` on Claude Haiku 5.5 and GPT-5.4 mini) left no provider (404 "No endpoints found"); the backend now leaves out what the model doesn't list in `supported_parameters` (GET /models, free). Models that refuse to run without reasoning (Gemini 3.8 Flash: 400 "Reasoning is mandatory") are retried once with `effort: "low"` (the refused call is free).
- Total spent on these runs, probes included: $0.083 of the $5 cap.
- Raw results: `bench-20261011-135633.json` (the Gemma run was stopped before writing its file; its summary line above is from the console).

## 2026-10-11: relines, local model (LM Studio), with the game running

- **Hardware:** MacBook, Apple M5 Pro, 48 GB unified memory. **Setup:** everything on the Mac: Ryujinx (Ryubing) Canary 1.3.351 playing game 1.0.4 with the dev mod (plugin 0.7.1), the bridge (0.7.3) serving the session, and the benchmark, all at once.
- **Runtime:** LM Studio 0.4.25+1, llama.cpp engine 2.55.0, `google/gemma-4-26b-a4b` GGUF Q8_0 (already loaded), same request settings as 2026-10-09.
- **Task:** 40 distinct eligible Mii lines from all our session logs to date (the same corpus as the baseline below). The bridge was also writing lines for the live session during the run (5 in that minute), so some requests overlapped: realistic for play, slightly pessimistic for the benchmark.

| Run | OK | p50 | p90 | max | Free memory |
|---|---|---|---|---|---|
| Baseline, game closed (19:03) | 39/40 | 0.78 s | 1.36 s | 2.11 s | 26% |
| **Game running** (19:14) | **40/40** | **1.17 s** | 2.28 s | 4.99 s | 16% → 14% |

- **The game:** no frame drops or other effects during the run (owner, playing at the time).
- **Memory:** LM Studio's llama.cpp server 27.2 GB, Ryujinx 1.3 GB resident; swap use unchanged (~4.5 GB, from before). Free memory went from 26% (game closed) to 14-16%: Gemma Q8 and the game fit together on 48 GB.
- **Conclusion:** running the model on the same Mac as the game works. Lines take about 50% longer (the slowest about twice as long), which the mod absorbs because it writes lines before their next showing. A smaller or 4-bit model (e.g. Qwen 3.6 35B A3B MLX 4-bit, ~20 GB) leaves more room on 32 GB machines; not measured with the game.
- Raw results: `bench-20261011-190301.json` (baseline) and `bench-20261011-191544.json` (game running).
