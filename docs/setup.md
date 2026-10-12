# Setup and pinned versions

All tools live **outside the repo** in `~/tools/tomodachi-llm/` (`$TOMODACHI_LLM_TOOLS`). Game dumps live in `../dumps/<game version>/` (`$TOMODACHI_LLM_DUMPS`). Load the toolchain into a shell with:

```sh
source scripts/dev-env.sh
```

## Dev host (Mac)

| Tool | Pinned version | Location | Source / integrity | Status |
|---|---|---|---|---|
| tomo (tomocli) | 1.9.0 | `$TOMODACHI_LLM_TOOLS/bin/tomo` → `tomo-1.9.0/` | github.com/alexislours/tomo release `tomocli-v1.9.0`, `tomo-aarch64-apple-darwin.tar.gz` sha256 `c10aff2e…e19db` (verified) | ✅ installed 2026-10-08 |
| JDK | Temurin 21.0.12.1+1 (aarch64) | `$TOMODACHI_LLM_TOOLS/jdk/jdk-21.0.12.1+1/` | github.com/adoptium/temurin21-binaries, sha256 file verified | ✅ installed 2026-10-08 |
| Ghidra | 12.1.2 (`ghidra_12.1.2_PUBLIC_20260605.zip`) | `$TOMODACHI_LLM_TOOLS/ghidra_12.1.2_PUBLIC/` | NSA GitHub release, sha256 `b62e81a0…cf99d` (verified). Includes the `mac_arm_64` native decompiler | ✅ installed; headless import/analysis smoke-tested |
| Ghidra Switch Loader | 1.6.1-c600d05 (for Ghidra 12.1.2) | `$GHIDRA_INSTALL_DIR/Ghidra/Extensions/SwitchLoader/` | Adubbz/Ghidra-Switch-Loader release, sha256 `0bc6374c…1f477` (matches the release digest) | ✅ installed (loads in headless) |
| nx2elf | commit `a1760ba` (2026-04-09) | `$TOMODACHI_LLM_TOOLS/bin/nx2elf` (built from `$TOMODACHI_LLM_TOOLS/src/nx2elf`) | Built with Apple clang 21 + CMake 4.4.4 (`uv tool install cmake`) | ✅ built |
| REA (`rea-agents`) | 6.0.0 | run via `npx -y rea-agents@6.0.0` | npm, dist.shasum `730cb63f…a4` | ✅ `doctor --provider ghidra` healthy (12.1.2, JDK 21). ✅ `setup --client claude_code --yes` run 2026-10-08 (owner-approved): MCP server `npx -y rea-agents@6.0.0 mcp` in `~/.claude.json` (backup `~/.claude.json.rea.backup`), skill in `~/.agents/skills/reverse-engineer-anything`. Restart Claude Code to load it |
| devkitPro | devkitA64 r30-1 (GCC 16.1.0, native arm64), devkitA64-gdb 14.1-1, libnx 4.12.0-1, switch-tools 1.13.1-1 (`npdmtool`, `elf2nso`) | `/opt/devkitpro` | `devkitpro-pacman-installer.pkg` (dkp-pacman 6.0.2, github.com/devkitPro/pacman) | ✅ owner-installed 2026-10-08 |
| Skyline-LTD | v1.1.0 (released 2026-06-04) | staged in `~/Documents/tomodachi-llm-work/mods/tomodachi-llm-dev/exefs/` (`subsdk9`, `main.npdm`), seen by Ryujinx through the `tomodachi-llm-dev` symlink | github.com/tlmodding/skyline-ltd release asset `skyline.zip`, sha256 `4a6e1b46…484f` (matches GitHub's digest). `subsdk9` `1696bfec…1664`, `main.npdm` `a973e422…46e6`; the owner's copy is identical | ✅ installed 2026-10-08; loads the plugin (2026-10-08) |
| Python | 3.14.7 (system Homebrew) with `uv` 0.11.19 | | Bridge deps pinned in `bridge/uv.lock` | ✅ |
| Node | 24.14.1 (nvm) | | for `npx rea-agents` | ✅ |
| LM Studio | app 0.4.25+1, `lms` CLI commit 69d945a | `/Applications/LM Studio.app`, `~/.lmstudio/bin/lms` | Owner-installed | ✅ |

### devkitPro install (owner, needs sudo; done 2026-10-08)

devkitpro.org blocks automated requests, and the installer needs admin rights, so run this yourself:

1. Download `devkitpro-pacman-installer.pkg` from https://github.com/devkitPro/pacman/releases/tag/v6.0.2 and install it (double-click, or `sudo installer -pkg devkitpro-pacman-installer.pkg -target /`).
2. In a new terminal:
   ```sh
   sudo dkp-pacman -Sy
   sudo dkp-pacman -S switch-dev     # devkitA64, libnx, switch-tools (npdmtool, elf2nso)
   ```
3. Check it works and tell the agent the output:
   ```sh
   source scripts/dev-env.sh
   aarch64-none-elf-gcc --version | head -1
   file "$(which aarch64-none-elf-gcc)"     # arm64 = native; x86_64 = runs under Rosetta
   which npdmtool elf2nso
   dkp-pacman -Q | grep -E "devkitA64|switch-tools|libnx"
   ```
   Record the versions in the table above.

### REA registration (done 2026-10-08)

Run as `source scripts/dev-env.sh && npx -y rea-agents@6.0.0 setup --client claude_code --yes` (check first with `--dry-run`), so it records `GHIDRA_INSTALL_DIR` and `JAVA_HOME` from `scripts/dev-env.sh`. It writes the MCP registration to `~/.claude.json` (backup alongside) and installs REA's skill. Restart Claude Code afterwards.

## Game host

| Item | Value |
|---|---|
| Emulator (Windows PC) | Ryubing **Canary 1.3.279** |
| Emulator (Mac) | Ryujinx (Ryubing) **Canary 1.3.351**, `/Applications/Ryujinx.app`, data in `~/Library/Application Support/Ryujinx/`, logs in `~/Library/Logs/Ryujinx/`. Boots game 1.0.4 (2026-10-08). **Provenance unverified** (downloaded from Uptodown, a third-party mirror; archive sha256 `bcdac85194e89e6f5ad19282b91af7a5f8c8a08aa908040750b688028efa7212`); compare against the official release before relying on it |
| Eden (Windows PC) | Not installed; v0.2.1 for the planned Eden check (`docs/roadmap.md`). Eden doesn't run the game on this Mac (owner, 2026-10-08), so Eden is Windows-only for this project |
| Game | Title ID `010051F0207B2000`; **pinned to 1.0.4** (updated 2026-10-08). Hashes in `re/offsets.md` |

## Work folder

`~/Documents/tomodachi-llm-work/` (created 2026-10-08, outside the repo; see "Everything a dev setup creates" below):

- `mods/tomodachi-llm-dev/`: the dev mod (Atmosphère layout), seen by Ryujinx through a symlink in its mods folder
- `logs/`: bridge session logs (`events-*.jsonl`), benchmark results (`bench-*.json`), copies of emulator logs
- `lines.json`: the dev bridge's kept lines
- `src/skyline-ltd/`: Skyline-LTD source at the pinned commit (headers for the plugin build, its LICENSE for the release)
- `downloads/`: third-party downloads, one sub-folder each, never run from there
- `save-backups/`: copies of the game save made for testing

## Build, test, deploy, release

```sh
source scripts/dev-env.sh
make -C hook test        # plugin host tests (reline splicing, line store, ring buffer); no devkitPro needed
make -C hook             # build hook/build/tomodachi-llm.nro
make -C hook deploy      # copy it into the dev mod (also copies config.example.ini there if no config exists)
cd bridge && uv run pytest                     # bridge tests (mock backend, no network)
uv run python server.py --config config.toml   # run the dev bridge
scripts/package.sh ~/Documents/tomodachi-llm-work/downloads/skyline-ltd-1.1.0/exefs   # dist/tomodachi-llm.zip
```

Bump `kVersion` in `hook/src/main.cpp` and `VERSION` in `bridge/server.py` when their behavior changes, and `RELEASE` for a new release. The game must be restarted to load a new plugin build; the bridge only needs a restart.

## Testing in the game

Most behavior can only be checked by playing. Before a session:
- make sure the model is loaded in LM Studio (`lms ps`); load it with a long TTL, e.g. `lms load google/gemma-4-26b-a4b --ttl 86400`, because LM Studio unloads idle models
- start the bridge, and check its window says it is ready, and "Game connected" once the game runs

While playing, watch for:
- **conversations about a topic** ("Do you feel like talking about… X?") and **"help me befriend" requests**: these are rewritten as whole conversations; check they read as one dialogue and that each line fits the Mii saying it
- **introductions** ("Nice to meet you, I'm…"): the speaker must keep their own name
- **captions** ("X is staring at Y"): rewritten from their second showing
- anything that reads oddly, repeats a lot, or doesn't match what the Mii is doing
- **heavy scenes** (the Mii News) after any change to the plugin's memory or hooks

Afterwards, read the session log (`events-*.jsonl` in the bridge's `log_dir`):
- `bridge_reline` / `bridge_scene` records: each generated line, or why it was dropped
- `stats` events: `dropped` (events lost, should stay near 0 after boot), `scratch_miss` (0), `relines` = [stored, used, bad, evicted, arena laps] (bad, evicted and laps should be 0)
- `line` events with `relined: 1`: generated text actually played

Switching builds: `scripts/swap-mod.sh` (Ryujinx must be closed) installs the release build from `dist/` in place of the dev mod (`release`), puts the dev mod back (`dev`), removes both for a run without the mod (`none`), or shows what's installed (`status`). It doesn't touch the bridges: stop the dev bridge before running a release's own, since both use the same SD card folder.

Release checks worth repeating for a new release:
- a fresh install from the zip, following only its README, with the dev mod set aside
- stopping the bridge mid-session (the game keeps its own lines) and starting it again (generated lines come back)
- the bridge stopped for a whole session: the game must be unaffected

## Everything a dev setup creates

Outside the repo, this project's development created the following. Remove them when you're done:

- `~/Documents/tomodachi-llm-work/` (work folder: dev mod, logs, downloads, `lines.json`, save backups)
- `~/Documents/dumps/<version>/` (game dumps and everything derived from them, including the Ghidra project). **Contains game files: delete, never share**
- `~/tools/tomodachi-llm/` (tomo, JDK, Ghidra + Switch Loader, nx2elf; ~1.9 GB) and `~/Library/ghidra/ghidra_12.1.2_PUBLIC/` (Ghidra's preferences)
- in Ryujinx's data folder: the dev mod symlink in `mods/contents/010051F0207B2000/` and the plugin's channel folder `sdcard/tomodachi-llm/`
- REA's MCP entry in `~/.claude.json` and its skill in `~/.agents/skills/`, if REA was registered
- `uv tool install cmake` (for nx2elf) and npm/uv caches
- an OpenRouter key and LM Studio LAN serving, if set up for this

A player install is only the release folder, the mod folder in the emulator, and the emulator's `sdcard/tomodachi-llm/` folder.

## Dumping (owner, on the machine running Ryubing)

1. Right-click the game → **Extract Data → ExeFS**, then **RomFS**. Use a folder outside any git repo.
2. Put the results on the Mac at `../dumps/<version>/exefs/` and `../dumps/<version>/romfs/` (relative to this repo; i.e. `~/Documents/dumps/<version>/`).
3. The agent then records `shasum -a 256 main` in `re/offsets.md` and runs `tomo romfs extract` into `../dumps/<version>/romfs_extracted/`.

Derived files the agent creates in `../dumps/1.0.4/` (never in the repo):

| Folder | How | Contents |
|---|---|---|
| `romfs_extracted/` | `tomo romfs extract --out …/romfs_extracted …/romfs` | Every container unpacked (111,268 files, 14 GB) |
| `romfs_text/` | `tomo romfs extract --convert` on `Mals`, `ScenarioChat`, `GameData`, `AI`, `AIParam`, `System`, `Data`, `RSDB`, `MiiNewsParam`, `Yukaidi`, `Parameter`, `Lib` | MSBT/MSBP/BYML as YAML |
| `index/USen.tsv` | `python3 -I scripts/msgindex.py …/romfs_text/Mals/USen.Product.100 …/index/USen.tsv` | One row per message: file, label, attributes, text |
| `elf/` | `nx2elf <nso> --export-elf <out>` for `main`, `sdk`, `rtld` | ELFs for Ghidra / REA |
| `ghidra/tomodachi-llm-1.0.4.{gpr,rep}` | Ghidra 12.1.2 headless, three passes (below) | Analyzed `main` (NSO via Switch Loader, base `0x7100000000`) |
| `ainb_yaml/` | `tomo ainb extract <file>.ainb -o <out>.yml` | AI graphs we've read (`re/ai-graphs.md`) |

### Ghidra project for `main` (2026-10-08)

REA's own Ghidra analysis of `main.elf` times out (its tool calls stop after ~330 s), so the project is built with headless Ghidra and queried with our scripts in `re/ghidra/`. Each pass takes about 4 minutes on the M5 Pro.

```sh
source scripts/dev-env.sh
G=../dumps/1.0.4/ghidra; S=re/ghidra
# 1. import + analysis, slow optional analyzers off
analyzeHeadless $G tomodachi-llm-1.0.4 -import ../dumps/1.0.4/exefs/main -scriptPath $S -preScript TomodachiLlmFastOptions.java
# 2. functions reached only through data pointers (vtables): ~61.7k new functions
analyzeHeadless $G tomodachi-llm-1.0.4 -process main -scriptPath $S -preScript TomodachiLlmDataPtrFuncs.java
# 3. every raw BL target: ~1.8k more
python3 -I $S/a64scan.py ../dumps/1.0.4/elf/main.elf bltargets > /tmp/bltargets.txt
analyzeHeadless $G tomodachi-llm-1.0.4 -process main -scriptPath $S -preScript TomodachiLlmCreateAt.java /tmp/bltargets.txt
# queries (read-only): decomp:<addr> callers:<addr> ptrs:<addr> str:<text>
analyzeHeadless $G tomodachi-llm-1.0.4 -process main -noanalysis -readOnly -scriptPath $S -postScript TomodachiLlmQuery.java out.c decomp:0x71005abc00
```

Known gap: pass 1 turns off *Decompiler Switch Analysis*, so code reached only through jump tables (big `switch` functions) stays undisassembled. Re-enable it for a function when needed. The `Failed to find .got section` errors from the Switch Loader are harmless.

