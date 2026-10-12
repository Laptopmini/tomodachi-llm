# Source this file to put the pinned toolchain on PATH:  source scripts/dev-env.sh
# Tools live outside the repo in $TOMODACHI_LLM_TOOLS (see docs/setup.md).
export TOMODACHI_LLM_TOOLS="${TOMODACHI_LLM_TOOLS:-$HOME/tools/tomodachi-llm}"
export JAVA_HOME="$TOMODACHI_LLM_TOOLS/jdk/jdk-21.0.12.1+1/Contents/Home"
export GHIDRA_INSTALL_DIR="$TOMODACHI_LLM_TOOLS/ghidra_12.1.2_PUBLIC"
export DEVKITPRO="${DEVKITPRO:-/opt/devkitpro}"
export DEVKITA64="$DEVKITPRO/devkitA64"
export TOMODACHI_LLM_DUMPS="${TOMODACHI_LLM_DUMPS:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd)/dumps}"
export PATH="$TOMODACHI_LLM_TOOLS/bin:$JAVA_HOME/bin:$GHIDRA_INSTALL_DIR/support:$DEVKITPRO/tools/bin:$DEVKITA64/bin:$PATH"
