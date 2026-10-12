"""Sanitize player-entered inputs and validate generated lines before they reach the game.

Generated text goes into the game's text box and TTS, so it must be one plain line: no MSBT tag
syntax, no VTML/markup, no control characters, no emoji, and within the box's length limit.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Characters we refuse in generated lines: markup openers (could be read as tags/VTML) and
# characters the plugin's MSBT encoder would treat specially.
_FORBIDDEN = set("{}<>\\[]|^~`")


def sanitize_input(text: str | None, max_len: int = 64) -> str:
    """Clean player-entered text (Mii names, taught words): drop control/format characters
    (names can contain U+0008 from keyboard input), collapse whitespace, cap the length."""
    if not text:
        return ""
    out = []
    for ch in text:
        if ch.isspace():
            out.append(" ")
            continue
        if unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn"):
            continue
        out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()[:max_len]


@dataclass
class Verdict:
    ok: bool
    text: str = ""
    reason: str = ""


def validate_line(text: object, *, max_chars: int, word: str | None = None, needs_word: bool = False,
                  allow: str = "") -> Verdict:
    """`allow`: the game's own line; symbols it uses (e.g. ♪ in songs) are allowed in the new one."""
    if not isinstance(text, str):
        return Verdict(False, reason="not a string")
    t = text.strip().strip('"').strip()
    if not t:
        return Verdict(False, reason="empty")
    if "\n" in t or "\r" in t:
        return Verdict(False, reason="multiple lines")
    for ch in t:
        cat = unicodedata.category(ch)
        if cat.startswith("C"):
            return Verdict(False, reason=f"control character U+{ord(ch):04X}")
        if (ord(ch) > 0xFFFF or cat == "So") and ch not in allow:
            return Verdict(False, reason=f"symbol or emoji U+{ord(ch):04X}")
        if ch in _FORBIDDEN:
            return Verdict(False, reason=f"forbidden character {ch!r}")
    if len(t) > max_chars:
        return Verdict(False, reason=f"too long ({len(t)} > {max_chars})")
    if needs_word and word and word.casefold() not in t.casefold():
        return Verdict(False, reason="topic word missing")
    return Verdict(True, text=t)
