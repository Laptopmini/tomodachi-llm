#!/bin/zsh
# Trace every MSBT label the game fetches, through Ryujinx's GDB stub (no mod needed).
#
# Usage (game already running in Ryujinx with enable_gdb_stub = true):
#   re/gdb/trace_labels.sh [port]          # default port 55555; Ctrl-C to stop
#
# Reads main's load address from the newest Ryujinx log, sets a breakpoint on
# Msbt_GetTextByLabel (re/offsets.md) that prints the label and the caller, then continues.
# Output: ~/Documents/tomodachi-llm-work/logs/labels-<timestamp>.log
# Each hit stops the guest briefly, so expect small hitches while tracing.
set -eu
port=${1:-55555}
GHIDRA_BASE=0x7100000000
MSBT_GET_TEXT_BY_LABEL=0x71005abc00   # void (TextRef* out, MsbtRef* msbt, const char* label)

log=$(ls -t ~/Library/Logs/Ryujinx/*.log | head -1)
# "Loader LoadNsos: Loading image 1 at 0x0000000008506000..." (image 0 = rtld, 1 = main, 2 = sdk)
base=$(grep -a "LoadNsos: Loading image 1 at" "$log" | tail -1 | sed -E 's/.* at (0x[0-9A-Fa-f]+).*/\1/')
[[ -n "$base" ]] || { echo "main load address not found in $log" >&2; exit 1; }
addr=$(printf '0x%x' $(( MSBT_GET_TEXT_BY_LABEL - GHIDRA_BASE + base )))

out=~/Documents/tomodachi-llm-work/logs/labels-$(date +%Y%m%d-%H%M%S).log
cmds=$(mktemp -t tomodachi-llm-gdb)
cat > "$cmds" <<EOF
set pagination off
set confirm off
set architecture aarch64
target remote 127.0.0.1:$port
break *$addr
commands
silent
printf "label=%s lr=0x%lx\n", (char*)\$x2, \$x30 - $base + $GHIDRA_BASE
continue
end
continue
EOF
echo "main base $base (from $log); breakpoint at $addr; logging to $out"
/opt/devkitpro/devkitA64/bin/aarch64-none-elf-gdb -q -batch -x "$cmds" 2>&1 \
  | while IFS= read -r line; do printf '%s %s\n' "$(date +%H:%M:%S)" "$line"; done | tee "$out"
