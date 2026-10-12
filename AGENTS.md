# AGENTS.md: orientation for agents and developers

This is **Tomodachi LLM: Living the Prompt**, an LLM dialogue mod for *Tomodachi Life: Living the Dream* (game 1.0.4, emulators only). The repo has two parts:
- a Skyline plugin (`hook/`, C++), which hooks the game's text function, reports lines and swaps in generated ones
- a Python bridge (`bridge/`), which asks a local or cloud model for new lines, checks them and sends them back

Release 0.1.0 works on Ryujinx on macOS.

Everything we learned along the way is written down here, so you can study this mod, improve it, or build your own mod for this game. This file says where to start.

## Start here, by goal

| Goal | Read, in order |
|---|---|
| **Understand this mod** | [README.md](README.md) (what it does for players) → [docs/how-it-works.md](docs/how-it-works.md) (components, mechanisms, messages) → [docs/configuration.md](docs/configuration.md) |
| **Improve this mod** | The above, then [docs/roadmap.md](docs/roadmap.md) (status, what's planned, known risks, past decisions) → [docs/setup.md](docs/setup.md) (toolchain, build, test, release) → the code (`hook/src/main.cpp`, `bridge/server.py`, `bridge/relines.py`) |
| **Build another mod for this game** | [docs/game-research.md](docs/game-research.md) (tools, file formats, code addresses, runtime behavior, emulators, plugin lessons, dead ends) → [re/](re/) (raw notes: offsets, message format, AI graphs) → `hook/` as a working Skyline plugin to copy from |
| **Study the game's internals only** | [docs/game-research.md](docs/game-research.md) → [re/offsets.md](re/offsets.md), [re/msbt.md](re/msbt.md), [re/ai-graphs.md](re/ai-graphs.md) |
| **Choose or test a model** | [docs/model-benchmarks.md](docs/model-benchmarks.md) (results and how to re-run `bridge/bench.py`) |

## Knowledge map

| File | Holds |
|---|---|
| `docs/game-research.md` | Topic-organized summary of all research: the starting point for modders |
| `docs/how-it-works.md` | This mod's design: plugin hooks, line splicing, the bridge's pipeline, messages |
| `docs/configuration.md` | Every bridge and plugin setting |
| `docs/setup.md` | Pinned tool versions, dumping the game, building, the Ghidra recipe |
| `docs/model-benchmarks.md` | Model measurements |
| `re/offsets.md` | Function addresses in game 1.0.4 with signatures and evidence; build IDs and hashes |
| `re/msbt.md` | The message format, tags, labels, attributes, where conversations live |
| `re/ai-graphs.md` | AI graph (`.ainb`) structure, BGYML enum hashing, hook candidates |
| `re/ghidra/` | Headless Ghidra scripts (fast analysis options, functions from data pointers, query tool) |
| `scripts/` | `msgindex.py` (index of all messages), `bgyml_hash.py` (enum hashes), `playreports.py` (Ryujinx play reports), `event_listen.py` (TCP event listener), `package.sh`, `start-bridge.command`, `swap-mod.sh` (switch Ryujinx between the dev and release builds) |
| `docs/roadmap.md` | Status, what's next, known risks, and the dated decisions behind the design |
| `CLAUDE.md` | Working rules the original agent followed with the owner (permissions, conventions) |

How much to trust each fact: notes mark things seen in the running game as runtime, and things read from the binary or files as static. **`[UNVERIFIED]`** marks guesses. Remove the marker only after testing, and note how it was tested.

## Rules for anyone working here

1. **No game files in the repo, ever.** That means no RomFS or ExeFS contents, no NSO/NRO/NCA/NSP/XCI from the game, no keys, firmware or saves, no extracted `.msbt`/`.sarc`/`.byml`/`.ainb`, and no ELFs converted from game code. Keep dumps outside the repo (this project uses `../dumps/<version>/`). Short quotes of game text are fine where needed to explain a format.
2. **No secrets in files.** API keys (`OPENROUTER_API_KEY`, `LMSTUDIO_API_TOKEN`) come from the environment only, never from config, tests, logs or commits.
3. **The game's own line is always the fallback.** Any failure (bridge down, timeout, bad output, unknown template) plays the vanilla line. Hooks never block the game, never do I/O on game threads, and keep their stack frames small (see game-research §7 for why).
4. **One pinned game version.** Hooks target 1.0.4 and check the code before installing. A new game version means new offsets in `re/offsets.md`.
5. **Trusted sources only.** Many fake repos pose as this game's PC port or as emulator downloads. Use the sources listed in game-research §1 and the emulators' official sites.
6. **Keep the docs true.** When you change behavior, messages or config keys, update `docs/how-it-works.md` and `docs/configuration.md`. When you learn something about the game, add it to `docs/game-research.md` (and `re/` for raw details). Plans and decisions go in `docs/roadmap.md`.

## Build and test

```sh
source scripts/dev-env.sh            # toolchain paths (devkitPro, Ghidra, tools); see docs/setup.md
make -C hook test                    # plugin host tests (no devkitPro needed)
make -C hook                         # build the plugin: hook/build/tomodachi-llm.nro
cd bridge && uv sync && uv run pytest    # bridge tests: mock backend, no network, no cost
```

Running in the game needs your own dump of game 1.0.4, Ryujinx, Skyline-LTD 1.1.0 and a model (LM Studio or OpenRouter); see docs/setup.md and the README. `scripts/package.sh` builds the release zip.

## License

The project's own code and docs are public domain ([The Unlicense](LICENSE)). Skyline-LTD and And64InlineHook keep their MIT license. Nothing of the game is included or covered.
