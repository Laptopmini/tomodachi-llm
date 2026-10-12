# How it works

Tomodachi LLM has two programs: a **plugin** that runs inside the game on the emulator, and a **bridge** that runs on your computer and talks to the model. They exchange JSON messages through two files on the emulator's SD card.

File paths below (`hook/`, `bridge/`, `re/`) refer to the source repository.

```
 emulator (Ryujinx)                                   your computer
┌─────────────────────────────────┐                  ┌──────────────────────────┐      ┌──────────────────┐
│ game 1.0.4                      │   sdcard/        │ bridge (Python)          │ HTTP │ LM Studio        │
│   ↑ Text_Expand hook            │   tomodachi-llm/ │   plans each line        │─────▶│   or OpenRouter  │
│ plugin (Skyline, C++)           │   out.jsonl ────▶│   asks the model         │◀─────│                  │
│   logs lines, swaps in new ones │◀──── in.jsonl    │   checks the answer      │      └──────────────────┘
└─────────────────────────────────┘                  │   keeps lines (lines.json)│
                                                      └──────────────────────────┘
```

The main idea is **learn as you go**. The first time the game shows a Mii line, it plays as usual, and the plugin reports it. The bridge writes a new version and sends it back. The next time the game shows that line, the plugin plays the new version. Lines are kept between sessions, so lines you've seen before play rewritten from their first showing.

Nothing waits on the model. If a new line isn't ready, the game plays its own.

## Goals and design choices

The aim is to replace the real sentences Miis say with lines written for the moment, while keeping everything else the game's own: the text box, the voice, names, catchphrases, animations and who speaks when. Mii-language gibberish stays, because it's part of the game's feel.

| Requirement | How it's met |
|---|---|
| Local or cloud model, the user's choice | One OpenAI-compatible client for LM Studio and OpenRouter. The plugin never talks to the model; only the bridge does, so API keys stay on the computer and the model can change without rebuilding the mod |
| Never make the game wait or break | Hooks only copy data and return; all I/O runs on a worker thread. Any failure plays the vanilla line. The plugin checks the game version and installs nothing on a mismatch |
| Hide the model's latency | Lines are written before they're needed: after a line's first showing (for its next showing), and ahead of time for scripted conversations. Lines are kept between sessions |
| Write in the player's language | The plugin reads the game's language at start, and the model is told to write in it |

**Why "learn as you go".** The first design asked the model when a conversation started, and held the conversation until the answer arrived. Testing changed that:
- On Ryujinx every network call from the game took about 1 s.
- The hooks meant to catch a conversation starting never fired.
- Most lines are picked at the last moment, from their attributes.

Lines repeat a lot, though: the same greeting, the same musing. Rewriting a line after it's been seen once needs only the text hook and never waits. Holding a line until it's ready remains a possible addition ([roadmap.md](roadmap.md)).

## The components

