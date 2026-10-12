#!/bin/bash
# Switch which build of the mod Ryujinx loads, for testing a release next to the dev build.
#
#   scripts/swap-mod.sh status     # what's installed
#   scripts/swap-mod.sh release    # remove the dev mod, install dist/tomodachi-llm-<RELEASE>/tomodachi-llm
#   scripts/swap-mod.sh dev        # remove the release mod, put the dev mod back
#   scripts/swap-mod.sh none       # remove both (play the game without the mod)
#
# The dev mod is a symlink into the work folder (its files are never deleted here); the release mod is a copy, as
# a player would install it. Only folders this script recognizes as ours are removed. Refuses while Ryujinx runs.
# The bridge isn't touched: stop the dev bridge before testing a release, and run the release's own
# start-bridge.command. Both use the same SD card folder, so only one bridge may run at a time.
#
# Paths (override with environment variables):
#   RYUJINX_DIR  Ryujinx's data folder         (default: ~/Library/Application Support/Ryujinx)
#   DEV_MOD      the dev mod in the work folder (default: ~/Documents/tomodachi-llm-work/mods/tomodachi-llm-dev)
set -euo pipefail
cd "$(dirname "$0")/.."

TITLE=010051F0207B2000
RYUJINX_DIR="${RYUJINX_DIR:-$HOME/Library/Application Support/Ryujinx}"
DEV_MOD="${DEV_MOD:-$HOME/Documents/tomodachi-llm-work/mods/tomodachi-llm-dev}"
MODS="$RYUJINX_DIR/mods/contents/$TITLE"
DEV_LINK="$MODS/tomodachi-llm-dev"
REL_DIR="$MODS/tomodachi-llm"
REL_SRC="dist/tomodachi-llm-$(cat RELEASE)/tomodachi-llm"

ours() {  # a release mod folder we installed: the plugin and nothing unexpected at the top
    [ -f "$1/romfs/skyline/plugins/tomodachi-llm.nro" ] && [ -f "$1/exefs/subsdk9" ]
}

status() {
    if [ -L "$DEV_LINK" ]; then echo "dev mod:     installed ($DEV_LINK -> $(readlink "$DEV_LINK"))"; else echo "dev mod:     not installed"; fi
    if [ -d "$REL_DIR" ]; then
        if ours "$REL_DIR"; then echo "release mod: installed ($REL_DIR)"; else echo "release mod: $REL_DIR exists but doesn't look like ours"; fi
    else
        echo "release mod: not installed"
    fi
    if pgrep -f "server.py --config" >/dev/null; then echo "bridge:      running (pid $(pgrep -f "server.py --config" | tr '\n' ' '))"; else echo "bridge:      not running"; fi
}

remove_dev() { [ -L "$DEV_LINK" ] && rm "$DEV_LINK" && echo "removed the dev mod link" || true; }

remove_release() {
    [ -e "$REL_DIR" ] || return 0
    ours "$REL_DIR" || { echo "$REL_DIR doesn't look like our release mod; remove it yourself" >&2; exit 1; }
    rm -rf "$REL_DIR" && echo "removed the release mod"
}

cmd="${1:-status}"
if [ "$cmd" != status ] && pgrep -qi -x ryujinx; then
    echo "Ryujinx is running: quit it first (changing mods under a running game can break it)" >&2
    exit 1
fi
[ "$cmd" = status ] || mkdir -p "$MODS"

case "$cmd" in
    status) status ;;
    release)
        [ -d "$REL_SRC" ] || { echo "no $REL_SRC: build it with scripts/package.sh first" >&2; exit 1; }
        remove_dev
        remove_release
        cp -R "$REL_SRC" "$REL_DIR" && echo "installed the release mod ($(cat RELEASE)) at $REL_DIR"
        status ;;
    dev)
        [ -d "$DEV_MOD" ] || { echo "no dev mod at $DEV_MOD (make -C hook deploy)" >&2; exit 1; }
        remove_release
        [ -L "$DEV_LINK" ] || { ln -s "$DEV_MOD" "$DEV_LINK" && echo "linked the dev mod"; }
        status ;;
    none)
        remove_dev
        remove_release
        status ;;
    *) echo "usage: $0 status|release|dev|none" >&2; exit 2 ;;
esac
