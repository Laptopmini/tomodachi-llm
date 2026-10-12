# Roadmap and decisions

Where the project stands, what's planned, and the decisions that shaped it. Dates are 2026.

## Status

**Release 0.1.0** (plugin 0.7.1, bridge 0.7.3) works on **Ryujinx (Ryubing) Canary 1.3.351 on macOS (Apple silicon)** with game **1.0.4** in English. Confirmed in the game:
- Mii speech lines are rewritten after their first showing, including lines of several balloons. Each repeat gets a fresh version.
- Narrator captions are rewritten after their first showing.
- Generated lines are kept between sessions and play rewritten from their first showing.
- **Befriend conversations** (also used when two Miis start a topic chat on their own) are written as one dialogue: their first lines ahead of time, the rest once the conversation is known.
- Models through LM Studio or OpenRouter, with fallback between them and a spend cap.
- The game plays its own lines whenever the bridge is down, late or rejects a line.
- **Fresh install** (2026-10-11): the owner installed the release zip following only its README, with the release plugin settings, and played about 7 minutes: lines rewritten from their second showing, no problems. It led to two fixes: the mod folder now sits at the top of the package (it was inside `mod/`, which invited copying the wrong folder), and the bridge prints plain-language messages (ready, game connected, problems).
- **Local model with the game running** on the same Mac: no frame drops, lines about 50% slower ([model-benchmarks.md](model-benchmarks.md)).
- Stopping the bridge mid-session and starting it again (2026-10-11, release 0.1.0): the game kept playing its own lines while it was down and rewritten lines came back after the restart. The restarted bridge doesn't know what was written before, so each line's first showing after a restart plays the game's text (or the version the plugin already holds).

**Known issue in release 0.1.0:** choice menus (yes/no prompts, the topic picker, Pixel Quiz) show only their first option; the other buttons stay empty or show `%%%%`. The plugin's text hook didn't pass the game's return value through. Fixed in plugin 0.7.2 (2026-10-11), to be confirmed in a session and released as 0.1.1.

**Not yet verified:**
- the release plugin settings (`hook/config.release.ini`) in a long session (only ~7 minutes so far)
- the OpenRouter fallback during play (covered by tests only)
- which Mii says each line of a generated conversation on screen (the order fits the content)
- how the TTS voice pronounces generated lines (no problems noticed, not checked systematically)

## Before the first public release

- Publish the repo (push / merge) and the release zip.

