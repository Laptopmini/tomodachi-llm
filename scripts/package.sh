#!/bin/bash
# Build the release folder and zip: dist/tomodachi-llm-<version>/ and dist/tomodachi-llm.zip (gitignored).
# The zip name is fixed so the README's "download the latest tomodachi-llm.zip" holds for every release.
#   scripts/package.sh <skyline-ltd release exefs dir>
# The Skyline-LTD exefs (subsdk9, main.npdm) comes from its GitHub release (docs/setup.md: v1.1.0, hashes there);
# it is MIT-licensed and shipped with its license. No game files are included.
set -euo pipefail
cd "$(dirname "$0")/.."
SKY="${1:?usage: scripts/package.sh <skyline-ltd exefs dir>}"
VERSION="$(cat RELEASE)"
OUT="dist/tomodachi-llm-$VERSION"
SKY_SUBSDK=1696bfec5cd19ebdc5eda762d594f03a42217b9a8b2a393cb0b7d6739ab81664
SKY_NPDM=a973e4228d6c613fa83bc7c94df0f41cba1bacbe6d115a41f5c33a241b7d46e6
[ "$(shasum -a 256 "$SKY/subsdk9" | cut -d' ' -f1)" = "$SKY_SUBSDK" ] || { echo "subsdk9 is not Skyline-LTD 1.1.0"; exit 1; }
[ "$(shasum -a 256 "$SKY/main.npdm" | cut -d' ' -f1)" = "$SKY_NPDM" ] || { echo "main.npdm is not Skyline-LTD 1.1.0"; exit 1; }

source scripts/dev-env.sh >/dev/null
make -C hook test >/dev/null
make -C hook >/dev/null
(cd bridge && uv run pytest -q >/dev/null) || { echo "bridge tests failed"; exit 1; }

ZIP="dist/tomodachi-llm.zip"
rm -rf "$OUT" "$ZIP" "$OUT.zip"
MOD="$OUT/tomodachi-llm"  # the mod folder players copy into the emulator; the name Ryujinx shows
mkdir -p "$MOD/exefs" "$MOD/romfs/skyline/plugins" "$MOD/romfs/tomodachi-llm" "$OUT/bridge" "$OUT/licenses"
cp "$SKY/subsdk9" "$SKY/main.npdm" "$MOD/exefs/"
cp hook/build/tomodachi-llm.nro "$MOD/romfs/skyline/plugins/"
cp hook/config.release.ini "$MOD/romfs/tomodachi-llm/config.ini"
git ls-files bridge | grep -v '^bridge/tests/' | while read -r f; do
    mkdir -p "$OUT/$(dirname "$f")"; cp "$f" "$OUT/$f"
done
cp scripts/start-bridge.command "$OUT/"
cp README.md LICENSE "$OUT/"
mkdir -p "$OUT/docs"
cp docs/how-it-works.md docs/configuration.md docs/model-benchmarks.md "$OUT/docs/"
cp "$HOME/Documents/tomodachi-llm-work/src/skyline-ltd/LICENSE" "$OUT/licenses/skyline-ltd.txt"
(cd dist && zip -qr "tomodachi-llm.zip" "tomodachi-llm-$VERSION")
echo "$ZIP ($VERSION)"
find "$OUT" -type f | sort | sed "s|^$OUT/|  |"