| Part | Where | Role |
|---|---|---|
| Skyline-LTD 1.1.0 | release `tomodachi-llm/exefs/` (`subsdk9`, `main.npdm`) | Third-party loader. The emulator loads it in place of the game's SDK module; it then loads every plugin in `romfs/skyline/plugins/` |
| Plugin | `hook/` → `tomodachi-llm.nro` | Hooks the game's text functions, reports lines, swaps in generated ones |
| Plugin config | `hook/config.example.ini` (dev), `hook/config.release.ini` (release) → `romfs/tomodachi-llm/config.ini` | Switches and transport settings ([configuration.md](configuration.md#plugin-configini)) |
| Bridge | `bridge/` | Plans, generates, checks and sends lines; keeps them between sessions |
| Bridge config | `bridge/config.example.toml` → `bridge/config.toml` | Model choice, paths, limits ([configuration.md](configuration.md)) |
| Launcher | `scripts/start-bridge.command` | Finds `uv`, creates `config.toml` on first start, picks up API keys from your shell, starts the bridge |
| Game knowledge | `re/offsets.md`, `re/msbt.md`, `re/ai-graphs.md` | Addresses of the hooked functions, the text format, how the game picks conversations |

## The plugin (`hook/`)

### Loading safely

At game start, Skyline loads the plugin and calls its entry point (`hook/src/main.cpp`, at the end). The plugin:

1. Reads `content:/tomodachi-llm/config.ini`, which is its own file added to the game's files by the mod's `romfs/`.
2. Checks that the first instructions of each function it hooks match game 1.0.4 (`SigMatches`). If any differ, it installs no hooks and the game runs as if the mod weren't there.
3. Installs its hooks with And64InlineHook, which comes with Skyline.
4. Starts one **worker thread** that does all file and network I/O.

The hooks run on the game's own threads, so they follow strict rules. They never block or touch files or the network: they only copy an event into a fixed-size ring buffer (`hook/src/ring.h`) and return. Their stack use is kept small with a pool of scratch buffers, because some game threads have small stacks. Plugin 0.6.3 froze the Mii News by using too much.

Memory is fixed at build time and kept small (about 1.2 MB in all), because Skyline takes it from the game's heap.

### The hooks

| Hook | Game function (`re/offsets.md`) | What the plugin does |
|---|---|---|
| **Text_Expand** | Builds the text you see from a message template, filling in names, words and tags | Computes the template's **key**, a 64-bit FNV-1a hash of the raw template. If a generated line is stored for that key, it hands the game the new template instead. Then it logs a `line` event: key, template (`src`), text shown (`out`), caller address, and `caption: 1` for narrator captions |
| GetTextByLabel | Looks up a message by its label (e.g. `FriendUgc_Person_Success_0001_02`) | Logs a `label` event with the label and key. The first lookup in a file whose labels match `scripts=` also reads the **whole file** once, so the bridge knows a conversation's lines before they're shown |
| GetIndexByLabel | The lookup used by chat captions | Same whole-file read (`hook_index=1`, dev only) |
| Chat and befriend hooks | ScenarioChatSelector, FocusTelop, FriendUgc pickers | Observation only, for research (`hook_chat=1`, dev only) |

### How a generated line is played

The game's message templates mix text with **tags**: a Mii's name, the topic word, a voice or emotion, a pause, a page break. Tags that sit next to each other form a **tag run**. The bridge's generated text says `[k]` wherever the template's k-th tag run goes. In `hook/src/reline.h`, `BuildReline` builds a new template by copying the game's original tag runs into those places. The game then expands that template as usual, so names, articles, voices, text-to-speech and the conversation log all work as normal.

The plugin keeps generated lines in `RelineStore` (`hook/src/relinestore.h`): 4,096 entries and 256 KB of text, in memory only. The worker thread writes to it and the game threads read from it, without locks. Each entry carries a sequence number, and the text area is circular: when it wraps around, the oldest lines are dropped, and those play vanilla until the bridge sends them again.

## The bridge (`bridge/`)

| File | Role |
|---|---|
| `server.py` | Entry point. Follows the plugin's messages, tracks what was shown, starts jobs, sends lines back, writes the session log |
| `relines.py` | Turns one shown line into a request, then the model's answer back into a `[k]` line |
| `scenes.py` | Befriend conversations: writes the rest of a conversation in one call |
| `generator.py` | Runs a request on the backends in order (main, then fallback) within a deadline, then validates the answer |
| `backends/openai_compat.py` | One client for LM Studio and OpenRouter (OpenAI-compatible `/chat/completions`, JSON-schema output) |
| `backends/mock.py` | Canned answers for tests and for running without a model |
| `prompts/builder.py` | The system prompt and the per-request message |
| `validator.py` | Checks for every generated line: one line, plain text, no markup or emoji, length limit |
| `store.py` | Generated lines kept between sessions (`lines.json`) |
| `spend.py` | OpenRouter spend cap: adds up each call's cost in a file |
| `config.py` | Reads `config.toml`, picks the backends |
| `protocol.py` | Message and request types |
| `bench.py` | Model benchmark ([model-benchmarks.md](model-benchmarks.md)) |

### Rewriting a line (relines)

1. **Pick.** The bridge only considers lines whose `line` event comes from the speech-balloon caller (`0x71001424b0`), plus narrator captions. It skips:
   - Mii-language lines (`{6.11}`)
   - lines with hand-placed line breaks
   - lines with more than 4 balloons
   - lines with fewer than 2 letters
   - templates the plugin had to cut short (`cut`)
2. **Plan** (`relines.plan`). It splits the template into balloons at `{0.4}`, then sorts its tag runs:
   - Runs at the start or end of a balloon (voice, emotion, a catchphrase wrapper) stay where they are.
   - Runs in the middle (names, the topic word, items) become placeholders `@1`, `@2`… that the model has to keep.
   - Style-only tags inside the line (pauses, emphasis) aren't shown to the model; they move to the end of the line.
3. **Ask** (`relines.request`, `prompts/builder.py`). The model gets:
   - the template in readable form
   - the line as it appeared on screen, so it knows what each placeholder became
   - what each placeholder stands for
   - a length limit
   - for a repeat, the versions it already wrote, so it writes something new
   - for a caption, the caption rules

   The answer is JSON (`{"lines": [{"slot_id", "text"}]}`), one slot per balloon.
4. **Check.** Every line goes through `validator.py`. The bridge then checks the placeholders: each must be used exactly once, and no new ones may appear. It also catches common mistakes:
   - a verb right after a pronoun word, as in "He's is"
   - a lowercase start
   - a speaker who loses their own name ("I'm @2")

   A rejected line isn't sent, so the game keeps its own.
5. **Send.** The bridge waits until every balloon of the line has been shown, so one showing never mixes two versions. Then it sends `{"t": "reline", "key", "text"}` and saves the line in the store.
6. **Refresh.** When a generated line plays, the bridge writes a fresh one for next time (`reline_refresh`), so lines you see often keep changing.

At most two reline jobs run at once.

### Befriend conversations (scenes)

When a Mii asks for help making a friend, or two Miis start a topic chat, the game plays a scripted conversation from `FriendUgc_*` labels. It has an opening, then lines that alternate between the two Miis, then a success or failure ending. The plugin reads these script files whole when the game loads them (`scripts=FriendUgc_`). `scenes.py` uses them two ways:

- **Ahead of time.** A few seconds after the scripts load, and again after each conversation, the bridge writes the first lines of each scene in the background. Those lines only refer to the topic through placeholders, so they can be written before it's known.
- **During a conversation.** Once the lines shown narrow it down to at most two possible scenes, one call writes every remaining line of those scenes, endings included. It continues from what was said and knows the topic word.

These lines are sent as ordinary relines, so whichever branch the game takes finds them ready. They aren't kept between sessions, because they're written for one topic.

### Narrator captions

Captions such as "{name} is staring at {name}" come from the same text function. The caller they share is also used by the conversation log, dialogs and tutorials, so the plugin marks a real caption by finding the caption window's frame on the call stack. The bridge treats captions as one short line of narration, with their own rules. They're kept between sessions, but they're the first to go when the store is full.

When the camera is on a Mii, the game leaves the name out ("is making up constellations"). That's the game's own behavior, and generated captions keep it.

### Kept lines (`lines.json`)

The bridge saves each generated line per game language: its text, the original line, and its recent versions. When the plugin connects, it says how many lines (`relines_max`) and how much text (`relines_bytes`) it can hold. The bridge then sends the most recently used kept lines, using at most half of that text space, so new lines don't push them out soon. A kept line that fails a check added since it was written is dropped. The file holds up to `line_store_max` lines per language, and the least recently used go first.

### Models, fallback and limits

- **Backends** (`config.py`). You choose LM Studio, OpenRouter or `auto`. With both set up and `fallback = true`, the second is tried when the first fails or is too slow, within the same deadline (`generator.py`).
- **Deadlines.** Each request gets `deadline_s` in all, and each attempt is capped by that backend's `timeout_s`:
  - Scene calls get `scene_deadline_s`.
  - Lines written ahead get 60 s and no fallback, since nothing is waiting for them.
- **Request.** Temperature 0.9 and JSON-schema output. Reasoning is turned off through `extra_body`, because thinking models otherwise spend their whole token budget reasoning.
- **OpenRouter specifics:**
  - The backend asks for each call's cost and adds it to `openrouter-spend.json`. At `spend_cap_usd`, it stops making paid calls.
  - It leaves out parameters a model doesn't accept, which it learns from the free `GET /models`.
  - If a model refuses to run without reasoning, it retries once with low reasoning.

## Messages between plugin and bridge

Each message is one line of JSON with a type in `t`. Unknown types and fields are ignored.

**Transport.** By default (`transport = "file"`):
- The plugin appends to `out.jsonl` in the SD card's `tomodachi-llm/` folder and reads `in.jsonl` from it. The bridge does the opposite.
- At each game start, the plugin deletes both files and starts a new `out.jsonl`. The bridge treats a new `out.jsonl` as a new session and empties `in.jsonl`.
- A bridge started mid-session follows `out.jsonl` from its end. It doesn't get a `hello`, so it sends no kept lines until the next game start.

`transport = "socket"` uses one TCP connection instead. That's slower on Ryujinx, which runs the game's socket calls one at a time (about 1 s each).

**Plugin → bridge**

| `t` | Main fields | Meaning |
|---|---|---|
| `hello` | `ver`, `lang` (e.g. `USen`), `relines_max`, `relines_bytes` | Game started; the bridge answers `welcome` and sends kept lines |
| `line` | `key`, `src` (template), `out` (shown text), `lr` (caller), `relined`, `caption`, `cut` | The game showed a line |
| `label` | `label`, `key`, `text`, `dump` (from a whole-file read) | The game looked up a message |
| `script_dump` | `prefix`, `labels` | A whole message file was read |
| `stats` | counters (events, drops, `relines` = stored/used/bad/evicted/laps, `scratch_miss`, `pings`/`pongs`/`pings_lost`) | Every 5 s |
| `ping` | `id` | Round-trip check every 5 s |
| `rx` | `what` | The plugin received a bridge message (logging) |
| `chat`, `chat_step`, `telop`, `friend_ugc`, `friend_line`, `index_label` | various | Research events (dev config only) |

**Bridge → plugin**

| `t` | Fields | Meaning |
|---|---|---|
| `welcome` | `ver`, `backend`, `model` | Reply to `hello` |
| `reline` | `key`, `text` | A generated line for that template key; `[k]` = the template's k-th tag run |
| `pong` | `id` | Reply to `ping` |

The bridge also still accepts `prefetch` / `cancel` (and answers `result` / `error`) from the first design, where the plugin asked for lines at conversation time. No plugin version sends them.

## Logs

- **Bridge session logs:** `bridge/logs/events-<time>.jsonl`. They hold every plugin event, plus the bridge's own records: `bridge_reline` (each generated line or the reason it was dropped) and `bridge_scene`.
- **Bridge window:** a summary of each line.
- **Plugin:** with `debug_log=1`, the plugin also writes to the emulator's log (`[tomodachi-llm] …`). Ryujinx keeps its logs in `~/Library/Logs/Ryujinx/` on macOS.

## Limits

- **Game version:** 1.0.4 only. Any other version leaves the game untouched.
- **What gets rewritten:** Mii speech balloons and narrator captions. Menus, item names, the news and other screens keep their text.
- **First showing:** a line plays vanilla the first time it's seen, unless it was kept from an earlier session.
- **Language:** English (`USen`) is tested. The model is told to write in the game's language, so other languages depend on the model.