What to check in each case: [setup.md](setup.md#testing-in-the-game).

## After the first release

### Chat captions written ahead

Chat captions (`EmotionTelopChat`, `FocusLeadingChat1`, about 1,300 variants) rarely repeat, so "rewrite after the first showing" seldom helps them. The plan:
- Find where the game picks the caption variant, and read the whole caption file from there.
- It needs more digging in the game code, and might not work: the game repeats that picking code in several places, so there may be no single point to hook.
- Estimate: a day or two, plus play sessions.

Background: variants are picked by their attributes and read by row number (`0x7101e11e00`), never looked up by label ([game-research.md §5](game-research.md#5-conversations-and-captions)).

### Hold a line until it's ready

Today a line plays vanilla until a generated version exists. The original design held the conversation briefly while the model wrote:
- Keep the Mii in a waiting state the game already has. Candidates in the AI graphs:
  - `MiiTouchWaitSecServiceWaitSelector` with its idle "service wait" animation, before tap-talk lines
  - the reaction beat before a chat's closing line
  - the caption wait loops
- A worker thread polls the bridge, and the per-frame check only reads a flag.
- Hold for a configurable maximum (about 2 s), then play vanilla.
- It must look natural, and leaving the scene mid-hold must be safe.

This would let first showings be generated too. None of the hold points has been tested.

### Richer context

- Feed the model more about each Mii: personality, mood, relationships, likes and dislikes. tlmodding's save editor, gamedatalib and charinfo-ex have mapped much of this.
- Feed it the line's own selection attributes (ATR1, about 40 per message).
- Keep a short memory per pair of Miis.

### More conversations

- Cupid scenes (`FriendUgc_*_Cupid_*`), which have the same structure as the befriend scenes.
- News broadcasts (`MiiNews_*`, read by label, so known ahead).
- Introductions and accidents (`Chat/GroupSequentialChat`): two-line exchanges with about 40 personality variants per reply, picked late. Mapped, but not worth it yet.

### Emulators and platforms

- **Eden** (Windows):
  - Eden didn't run the game on macOS.
  - The file transport needs the bridge to see Eden's SD card folder (`<eden data>/sdmc/`).
  - The optional TCP transport needs **Airplane Mode off**; Eden turns it on by default.
  - Mods go in `<eden data>/load/010051F0207B2000/<mod>/` and are enabled under *Configure Game → Add-Ons*.
  - "Eden supported" means the identical build passes a play session (lines rewritten, fallback with the bridge stopped). If it fails, document the gap rather than fork the code.
- **Ryujinx on Windows and Linux:** should work with a different `file_dir`, but untested. Also unknown: whether their socket calls are as slow as on macOS.
- **Split setup:** the game on one PC and LM Studio on another machine on the LAN. That needs LM Studio serving on the network with its auth token.
- **Real Switch hardware:** out of scope. The file transport needs the bridge to reach the SD card.

### Models

- Re-run the benchmark as new models come out ([model-benchmarks.md](model-benchmarks.md#re-running)).
- Mistral Small (provider errors) should be measured again.
- Candidates from the original desk research (2026-10-08), never measured:
  - **local:** Nemotron 3.5 Lightning 30B, LFM2-24B-A2B, Gemma 4 E4B (for low memory)
  - **cloud:** Mercury 2.5 (inception), Nemotron 3.5 Lightning, GPT-6 Luna
- Speculative decoding (draft models) in LM Studio may speed up local models; unmeasured.

### Smaller ideas

- Skip unvoiced ambient balloons (other Miis' thought balloons) to save model calls. They're told apart by having no voice expansion.
- Generic swaps repeat across lines ("Good morning" → "Rise and shine"): give the model a list of recent outputs.
- Read the taught word's grammatical number (singular/plural) and pass it to the model.

## Known risks and limits

- **Game updates:** offsets are pinned to 1.0.4. On any other version the plugin installs nothing and the game runs untouched. Supporting a new version means redoing the offsets in `re/offsets.md`.
- **Plugin memory and stack:** the plugin's memory comes from the game's heap, and its hooks run on game threads with small stacks. Growth needs a session through heavy scenes such as the Mii News. See [game-research.md §7](game-research.md#7-writing-a-skyline-plugin-for-this-game).
- **Local model and emulator on one machine:** measured on a 48 GB M5 Pro (2026-10-11): no frame drops, lines about 50% slower, 14-16% memory left with Gemma Q8. Machines with less memory need a smaller model, a second machine, or the cloud.
- **Model recommendations go stale:** they're dated, and the benchmark can be re-run.
- **Other languages:** the model is told to write in the game's language; quality depends on the model, and only English is tested.
- **Content:** players can teach the game any word. A cloud provider's content policy could refuse a line, in which case the game's own line plays.

## Decisions

| Date | Decision | Why |
|---|---|---|
| 10-08 | Targets: the real sentences Miis say. First chat captions, closing lines after a chat, and lines when you tap a Mii; scripted scenes later. **Mii-language gibberish stays** | It's part of the game's feel. The original idea ("talk about garlic fries") turned out to be gibberish plus a caption, not English dialogue |
| 10-08 | The user picks the model service: LM Studio (local) or OpenRouter (cloud). With both set up, the second is the fallback | Free local play, or no hardware needed; keys stay on the host, never in the mod |
| 10-08 | Send the game's language with every request | Players in other languages get lines in their language |
| 10-08 | **Skyline-LTD** as the code-mod framework (over exlaunch) | Already adapted to this game, loads plugins next to community ones. exlaunch's main branch crashes on this game's newer rtld |
| 10-08 | Pin game 1.0.4 and every tool version | Hooks are version-specific |
| 10-09 | **Emulators only**: "a fun toy and proof of concept" | Removes real-hardware constraints |
| 10-09 | **Files on the emulated SD card** as the default plugin ↔ bridge channel; TCP optional | Ryujinx runs guest socket calls one at a time, about 1 s each; files take milliseconds |
| 10-09 | **"Learn as you go"** instead of asking the model when a conversation starts and holding until the answer arrives | No usable "conversation starts" hooks fired, most lines are picked late by attributes, and lines repeat a lot. Rewriting after the first showing needs one hook and never waits |
| 10-10 | Whole conversations written in one call (befriend scripts) | Lines of one conversation should fit together; the scripts are known long before they play |
| 10-11 | First release: Ryujinx on macOS, LM Studio and OpenRouter. Holding lines and Eden come after | Ship what works |
| 10-11 | Narrator captions: rewrite after the first showing (option b); chat captions written ahead after the release | Works with the existing design; reading caption files ahead needs more reverse engineering |
| 10-11 | OpenRouter spend cap, configurable, default $5 | Protect players' wallets |
| 10-11 | The live test of the OpenRouter fallback was skipped | Covered by tests; the owner chose not to spend on it |
| 10-11 | Single-Mii captions that miss their subject's name are the game's own behavior | The owner saw them before the mod rewrote anything; the name is left out when the camera is on that Mii |
| 10-11 | Renamed to **Tomodachi LLM: Living the Prompt** (`tomodachi-llm`) | Owner's choice |
| 10-11 | Public domain (The Unlicense) | Giving the work back to the community |
| 10-11 | The working notes were organized into these docs | One place per kind of information, written for readers rather than as a diary |
