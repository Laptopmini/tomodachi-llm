"""Mock backend: no network, no cost. Used by tests and for fallback testing."""

from __future__ import annotations

import asyncio
import json
import re

from protocol import GenRequest

_TAG = re.compile(r"\{[^}]*\}")


class MockBackend:
    name = "mock"

    def __init__(self, mode: str = "template", delay_s: float = 0.0):
        if mode not in ("template", "fail", "malformed", "hang"):
            raise ValueError(f"unknown mock mode {mode!r}")
        self.mode = mode
        self.delay_s = delay_s
        self.model = f"mock:{mode}"
        self.calls = 0
        self.requests: list[GenRequest] = []  # every request, for tests

    async def health(self) -> str:
        return "mock backend (no network)"

    async def generate(self, req: GenRequest) -> str:
        self.calls += 1
        self.requests.append(req)
        if self.mode == "hang":
            await asyncio.Event().wait()
        await asyncio.sleep(self.delay_s)
        if self.mode == "fail":
            raise RuntimeError("mock failure")
        if self.mode == "malformed":
            return "this is not json"
        lines = []
        word = req.word or ""
        for s in req.slots:
            text = _TAG.sub("", s.template).strip() or "Hello!"
            if s.needs_word and word and word.casefold() not in text.casefold():
                text = f"{text} {word}!"
            lines.append({"slot_id": s.slot_id, "text": f"Mock: {text}"[: s.max_chars]})
        return json.dumps({"lines": lines})
