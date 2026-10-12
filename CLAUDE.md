# CLAUDE.md — working rules for Tomodachi LLM: Living the Prompt

`AGENTS.md` is the general orientation: reading order, knowledge map, and rules for anyone. This file adds the working rules for sessions with the owner. At the start of a session, read `AGENTS.md`, `docs/roadmap.md` (status, plans, decisions) and the owner's private task list `~/Documents/tomodachi-llm-work/todo.md`.

**Where to record things as you work** (the project has no separate plan file):
- **Findings** about the game, emulators or tools go in `docs/game-research.md`, with the date. Raw detail (addresses, struct fields, file formats) goes in `re/`.
- **Status, plans and owner decisions** go in `docs/roadmap.md`. Add decisions to its Decisions table, with the date.
- **Changes to this mod's behavior, messages or config keys** go in `docs/how-it-works.md` and `docs/configuration.md`.
- **Owner tasks and the cleanup list** (things created outside the repo) go in `~/Documents/tomodachi-llm-work/todo.md`, outside the repo. It is private: never commit it, and never re-create `todo.md` or `PROJECT_PLAN.md` in the repo (both are gitignored).

Keep `[UNVERIFIED]` markers accurate: remove one only after testing, and note there how it was tested.

## Hard rules

1. **No game files in the repo, ever.** That covers RomFS, ExeFS, NSO/NRO/NCA/NSP/XCI, `main.npdm` from the game, keys, firmware, saves, extracted `.msbt`/`.msbp`/`.msbf`/`.sarc`/`.zs`/`.byml`/`.ainb`, and ELFs converted from game code. Dumps live in `../dumps/<version>/`, outside the repo. Don't copy them into the repo or run tools on them from inside it. Only original code, patches, offset tables, notes, and logs of our own play are committed.
2. **No secrets in files.** `OPENROUTER_API_KEY` and `LMSTUDIO_API_TOKEN` come from the environment only. Never write them into config, tests, fixtures, logs or commit messages. `config.toml` is gitignored; only `config.example.toml` is committed, with placeholder values.
3. **Fallback to vanilla, always.** Any failure (bridge down, timeout, malformed reply, validator reject, unknown template) plays the vanilla line. The plugin must never crash or hang the game, and never blocks a game thread: I/O happens on its worker thread, and hooks only copy data, with small stack frames.
4. **Portability: emulators only** (owner decision 2026-10-09). The mod only has to work on emulators (Ryujinx, Eden); it's a fun proof of concept, not a release for real hardware.
   - It still uses the Atmosphère layout (`exefs/` + `romfs/`).
   - Emulator-specific transports are fine, behind config. The plugin talks to the bridge through files on the emulated SD card by default (`transport=file`); TCP is optional.
   - Bridge host/port, file paths and timeouts are runtime config (the bridge config or the plugin's config file), never compile-time constants. The plugin connects to IP literals, not hostnames.
   - A behavior difference between emulators gets an entry in `docs/game-research.md` (emulator + version), and a config fix before any code fix.
5. **Version pinning.** Hooks target exactly one game version (1.0.4), recorded with the SHA-256 of `main` in `re/offsets.md`. Pin tool versions too (framework commit, devkitPro, Ghidra 12.1.2, Switch Loader, tomo, REA, Python deps via `uv.lock`) and record them in `docs/setup.md`. Never update the game during development without re-planning.
6. **Ask before you:**
   - install system packages or tools
   - download models
   - run `npx rea-agents setup` (it edits MCP config)
   - change emulator settings
   - touch the emulator or mod folders
   - make paid OpenRouter calls beyond the approved spend cap

   Free calls (e.g. `GET /models`) are fine. Standing permissions are listed below.
7. **Trust only the sources listed in `docs/game-research.md` §1** and the emulators' official sites. GitHub has many malware repos posing as this game's "PC port" or as emulator downloads.

## Standing permissions (owner)

- **2026-10-08, Mac Ryujinx only:**
  - Keep the symlink `~/Library/Application Support/Ryujinx/mods/contents/010051F0207B2000/tomodachi-llm-dev` → `~/Documents/tomodachi-llm-work/mods/tomodachi-llm-dev`, and deploy test builds into it without asking each time.
  - Read Ryujinx's logs.
  - The GDB stub was also allowed, but it crashes this Ryujinx build and is off; don't turn it on again without asking.
- **2026-10-09:** the plugin creates and writes `sdcard/tomodachi-llm/` in Ryujinx's data folder, and the bridge reads and writes `out.jsonl` / `in.jsonl` there.
- **LM Studio:** loading and unloading models that are already downloaded (e.g. `lms load … --ttl 86400`, `lms unload`).
- **OpenRouter:** a spend cap of $5 (configurable, `spend_cap_usd`), counted in `bridge/openrouter-spend.json`. The owner's own config keeps `fallback = false`, so play makes no paid calls.
- Everything else (other settings, other emulators, the Windows PC) still needs asking.

## Bridge

```sh
cd bridge
uv sync                                   # install pinned deps
cp config.example.toml config.toml        # local, gitignored
export OPENROUTER_API_KEY=...             # only for backend = "openrouter"
uv run python server.py --config config.toml        # start the bridge
uv run pytest                                       # unit tests; uses the mock backend, no network, no cost
uv run python -m bench --config config.toml --corpus 'logs/events-*.jsonl' --models <list>   # model benchmark
```

- Tests must pass without network access or API keys (mock backend).
- **Benchmarks:** run local (LM Studio) candidates **with the emulator running** when measuring play on one machine. Record the date, hardware, setup, model, quantization and runtime versions in `docs/model-benchmarks.md`.
- **LM Studio:** `~/.lmstudio/bin/lms server start` (default port 1234). Use `--bind 0.0.0.0` only when it serves another machine, with auth on. Check with `lms ps` and `curl http://127.0.0.1:1234/v1/models`.

## Plugin

- Build with devkitPro (devkitA64): `make -C hook`. The output is `hook/build/tomodachi-llm.nro`, gitignored. `make -C hook deploy` copies it into the dev mod.
- Deploying to an emulator's mod folder means touching the owner's emulator folders: ask first, unless a standing permission above covers it.
- Stage builds, session logs and third-party downloads in `~/Documents/tomodachi-llm-work/` (outside the repo). Anything you create outside the repo goes on the Cleanup list in that private `todo.md` (and, if a dev setup generally needs it, in `docs/setup.md` "Everything a dev setup creates").
- The plugin's events go to the bridge's session logs (`log_dir`). Never log API keys or full prompts containing them.
- Plugin memory comes from the game's heap, and hooks run on game threads with small stacks. Any growth in `.bss` or in a hook's stack frame needs a session through a heavy scene such as the Mii News (`docs/game-research.md` §7).

## Docs and releases

- **The docs:**
  - `AGENTS.md`: orientation
  - `docs/game-research.md`: everything learned about the game, emulators and tools
  - `docs/how-it-works.md`: this mod's design and messages
  - `docs/configuration.md`: every config key
  - `docs/setup.md`: toolchain, build, Ghidra
  - `docs/model-benchmarks.md`: measurements
  - `docs/roadmap.md`: status, plans, decisions
  - `re/`: raw notes

  Keep them true when you change things.
- **Releases:** build with `scripts/package.sh <skyline-ltd exefs folder>`. It checks the Skyline-LTD 1.1.0 hashes, runs the plugin and bridge tests, builds, and writes `dist/tomodachi-llm-<RELEASE>/` plus `dist/tomodachi-llm.zip` (gitignored). The version comes from `RELEASE`.
- The README is for players (install, configure, troubleshoot, credits). Developer docs go in the files above, not the README.

## Conventions

- Keep commits small; attribution lines as instructed by the harness.
- Dates in docs are absolute (YYYY-MM-DD).
- The README's "Recommended models" section is dated and says it is likely to be out of date.
