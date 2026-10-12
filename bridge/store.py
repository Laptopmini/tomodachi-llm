"""Generated lines kept between game sessions, so a line plays generated from its first showing.

The plugin's store lives in memory and starts empty at every game start. The bridge keeps each line it generated
(per language) in one JSON file and sends the most recently used ones at hello, up to what the plugin says it can
hold (`relines_max`, plugin 0.6.0+). Each entry also keeps the vanilla line as it was shown (to write a fresh
version next time) and the last generated versions (so a refresh doesn't repeat them).

Scene lines (befriend scripts, scenes.py) are not kept: they are written for one conversation and its topic.
Narrator captions are kept with `"cap": true` and come last: they are sent after speech lines at hello and dropped
first when the file is full (chat captions rarely come back; speech lines do).

File: {"version": 1, "langs": {"USen": {"<key>": {"text", "src", "out", "hist": [...], "used": <unix time>,
"cap": true (captions only)}}}}
"""

from __future__ import annotations

import json
import logging
import os
import time

log = logging.getLogger("bridge")
VERSION = 1


class LineStore:
    def __init__(self, path: str, max_lines: int):
        self.path = os.path.expanduser(path) if path else ""
        self.max_lines = max_lines
        self.langs: dict[str, dict[str, dict]] = {}
        self.dirty = False
        if self.path:
            self.load()

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            log.warning("line store %s unreadable (%s); starting empty", self.path, e)
            return
        if not isinstance(raw, dict) or raw.get("version") != VERSION or not isinstance(raw.get("langs"), dict):
            log.warning("line store %s has an unknown format; starting empty", self.path)
            return
        for lang, lines in raw["langs"].items():
            if isinstance(lines, dict):
                self.langs[str(lang)] = {str(k): e for k, e in lines.items()
                                         if isinstance(e, dict) and isinstance(e.get("text"), str)
                                         and isinstance(e.get("src"), str)}
        log.info("line store: %s lines from %s",
                 ", ".join(f"{len(v)} {k}" for k, v in self.langs.items()) or "no", self.path)

    def lines(self, lang: str, limit: int) -> list[tuple[str, dict]]:
        """Up to `limit` lines of a language: speech lines first, then captions, each most recently used first."""
        got = sorted(self.langs.get(lang, {}).items(),
                     key=lambda kv: (bool(kv[1].get("cap")), -kv[1].get("used", 0)))
        return got[:max(0, limit)]

    def get(self, lang: str, key: str) -> dict | None:
        return self.langs.get(lang, {}).get(key)

    def put(self, lang: str, key: str, text: str, src: str, out: str, hist: list[str], caption: bool = False) -> None:
        if not self.path:
            return
        self.langs.setdefault(lang, {})[key] = {"text": text, "src": src, "out": out, "hist": list(hist),
                                                "used": time.time(), **({"cap": True} if caption else {})}
        self.dirty = True
        self._prune(lang)

    def touch(self, lang: str, key: str) -> None:
        e = self.get(lang, key)
        if e is not None:
            e["used"] = time.time()
            self.dirty = True

    def drop(self, lang: str, key: str) -> None:
        if self.langs.get(lang, {}).pop(key, None) is not None:
            self.dirty = True

    def _prune(self, lang: str) -> None:
        lines = self.langs[lang]
        if len(lines) > self.max_lines:
            # captions first, then the least recently used
            order = sorted(lines.items(), key=lambda kv: (not kv[1].get("cap"), kv[1].get("used", 0)))
            for k, _ in order[:len(lines) - self.max_lines]:
                del lines[k]

    def save(self) -> None:
        """Write the file if anything changed (atomically: a crash mid-write keeps the old file)."""
        if not self.path or not self.dirty:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": VERSION, "langs": self.langs}, f, ensure_ascii=False)
            os.replace(tmp, self.path)
            self.dirty = False
        except OSError as e:
            log.warning("line store %s not saved (%s)", self.path, e)
