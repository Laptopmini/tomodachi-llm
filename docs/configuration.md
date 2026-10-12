# Configuration

The bridge reads `bridge/config.toml`. On first start, the launcher (`start-bridge.command`) creates it from `bridge/config.example.toml`. After that, the file is yours and updates never overwrite it. **Restart the bridge after changing it.**

The defaults work out of the box with LM Studio running `google/gemma-4-26b-a4b` and Ryujinx on macOS. You only need to edit the file to:
- use another model
- use OpenRouter
- use a different emulator or SD card folder
- change the spend cap

Paths can start with `~`. Relative paths (`log_dir`, `line_store`, `spend_file`) are relative to the folder `config.toml` is in.

**API keys never go in this file.** OpenRouter's key comes from the `OPENROUTER_API_KEY` environment variable, and LM Studio's optional token from `LMSTUDIO_API_TOKEN`. Add a line like `export OPENROUTER_API_KEY=sk-or-…` to `~/.zshrc`. When you double-click the launcher, it picks the key up from your shell.

## Common changes

| I want to… | Set |
|---|---|
| Use a different LM Studio model | `[lmstudio] model` = the model's identifier in LM Studio (as `lms ls` shows it), and load it in LM Studio |
| Use OpenRouter only | `[generation] backend = "openrouter"`, `fallback = false`; set `OPENROUTER_API_KEY` |
| Use LM Studio, with OpenRouter when LM Studio fails | keep `backend = "auto"` and `fallback = true`; set `OPENROUTER_API_KEY` |
| Never make paid calls | leave `OPENROUTER_API_KEY` unset (or `fallback = false` with LM Studio) |
| Spend more or less on OpenRouter | `[openrouter] spend_cap_usd` |
| Stop lines from changing every time | `[generation] reline_refresh = false` |
| Start over with no saved lines | delete `bridge/lines.json` while the bridge is stopped |
| Use Ryujinx on Windows (untested) | `[server] file_dir = "C:/Users/<you>/AppData/Roaming/Ryujinx/sdcard/tomodachi-llm"` (the full path; `%APPDATA%` isn't expanded) |

## `[server]`: how the bridge reaches the plugin

| Key | Default | What it does |
|---|---|---|
| `transport` | `"file"` | `"file"`: exchange messages through two files in the emulator's SD card folder. `"socket"`: TCP, with the plugin connecting to `host:port`. Must match `transport` in the plugin's `config.ini`. Keep `"file"`: emulators run the game's network calls one at a time (about 1 s each on Ryujinx), files don't have that cost |
| `file_dir` | `"~/Library/Application Support/Ryujinx/sdcard/tomodachi-llm"` | The emulator's SD card folder plus `/tomodachi-llm`. The plugin creates it the first time the game starts with the mod; the bridge waits for it. On Ryujinx, **File → Open Ryujinx Folder** shows where `sdcard` is |
| `poll_ms` | `20` | How often (milliseconds) the bridge checks for new messages from the plugin |
| `host` | `"127.0.0.1"` | Socket transport only: the address the bridge listens on. The plugin's `host` must be the same address, as an IP (not a host name) |
| `port` | `8765` | Socket transport only: the TCP port, same as the plugin's `port` |
| `log_dir` | `"logs"` | Folder for the session logs (`events-<time>.jsonl`, one per bridge run) and benchmark results |

## `[generation]`: what gets written, and by which model

| Key | Default | What it does |
|---|---|---|
| `backend` | `"auto"` | Which model service to use. `"auto"`: whichever of `[lmstudio]` and `[openrouter]` is set up, LM Studio first. `"lmstudio"` or `"openrouter"`: that one first, the other as fallback if it's set up too. `"mock"`: no model, canned lines (testing). LM Studio counts as set up when it has `base_url` and `model`; OpenRouter when it has a `model` **and** its key is in the environment |
| `fallback` | `true` | When two services are set up, try the second when the first fails, times out or gives a malformed answer. `false`: only the first |
| `relines` | `true` | Rewrite Mii lines and narrator captions. `false` turns generation off: the bridge only logs (the plugin then plays everything vanilla) |
| `reline_refresh` | `true` | Each time a generated line plays, write a new one for the next time, so repeated lines keep changing. One model call per line shown: free on LM Studio, a fraction of a cent on OpenRouter |
| `line_store` | `"lines.json"` | File where generated lines are kept between sessions, so lines you've seen before play rewritten from their first showing. `""`: don't keep them |
| `line_store_max` | `4000` | Lines kept per game language; the least recently used are dropped first (narrator captions before speech) |
| `scenes` | `true` | Befriend conversations: write the rest of a conversation in one call, so its lines fit together. Needs `FriendUgc_` in the plugin's `scripts=` |
| `scene_deadline_s` | `15.0` | Seconds a whole-conversation call may take before it's given up |
| `scene_warm_delay_s` | `5.0` | Seconds after the game loads its conversation scripts before the bridge writes their first lines ahead |
| `deadline_s` | `8.0` | Seconds one line may take, across the main service and the fallback together. Past it, the line isn't sent (the game plays its own) |
| `reline_page_wait_s` | `20.0` | Not in the example file. For a line of several balloons, the new version is sent once all its balloons have been shown, or after this many seconds without one |
| `cache_size` | `512` | Recent model answers kept in memory; an identical request is answered from it without a model call. `0` turns it off |

## `[lmstudio]`: local models

| Key | Default | What it does |
|---|---|---|
| `base_url` | `"http://127.0.0.1:1234/v1"` | LM Studio's server (its default port is 1234). For LM Studio on another computer: that computer's IP, with LM Studio's server set to listen on the network and authentication on. Empty: don't use LM Studio |
| `model` | `"google/gemma-4-26b-a4b"` | The model's identifier in LM Studio. It must be loaded (`lms ps` lists loaded models). Recommended models: [README](../README.md#recommended-models), measurements: [model-benchmarks.md](model-benchmarks.md) |
| `api_key_env` | `"LMSTUDIO_API_TOKEN"` | Name of the environment variable holding LM Studio's token, if you turned authentication on. Unset variable: no token is sent |
| `timeout_s` | `6.0` | Seconds one LM Studio call may take. Kept under `deadline_s` so a fallback still has time |
| `extra_body` | `{ reasoning_effort = "none" }` | Extra fields added to every request as-is. `reasoning_effort = "none"` turns thinking off: with it on, thinking models used their whole answer budget thinking and returned nothing. Also the place to override the temperature (default 0.9), e.g. `{ reasoning_effort = "none", temperature = 0.7 }` |

## `[openrouter]`: cloud models

| Key | Default | What it does |
|---|---|---|
| `base_url` | `"https://openrouter.ai/api/v1"` | OpenRouter's API |
| `model` | `"google/gemma-4-26b-a4b-it"` | The model's ID on openrouter.ai (as in its URL). Recommended: [README](../README.md#recommended-models) |
| `api_key_env` | `"OPENROUTER_API_KEY"` | Name of the environment variable holding your OpenRouter key. If it's unset, OpenRouter isn't used |
| `timeout_s` | `20.0` | Seconds one OpenRouter call may take (still within `deadline_s` overall) |
| `spend_cap_usd` | `5.0` | Spending limit in US dollars. The bridge adds up the cost OpenRouter reports for each call and makes no more paid calls at the cap (LM Studio or the game's own lines are used instead). The benchmark counts toward it too |
| `spend_file` | `"openrouter-spend.json"` | Where the running total is kept. To start counting from zero, delete it, or raise `spend_cap_usd` |
| `extra_body` | `{ provider = { require_parameters = true, sort = "latency" }, reasoning = { effort = "none" } }` | Extra request fields, as-is. `provider.require_parameters`: only use providers that support JSON-schema output. `sort = "latency"`: fastest provider first. `reasoning.effort = "none"`: thinking off (a model that refuses is retried once with `"low"`) |

It's also worth setting a credit limit for the key on openrouter.ai, as a second safeguard.

## `[mock]`: no model (testing)

Used only with `backend = "mock"`.

| Key | Default | What it does |
|---|---|---|
| `mode` | `"template"` | `"template"`: answer with the game's own line plus a marker. `"fail"`: every call fails. `"malformed"`: answers that aren't valid JSON |
| `delay_s` | `0.2` (example) | Seconds each answer takes |

## Plugin `config.ini`

The plugin has its own small config, at `romfs/tomodachi-llm/config.ini` inside the mod folder (`tomodachi-llm/` in the release, copied into Ryujinx's mods directory) (`key=value` lines, `#` comments). The release defaults are in `hook/config.release.ini`. You rarely need to change it; restart the game afterwards.

| Key | Release | What it does |
|---|---|---|
| `reline` | `1` | Play generated lines. `0`: the game plays only its own lines; events are still reported |
| `transport` | `file` | `file` or `socket`; must match the bridge's `[server] transport` |
| `host`, `port` | `127.0.0.1`, `8765` | Socket transport only: the bridge's address (an IP, not a host name) |
| `rx` | `1` | Read the bridge's messages. `0`: send only (no generated lines arrive) |
| `hook_labels`, `hook_lines` | `1` | The two hooks the mod needs (message lookup, text expansion) |
| `scripts` | `FriendUgc_` | Label prefixes of message files the plugin reads whole ahead of time (up to 4, comma-separated). `FriendUgc_` = befriend conversations |
| `debug_log` | `0` | `1`: also write every event to the emulator's log (noisy) |
| `hook_index`, `hook_chat`, `backtrace` | `0` | Research hooks and call-stack logging, on in the development config only |
