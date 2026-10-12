"""Wire messages (docs/how-it-works.md): newline-delimited JSON, over files on the SD card or TCP."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from validator import sanitize_input

MAX_LINE_BYTES = 8 * 1024


@dataclass
class Slot:
    slot_id: str
    template: str
    max_chars: int = 80
    needs_word: bool = False
    label: str = ""
    keep: list[str] = field(default_factory=list)  # placeholders the line must contain (relines: "@2", ...)
    placeholders: dict[str, str] = field(default_factory=dict)  # what each placeholder stands for (scenes)


@dataclass
class GenRequest:
    conv_id: str
    kind: str
    lang: str
    context: dict[str, Any] = field(default_factory=dict)
    slots: list[Slot] = field(default_factory=list)

    @property
    def word(self) -> str | None:
        w = self.context.get("word")
        return w.get("text") if isinstance(w, dict) else None

    def cache_key(self) -> tuple:
        return (self.kind, self.lang, json.dumps(self.context, sort_keys=True),
                tuple((s.slot_id, s.template, s.max_chars, s.needs_word) for s in self.slots))


def _clean_context(raw: Any) -> dict[str, Any]:
    """Sanitize every string in the context: names and words are player-entered text."""
    if isinstance(raw, dict):
        return {str(k)[:32]: _clean_context(v) for k, v in list(raw.items())[:32]}
    if isinstance(raw, list):
        return [_clean_context(v) for v in raw[:16]]
    if isinstance(raw, str):
        return sanitize_input(raw, max_len=120)
    if isinstance(raw, (int, float, bool)) or raw is None:
        return raw
    return None


def parse_prefetch(msg: dict[str, Any], default_lang: str) -> GenRequest:
    slots = []
    for s in msg.get("slots", [])[:16]:
        if not isinstance(s, dict) or "template" not in s:
            continue
        slots.append(Slot(
            slot_id=str(s.get("slot_id", len(slots))),
            template=str(s["template"])[:1000],
            max_chars=max(8, min(int(s.get("max_chars", 80)), 400)),
            needs_word=bool(s.get("needs_word", False)),
            label=str(s.get("label", ""))[:120],
        ))
    if not slots:
        raise ValueError("prefetch without slots")
    return GenRequest(
        conv_id=str(msg["conv_id"])[:64],
        kind=str(msg.get("kind", "unknown"))[:32],
        lang=str(msg.get("lang") or default_lang)[:8],
        context=_clean_context(msg.get("context", {})),
        slots=slots,
    )


def encode(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
