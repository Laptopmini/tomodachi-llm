# Modding Tomodachi Life: Living the Dream: what we learned

This is everything this project found out about the game, the emulators and the tools while building an LLM dialogue mod. It's written for anyone, human or agent, who wants to:
- study this mod
- improve it
- build a different mod for this game

It is organized by topic. Dates in parentheses (2026) say when a fact was found. Decisions and plans are in [roadmap.md](roadmap.md).

**Scope:** game version **1.0.4** (title ID `010051F0207B2000`), English (`USen`), on **Ryujinx (Ryubing) Canary 1.3.351 on macOS (Apple silicon)**, October 2026. Addresses and behavior may differ on other versions and emulators.

**Confidence labels used below:**
- **runtime:** seen while the game was running with our plugin.
- **static:** read from the binary or the game files and not tested in play.
- **[UNVERIFIED]:** a guess or inference.

**No game files are in this repo**, and none should be added: no dumps, no extracted text, no converted executables. Short quotes of game text appear only where needed to explain a format. You need your own dump of your own copy (see [setup.md](setup.md#dumping-owner-on-the-machine-running-ryubing)).

## Contents

1. [Tools and sources](#1-tools-and-sources)
2. [The game's files](#2-the-games-files)
3. [The game's code (1.0.4)](#3-the-games-code-104)
4. [How text behaves at runtime](#4-how-text-behaves-at-runtime)
5. [Conversations and captions](#5-conversations-and-captions)
6. [Emulators](#6-emulators)
7. [Writing a Skyline plugin for this game](#7-writing-a-skyline-plugin-for-this-game)
8. [Generating lines with an LLM](#8-generating-lines-with-an-llm)
9. [Dead ends and pitfalls](#9-dead-ends-and-pitfalls)
10. [Open questions and ideas](#10-open-questions-and-ideas)

## 1. Tools and sources

| Tool / source | Use | Notes |
|---|---|---|
| [tlmodding](https://tlmodding.com/) ([GitHub](https://github.com/tlmodding), Discord linked from the site) | The community for this game: docs on text editing, RSTB patching, ExeFS dumping, Ghidra setup, Skyline plugins; save editor, data libraries, hex patterns | Start here |
| [Skyline-LTD](https://github.com/tlmodding/skyline-ltd) 1.1.0 | Code-mod framework already adapted to this game: replaces `subsdk9` + `main.npdm`, loads plugins (`.nro`) from `romfs/skyline/plugins/`, inline hooks (And64InlineHook), TCP logger on port 6969 | What this mod uses. exlaunch was the alternative; its main branch crashes on boot with this game's newer rtld (its issues #32/#33; fix in PR #31), so it was dropped |
| [tomo](https://github.com/alexislours/tomo) 1.9.0 | CLI for this game's files: `romfs extract` (all containers), MSBT/MSBP/BYML/AINB to YAML | Its MSBP YAML drops the first letter of some tag-parameter names (`lot` for `slot`); the MSBT text is right |
| Ghidra 12.1.2 + [Switch Loader](https://github.com/Adubbz/Ghidra-Switch-Loader) 1.6.1 | Disassembly and decompilation of `main` | Headless recipe and our scripts: [setup.md](setup.md#ghidra-project-for-main-2026-10-08), `re/ghidra/`. Takes three passes of ~4 min each; afterwards queries take seconds |
| [nx2elf](https://github.com/shuffle2/nx2elf) | NSO → ELF (for tools that don't read NSO) | Build from source |
| devkitPro (devkitA64, libnx, switch-tools) | Compiler for plugins | macOS installer needs admin rights; native arm64 |
| REA (`rea-agents`) | Agent-driven reverse engineering (MCP) | Its Ghidra analysis of `main` timed out (330 s); not usable on a binary this size. Use headless Ghidra plus small query scripts instead |
| GameBanana (game 23911) | Existing mods (ExeFS patches, text mods) | For reference |
| MSBT Editor | Has a `TomoLife_LtD` config | For hand-editing text |

**Beware of malware.** GitHub hosts many fake repos posing as this game's "PC port" or as emulator downloads. Use the official sources above and the emulators' official sites.

## 2. The game's files

### ExeFS

`main`, `rtld`, `sdk`, `main.npdm`. There are **no `subsdk0-9`**, so every subsdk slot is free (Skyline-LTD uses `subsdk9`). Hashes and the build ID of 1.0.4 are in [`re/offsets.md`](../re/offsets.md). RomFS `System/RegionLangMask.txt` ends with `main`'s build ID, so it ties a RomFS dump to its ExeFS.

### Text: MSBT (static; details in [`re/msbt.md`](../re/msbt.md))

- **Layout:**
  - One SARC per language: `Mals/<lang>.Product.100.sarc.zs`. The text is still version `100` at 1.0.4; the update didn't touch it. The version number is the 3rd line of `System/RegionLangMask.txt`.
  - The RomFS carries all 13 languages, so the language shown follows the emulator's system language, not the copy's region.
  - Inside: `project.msbp`, plus `ProgramMsg/` (87 MSBTs, dialogue), `LayoutMsg/` (119, UI), `ReplaceMsg/` (55, word lists) and `ReferenceMsg/`. About 25,700 messages per language.
  - MSBT v3, UTF-16LE, sections `LBL1 ATO1 ATR1 TSY1 TXT2`.
  - The label hash is LMS's `h = h * 0x492 + c`, modulo the slot count.
- **No `.msbf` event flows exist.** Behavior and conversations are driven by **AI graphs** (`.ainb`, 2,574 files in `Pack/AI.Product.100/AI/`) and BGYML parameter files.
- **Labels and variants:** a label's base form is the line's identity; `_V000`, `_V001`… are interchangeable variants.
- **Attributes:** every message carries about 40 **selection attributes** (ATR1): personality and mood masks, bond level, relationship, time, location, season, random weights, priorities. The game picks a line by them, and they're also ready-made context for a prompt.
- **Tags:** a tag is (group, index). The groups that matter:
  - `0` system: page break, colors, ruby
  - `2` replace: `UGCText`, the player-taught word, plus items and food
  - `5` player name
  - `6` voice: speed, pauses, emotions; `{6.11}` = Mii language
  - `8` balloon style
  - `9` animation and expression
  - `200` Mii: nickname, relation name, catchphrase wrapper `{200.8}`…`{200.9}`, pronoun words `{200.18}` and `{200.19}`
  - `201` grammar: articles, `Cap`/`Decap`, singular/plural agreement
- **Text-only mods** need no code: edit the MSBT and repack. If the file size changes, the game's resource size table needs a patch too (tlmodding's RSTB patcher).
- **Taught words** (`UGCText`, group 2, tag 0) carry genre (Person, Object, Action…), attitude and grammatical number. That's how the game writes "a garlic fry" vs "garlic fries".

### BGYML and AI graphs (static; [`re/ai-graphs.md`](../re/ai-graphs.md))

- BGYML enum values (`ScenarioChatType`, `ChatMessageLabelType`…) are stored as u32 **MurmurHash3 x86_32, seed 0** of the plain name. We found this by brute force over ten hash functions; `scripts/bgyml_hash.py` does it.
- `tomo ainb extract` turns AI graphs into YAML. Useful graphs:
  - `ScenarioChatState.module` (Mii-to-Mii chat)
  - `MiiTouchBalloonTalk.root` (a Mii talks to the player)
  - `GroupFocusLeadingChatRoot.root` ("Meet up")
- Line-producing nodes: `TalkMessageLabelAction` (79 graphs), `MiiTouchTalkAction`, `SetGroupFocusTelopSelector` (captions), `TalkMessageLabelDetailAction`, `TalkTalkMessageAction`.
- `System/AddressTable/*.atbl.byml` maps file paths to versioned names; it holds no code addresses.

### Play reports: a free activity log (runtime)

Ryujinx logs each `ProcessPlayReport` with its payload. `game_stat_miiai` lists the AI actions that ran (MurmurHash3 IDs of graph names) with durations. The `Common` block has `RegionLanguage` (1 = `USen`). Reports arrive at save or exit, so they're good for summaries, not for live triggers. `scripts/playreports.py` decodes them.

## 3. The game's code (1.0.4)

Addresses are in Ghidra's NSO layout (base `0x7100000000`); subtract the base for a module offset. The full tables, with signatures and the evidence for each, are in [`re/offsets.md`](../re/offsets.md). The binary has **no RTTI**. Each AI node class has a 3-instruction `getName()` in its vtable, and finding that method's pointer finds the vtable.

| What | Address | Status |
|---|---|---|
| `Msbt_GetTextByLabel(Msbt*, label)` → 24-byte `TextRef` via `x8` | `0x71005abc00` | **runtime: hooked since plugin 0.1.0.** Only sees some lookups: the label hash is inlined in at least 13 other functions |
| **`Text_Expand`**: template + `MessageParam` → finished UTF-16 line | `0x71001508ac` | **runtime: the central hook.** Every visible line we cared about goes through it: speech balloons, TTS input, captions, the conversation log, the news, UI. **It returns a value** (it ends by returning the result of a call to `0x71001519dc`; Ghidra shows it as `void`), and the UI text setter (`0x710045e7cc`, return address `0x710045e924`) passes it on: choice menus showed only their first option when a hook dropped it (2026-10-11) |
| `Msbt_GetIndexByLabel(header, label)` | `0x71008581f0` | runtime: hooked since 0.6.1. Mostly called by the MessageShuffler, but only 9 distinct labels went through it in a session (10-11) |
| MessageShuffler (variant pick by attributes) | `0x7101e13258`, `0x7101e13d40`, `0x7101e14b10` | static. Picks a row by attributes and reads it **by row number** (`0x7101e11e00`), with no label lookup |
| `TagProcessor` ctor / `ProcessTag` | `0x7102108b4c` / `0x710210cb64` | static. `ProcessTag` dispatches tag groups 2, 5, 200 and 201 |
| `GetRegionLanguage()` → `kLangCode[18]` | `0x710074ee0c` → `0x710319e910` | **runtime: the plugin reads the language this way** |
| Befriend conversations: pattern pick, line label builder | `0x71018c4048`, `0x7101a0a880` | static. Observation hooks installed since plugin 0.1.7 never produced an event in 59 session logs |
| `ScenarioChatSelector` (chat pick), `SetGroupFocusTelopSelector` (caption enter) | `0x710192c53c`, `0x710082f9f0` | static. Hooked through these vtable methods, they **never fired** in play (10-09). Either they're not the methods the AI runtime calls, or other classes do the work [UNVERIFIED] |
| VoiceText (ReadSpeaker) TTS engine | `0x7100f5…`–`0x7100f8…` | static. Statically linked. Some languages have no voice and fall back |
| `nn::fs::MountRom("content", …)` | `0x71008e0184` | runtime: the game's RomFS is mounted as `content:`, and emulators layer a mod's `romfs/` into it, so a plugin can ship its own files there |

**`Text_Expand` callers** (the return address tells what a line is for; runtime, 10-08 to 10-11):

| Caller | What it builds |
|---|---|
| `0x71001424b0` | **Speech-balloon text** (one call per page) |
| `0x7100163478` | The same line as **TTS input**, with VoiceText markup (`<vtml_break>`, `<vtml_sub>`…) |
| `0x7100a2d90c` | Conversation log, news broadcasts |
| `0x710045e9cc` | **Narrator captions**, but also the conversation log, result windows, dialogs and tutorials. A caption has `0x7100643428` (the caption window's text setter) on its call stack; result windows have `0x71017fa550`, confirm dialogs `0x710202235c` |
| `0x7100781c80` | Caption subject (the Mii's name), nested inside a caption's expansion |
| `0x710045e924`, `0x7100a2d864`, `0x710073c090`, `0x7100786d60` | UI: menus, shop names and prices, typed names, house names |

So one hook on `Text_Expand` that swaps the template before calling the original changes balloon, voice and log together, while the game still fills in names, articles and voices.

## 4. How text behaves at runtime

All runtime facts below are from our session logs (10-08 to 10-11).

- **Templates are expanded often.**
  - Text boxes re-expand the same line every frame, so deduplicate events: 9,119 duplicates in one 3-minute session.
  - Multi-page lines (`{0.4}`) are expanded **once per page**, 3-5 s apart, with the same template and only that page's text in the output. A swap must not change versions between pages.
- **Runtime tags differ from template tags.** `{0.4}` becomes `{0.133}` in the output. The catchphrase start `{200.8}` inserts "Totally, " plus a lowercase copy of the next letter and `{0.130}` (skip the next character), so the output reads "Totally, h{0.130}Hello…" and the screen shows "Totally, hello…". The catchphrase is applied only **sometimes**, for the same template.
- **Mii-language lines** (gibberish) start with `{6.6}{6.11}`, which makes them easy to skip. They also get the catchphrase and names.
- **Pronoun words.** `{200.19}` (ThirdPersonWord) is a word the game fits to the Mii and the template: his, him, himself, He's, He is, he has, thinks, he'd… A verb right after it is always wrong ("He's is"). `{200.18}` is a plain pronoun. `{201.3}` capitalizes what follows.
- **Food and items.** Food and item names come from game data (`{201.2}{2.13}` → "some split-pea soup"), not from the taught-word slots.
- **Player text can contain control characters.** A Mii name typed with a keyboard contained three U+0008 (backspace) characters. Sanitize names and words before using them.
- **Keys:** hashing the raw template up to its terminator, walking over tag parameters (which can contain zeros), gives the same key whether the template was read by label or passed to `Text_Expand`. 448 of 448 templates matched.
- **Most speech isn't looked up by label.** Most Mii speech is picked by the MessageShuffler by attributes, so `Msbt_GetTextByLabel` never sees it. The template text identifies the line anyway: an offline index of all messages maps template → label (`scripts/msgindex.py`).
- **Some balloons have no voice.** Other Miis' ambient balloons go through the balloon caller but get no voice or log expansion. They may be off screen [UNVERIFIED].
- **Captions** are expanded once while on screen and are **not spoken**. When the camera is on the Mii, single-Mii captions leave the subject's name out ("is making up constellations"). That's the game's own behavior (owner observation).
- **A long stall at boot.** Once, there was a 31 s gap with no events, 3 s after game start (the emulator loading). Don't treat a quiet start as a hang.

## 5. Conversations and captions

| Kind | Where | What it is |
|---|---|---|
| Mii-to-Mii chat about a word | `ChatMiiLanguage`, `EmotionTelopChat` captions, `ChatMiiLastWord*` | **Not English**: Mii-language turns, plus a narrator caption saying what it's about, plus one English closing line. Turn sequences in `ScenarioChat/ScenarioChatParam/*.bgyml` |
| **Befriend conversations** | `Trouble/TroubleFriendUgc` (347 labels) | Real English dialogue between two Miis about a taught word. Used both when a Mii asks the player for help making a friend and **when two Miis start a topic chat on their own** |
| "Meet up" (player-led) | `FocusLeading*`, `FocusTelopContent_GroupAction_FocusLeadingChat1` (245 captions) | Caption-driven |
| Introductions, accidents | `Chat/GroupSequentialChat` | Two-line scripts with about 40 reply variants picked by personality |
| Table and restaurant chats | `DemoChat/*` | Scripted English, one AI graph per scene |
| Mii talks to the player | `MiiTouch/BalloonTalk/*` | Single-speaker lines when you tap a Mii |
| Captions | `FocusTelopContent_*` (about 1,300), single-action captions (40: "X is staring at Y.") | One line, 106 characters at most |

**Befriend conversation structure** (runtime and static):
- **Opening:** `FriendUgc_Common_00..02`. These are the greeting, "Do you feel like talking about…", and the topic word. Common_02's variant gives the genre.
- **Scene:** `FriendUgc_<Person|Object|Action|Other>_<Success|Failure>_<pattern>_<NN>`, with lines alternating between the two Miis.
- **Aftermath:** `_Success_00/01` or `_Failure`, said later.
- **The outcome is decided before the conversation starts.** Success and Failure share lines 00-01 or 00-03 and split later.
- The game looks up **all** `FriendUgc_*` labels in one burst about 5 s after start (caller `0x7100399570`), so the whole script is known long before it plays.
- **Lead time:** about 26 s from the player choosing the topic to the first line that contains it.

**Chat captions rarely repeat.** Each `EmotionTelopChat`/`FocusLeadingChat1` variant was shown once in 6 sessions, while single-action captions repeat. Chat captions are picked by attributes and read by row, with no label lookup, so there's no label hook to read their file ahead.

## 6. Emulators

### Ryujinx (Ryubing) 1.3.351, macOS

- **Mods** go in `mods/contents/<title id>/<mod name>/{exefs,romfs}`. The emulator data folder is `~/Library/Application Support/Ryujinx/`, and **File → Open Ryujinx Folder** opens it.
  - New mods are enabled by default.
  - A symlinked mod folder works.
  - The mod loader can replace any of `rtld`, `main`, `subsdk0-9`, `sdk` and `main.npdm`, and also supports IPS `exefs_patches/`.
- **Load addresses were stable across launches:** rtld `0x8500000`, `main` `0x8506000`, sdk `0xd741000`, subsdk9 `0xd75f000`. Ghidra address → runtime: `addr - 0x7100000000 + 0x8506000`.
- **Sockets work, but slowly.**
  - Guest sockets use the host network stack, with no loopback filter; use IP literals, since DNS is gated by the guest internet setting.
  - **Every guest socket call took about 1 s:** `Send`, `Write`, `SendTo`, `Poll` and `Recv`, non-blocking or not.
  - Calls run **one at a time**: a second thread blocked in `Recv` starved the sender completely. The likely reason is that the game's own network thread keeps a blocking call in flight.
  - When `SO_RCVTIMEO` expires, Ryujinx returns errno 110 (ETIMEDOUT), not 11.
- **SD card file I/O is fast.**
  - `nn::fs::MountSdCard` works.
  - A write took about 3 ms, and a plugin ↔ host round trip through two files about 27 ms.
  - The SD card is `sdcard/` in the emulator's data folder.
  - That's why this mod talks over files.
- **Logging:** `svcOutputDebugString` lands in Ryujinx's log (`~/Library/Logs/Ryujinx/`) as a `KernelSvc` warning.
- **Diagnosing a hang:** at shutdown, the log lists every guest thread with its pending SVC. A thread that's missing from that list was running guest code (spinning). That's how the Mii News freeze was traced (10-11).
- **The GDB stub crashed the emulator** on every launch ("AddressAlreadyInUse" on port 55555, unhandled). Leave it off.

### Eden

- From its source (static): Eden layers `exefs/` mods like Ryujinx does, but **Airplane Mode is on by default**, and while it's on, creating a TCP socket fails. Turn it off per game if you use sockets. File transport doesn't need it.
- Eden didn't run this game on macOS (owner, 2026-10-08), so it's untested here.

## 7. Writing a Skyline plugin for this game

The working example is `hook/` (C++, devkitA64): its Makefile, linker script `plugin.ld`, `src/module.cpp` and `src/main.cpp`. The lessons:

- **Check the game version first.** Compare the first instructions of every function you hook with known bytes, and install nothing on a mismatch (`SigMatches` in `main.cpp`). The game then runs untouched on another version.
- **Hook code runs on game threads.** Never block, lock, wait or do I/O there. Copy what you need into a lock-free buffer and return. Do the I/O on your own worker thread; ours uses priority 16 on core 0, like Skyline's logger.
- **Pass the original's return value through, always.** Ghidra's `void` can be wrong: check the end of the function for a call whose result it returns. Our `Text_Expand` hook returned nothing, so the register held whatever the hook's last call left (a 1), and every choice menu (yes/no, the topic picker, Pixel Quiz) showed only its first option. It went unnoticed for days, because only the first option looked normal; buttons the game didn't fill show the layout's placeholder text (`%%%%`). Declare every hook and original as returning `u64` and return the original's value.
- **Keep hook stack frames small.** Some game threads have small stacks. Our `Text_Expand` hook had about 2.9 KB of local buffers, and the game froze while loading one specific Mii News report; with no other change, a 176-byte frame fixed it. Take large buffers from a preallocated pool (`Scratch` in `main.cpp`), and put work in `noinline` helpers that return before you call the original function.
- **Keep memory small.** Skyline allocates the plugin's `.bss` from **the game's heap**. Ours stays around 1.2 MB, and 1.8 MB has run fine. (A 3.3 MB build froze at the same Mii News report, but so did a small one, so memory wasn't proven to be the cause; treat growth as risky anyway.)
- **And64InlineHook** overwrites the first 4-5 instructions of a hooked function, or 1 for a near branch. Check that no branch inside the function targets them.
- **Live alongside other mods.** Skyline-LTD replaces `subsdk9` and `main.npdm`, so only one Skyline install can exist. Shipping your code as a Skyline plugin (an `.nro` in `romfs/skyline/plugins/`), rather than your own subsdk, lets it coexist with other community plugins.
- **Ship files with the mod.** Put them in `romfs/` and read them as `content:/…`; that's how our config is read.
- **Talk to the host through the SD card.** `MountSdCard`, then plain `CreateFile`/`OpenFile`/`WriteFile`/`ReadFile`, appending JSON lines.
- **Check your imports.** Before deploying, check every symbol your NRO imports against the strings of `sdk`/`main`/`subsdk9`. A missing import fails at load.
- **Read config generously.** A config file outgrew a 1 KB read buffer once and its new keys were silently ignored.
- **Watch your buffers.** Count drops and buffer misses in a periodic `stats` event; it caught several problems early.
- **Test on the host.** Pure helpers (template splicing, ring buffer, line store) are host-tested with plain `c++` (`make -C hook test`), with no Switch toolchain needed.

## 8. Generating lines with an LLM

How this mod does it is in [how-it-works.md](how-it-works.md); the measurements are in [model-benchmarks.md](model-benchmarks.md). What carries over to other projects:

- **Don't make the game wait.** Generating takes about 0.5-1 s per line on a good local or cloud model, and 3-15 s for a whole conversation. "Learn as you go" never waits:
  - rewrite a line after its first showing, play it the next time
  - keep rewritten lines between sessions
  - write scripted conversations ahead, as soon as their script is known
- **Leave the game's tags to the game.** The model writes plain text with numbered placeholders (`@1`, `@2`) where names and words go, and the plugin splices the original tags back in. Names, articles, catchphrases, voices and text-to-speech then keep working. The model has to be told to write the placeholder itself, never what it stands for.
- **Validate everything, and fall back to the game's line on any failure.** Checks that earned their place:
  - one line, no markup or emoji
  - length near the original
  - every placeholder exactly once
  - no verb right after a pronoun word
  - no lowercase start
  - the speaker keeps their own name ("I'm @2")
- **Turn thinking off** (`reasoning_effort: "none"` on LM Studio, `reasoning.effort: "none"` on OpenRouter). Thinking models otherwise spend the whole token budget reasoning and return nothing. Use JSON-schema output.
- **Refreshes need history.** With only the previous version, refreshes alternated between two lines. With the last 3, they stayed different.
- **Prompt for the game's style:** contractions, the same purpose (an instruction stays an instruction), about the same length. Generic swaps still repeat across lines ("Good morning" → "Rise and shine").
- **OpenRouter quirks:**
  - With `provider.require_parameters`, a parameter the model doesn't support, such as `temperature` on some models, leaves no provider at all (404). Check the model's `supported_parameters` in `GET /models` (free).
  - Some models refuse to run without reasoning (400); retry with `effort: "low"`.
  - Ask for `usage: {include: true}` to get each call's cost.
  - Routing options: `provider.sort` (`latency`, `throughput` or `price`), `preferred_max_latency`, the `:nitro` suffix (throughput + priority) and `:floor` (price).
- **LM Studio:** OpenAI-compatible on port 1234, with JSON-schema output through llama.cpp grammars (GGUF) or Outlines (MLX). It's weak below about 7B parameters. To serve other machines, use `lms server start --bind 0.0.0.0` with its auth token on.
- **Models (2026-10):** Gemma 4 26B A4B rewrote 40/40 lines, locally at 0.8 s and on OpenRouter at 0.5 s. Qwen 3.6 35B A3B was faster locally. See [model-benchmarks.md](model-benchmarks.md).

## 9. Dead ends and pitfalls

- **REA on `main`:** analysis timed out. Use headless Ghidra plus small query scripts.
- **The Ryujinx GDB stub** crashes the emulator, so a no-mod runtime label trace (`re/gdb/trace_labels.sh`) was never run.
- **Sockets on Ryujinx:** about 1 s per call, and calls run one at a time. A receiver thread starved the sender. Use files on the SD card.
- **AI node hooks** (`ScenarioChatSelector`, `SetGroupFocusTelopSelector`, befriend pickers) never fired through the vtable methods we found. Hook the text functions instead.
- **Reading caption files ahead by label** doesn't work: chat captions are picked by row and attributes, with no label lookup.
- **A hook that dropped its function's return value** made choice menus show one option (plugins 0.1.1-0.7.1, fixed in 0.7.2).
- **A large stack frame in a hook** froze the game during one Mii News report. It looked like a memory problem at first; a bisect (no mod: fine; text hook off: fine) found the cause.
- **A fixed-slot line store** with a direct-mapped table made lines evict each other. Use open addressing with a text arena.
- **`label` events alone** don't reveal most speech, because the shuffler reads by row.
- **Player names with control characters** got copied into prompts and output. Sanitize.

## 10. Open questions and ideas

The project's own plans are in [roadmap.md](roadmap.md). Open questions about the game:


- **Chat captions written ahead.** Find where the MessageShuffler picks a caption row, and read the whole caption file from there. The row access is inlined in several places, so there may be no single hook point.
- **Holding a line until it's ready.** The AI graphs have natural waits; for example, `MiiTouchWaitSecServiceWaitSelector` plays an idle animation before a tap-talk line. Holding briefly there would let a first showing be generated too.
- **Richer context:** a Mii's personality, mood and relationships, and each message's ATR1 attributes.
- **More scripted families:** news broadcasts (`MiiNews_*`, read by label), introductions and accidents (`GroupSequentialChat`).
- **Unknowns:**
  - which `ScenarioChatType` value maps to which param file
  - two undecoded enum hashes (`2482271722`, `669634247`)
  - what exactly shows unvoiced ambient balloons
  - whether the emulator's socket serialization also happens on Eden and on Ryujinx for Windows
- **Other emulators and platforms:** untested (Eden, Ryujinx on Windows and Linux).
- **Other languages:** the text format is the same for all 13 languages in the RomFS. The plugin reports the game's language, and the model is told to write in it, but only English was tested.
