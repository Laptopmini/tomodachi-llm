# Tomodachi LLM: Living the Prompt

> Ever wish your Miis had more than just pre-written nonsense to say? Now they have an existential crisis powered by an LLM.

A fan mod for **Tomodachi Life: Living the Dream** that integrates a Large Language Model (LLM) into the game, supplying it with context about your characters and their environment, and in turn providing bespoke dialogues and captions for your gameplay. Never hear the same thing twice. Always tailored to your game.

The mod was developed as a proof of concept and exploration of LLM usages in existing video games, specificly where NPC's could be provided with new and always evolving dialogue. For Tomodachi Life, this means rewritting speech balloons, conversation events, and narrator captions; all while respecting the players choices for the given interactions.

You can run this mod for **free** using a local model (LM Studio) or cloud model (OpenRouter), and the game will default to its own content when the LLM is too slow or unavailable.

**This repo and the release contain no game files.** You need your own copy of the game.

## Usage

1. Go to Releases page and download the latest `tomodachi-llm.zip`
2. In Ryujinx, right-click the game → **Open Mods Directory** and copy the `tomodachi-llm` folder (the one with `exefs` and `romfs` inside) into it
3. Double-click `start-bridge.command` (or run it in a terminal) to initialize the bridge between the LLM and game
4. In LM Studio, load the model configured in `bridge/config.toml` or configure make sure your OpenRouter key is set
5. Play the game!

This mod is safe to use with an existing save, it does not affect the actual game mechanics, only the text content.

## Requirements

- Ryujinx (^1.3.351)
- A copy of Tomodachi Life: Living the Dream with update 1.0.4 installed
- [uv](https://docs.astral.sh/uv/)
- A model, one of:
  - **Local:** [LM Studio](https://lmstudio.ai/) with a model loaded and its server running.
  - **Cloud:** an [OpenRouter](https://openrouter.ai/) account and API key.

## Configuration

Everything is in `bridge/config.toml` (comments explain each setting; [docs/configuration.md](docs/configuration.md) describes them all). By default, it runs against `google/gemma-4-26b-a4b` using LM Studio. If you prefer to use OpenRouter, switch `backend = "openrouter"` and provide a `OPENROUTER_API_KEY` environment variable (for example `export OPENROUTER_API_KEY=…` in `~/.zshrc`; the launcher picks it up from your shell).

**Make sure to restart the bridge after making changes.**

### Recommended models

_As of 2026-10-11._ Details: [docs/model-benchmarks.md](docs/model-benchmarks.md).

| Use | Model | Accepted | Typical time per line | Cost per line |
|---|---|---|---|---|
| **Local, better quality** | `google/gemma-4-26b-a4b` (Q8_0) | 40/40 | 0.8 s | free |
| Local, faster | `qwen/qwen3.6-35b-a3b` (MLX 4-bit) | 40/40 | 0.6 s | free |
| **Cloud (OpenRouter)** | `google/gemma-4-26b-a4b-it` | 40/40 | 0.5 s | ~$0.00006 |
| Cloud, fastest | `deepseek/deepseek-v4.1-flash` | 38/40 | 0.2 s | ~$0.00008 |
| Cloud | `qwen/qwen3.8-flash` | 38/40 | 0.9 s | ~$0.00005 |
| Cloud | `openai/gpt-5.4-mini` | 40/40 | 0.9 s | ~$0.0007 |

Gemma is the default for both: the same model locally and in the cloud, so lines sound the same either way. At the default $5 spend cap, cloud Gemma writes roughly 80,000 lines. Lines a model gets wrong are rejected by the bridge and the game plays its own line, so a lower "accepted" count only means fewer rewritten lines.

## Troubleshooting

The bridge writes each session's events to `bridge/logs/`.

## Credits

### Third-party code in release(s)

- **[Skyline-LTD](https://github.com/tlmodding/skyline-ltd) 1.1.0** by the tlmodding community, a fork of the Skyline project (MIT, `licenses/skyline-ltd.txt`). It loads the plugin into the game. The plugin is built against its headers, which include the community's `nn::` SDK declarations. The plugin's startup files follow its `crt0.s` and `module.cpp`.
- **[And64InlineHook](https://github.com/Rprop/And64InlineHook)** by Rprop (MIT), included in Skyline-LTD. It installs the plugin's hooks.
- **libgcc** (GCC runtime library exception), linked into the plugin by the devkitA64 toolchain.
- **Bridge:** [Python](https://www.python.org/), [httpx](https://www.python-httpx.org/) (BSD-3-Clause), and [uv](https://docs.astral.sh/uv/) by Astral (installs and runs the bridge).

### Built using

- **Claude Opus 5.5** (Anthropic), working in [Claude Code](https://claude.com/claude-code).
- **devkitPro** (devkitA64, switch-tools): compiles the plugin.
- **[Ghidra](https://ghidra-sre.org/)** 12.1.2 (NSA): disassembly and decompilation of the game code, scripted headless (`re/ghidra/`). Runs on [Eclipse Temurin](https://adoptium.net/) JDK 21.
- **[Ghidra Switch Loader](https://github.com/Adubbz/Ghidra-Switch-Loader)** by Adubbz: loads the game's executable into Ghidra.
- **[nx2elf](https://github.com/shuffle2/nx2elf)** by shuffle2: converts the game's executable to ELF.
- **[tomo](https://github.com/alexislours/tomo)** by alexislours: unpacks the game's archives and message files (MSBT, MSBP, BYML, AINB) for study.
- **[REA](https://www.npmjs.com/package/rea-agents)** (`rea-agents` 6.0.0): set up for agent-driven reverse engineering. Its analysis timed out on this game, so most queries went through the Ghidra scripts instead.
- **pytest:** the bridge's tests.

### Reference material

- **[tlmodding](https://tlmodding.com/)** documentation and community projects for this game ([github.com/tlmodding](https://github.com/tlmodding)).
- **Ryujinx (Ryubing) and [Eden](https://eden-emu.dev/) source code:** read to confirm how they load mods and handle networking.
- **The Skyline-LTD source:** for the plugin format and the hooking library.

### Trademarks

Tomodachi Life is a trademark of its owner. This is an unofficial fan project, not affiliated with or endorsed by the game's publisher or developer. Other names belong to their owners.

## License

This project's own code and documentation are released into the public domain under [The Unlicense](LICENSE): use, copy, modify and share it for any purpose, no permission or credit needed. The third-party code shipped in the release (Skyline-LTD and And64InlineHook) stays under its own MIT license (`licenses/skyline-ltd.txt`). The license covers none of the game's own content.
