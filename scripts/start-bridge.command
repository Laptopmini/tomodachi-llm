#!/bin/bash
# Tomodachi LLM: start the bridge (double-click in Finder, or run in a terminal). Stop it with Ctrl-C.
cd "$(dirname "$0")/bridge" || exit 1
if ! command -v uv >/dev/null 2>&1; then
    for d in "$HOME/.local/bin" "$HOME/.cargo/bin" /opt/homebrew/bin /usr/local/bin; do
        [ -x "$d/uv" ] && PATH="$d:$PATH" && break
    done
fi
if ! command -v uv >/dev/null 2>&1; then
    echo "The bridge needs uv (a Python package manager). Install it from https://docs.astral.sh/uv/ and try again."
    read -r -p "Press Return to close."
    exit 1
fi
# A double-click doesn't load your shell profile: take the API keys from your shell if they aren't set here.
# (They stay in the environment; nothing is written to disk.)
for var in OPENROUTER_API_KEY LMSTUDIO_API_TOKEN; do
    if [ -z "${!var:-}" ] && [ -n "${SHELL:-}" ]; then
        val="$("$SHELL" -lic "printf %s \"\${$var:-}\"" 2>/dev/null | tail -n 1)"
        [ -n "$val" ] && export "$var=$val"
    fi
done
if [ ! -f config.toml ]; then
    cp config.example.toml config.toml
    echo "Created bridge/config.toml with the default settings: LM Studio with google/gemma-4-26b-a4b."
    echo "To use another model or OpenRouter, edit bridge/config.toml and restart the bridge (README: Configuration)."
fi
uv run --frozen python server.py --config config.toml
status=$?
[ $status -ne 0 ] && read -r -p "The bridge stopped with an error (see above). Press Return to close."
exit $status
