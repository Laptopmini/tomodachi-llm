"""Run one generation job: cache, deadline, parse, validate (CLAUDE.md rule 3: any failure → vanilla)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from cache import LRU
from protocol import GenRequest
from validator import validate_line


@dataclass
class Outcome:
    conv_id: str
    lines: list[dict[str, str]] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    error: str | None = None
    rejected: dict[str, str] = field(default_factory=dict)  # slot_id -> reason (for logs)
    cached: bool = False
    backend: str = ""  # which backend produced the lines
    attempts: list[str] = field(default_factory=list)  # "<backend>: <failure>" for failed attempts

    def message(self) -> dict:
        if self.error:
            return {"t": "error", "conv_id": self.conv_id, "reason": self.error}
        return {"t": "result", "conv_id": self.conv_id, "lines": self.lines, "failed": self.failed}


class Generator:
    """backends: tried in order (primary, then the fallback if configured). All attempts share one
    deadline; each attempt is also capped by its backend's own timeout_s."""

    MIN_ATTEMPT_S = 0.5  # don't start a fallback attempt with less time than this left

    def __init__(self, backends, deadline_s: float, cache_size: int, timeouts: list[float] | None = None):
        self.backends = list(backends) if isinstance(backends, (list, tuple)) else [backends]
        self.timeouts = timeouts or [deadline_s] * len(self.backends)
        self.deadline_s = deadline_s
        self.cache = LRU(cache_size)

    @property
    def backend(self):
        return self.backends[0]

    async def run(self, req: GenRequest) -> Outcome:
        key = req.cache_key()
        hit = self.cache.get(key)
        if hit is not None:
            return Outcome(req.conv_id, lines=list(hit), cached=True, backend="cache")
        loop = asyncio.get_running_loop()
        end = loop.time() + self.deadline_s
        attempts: list[str] = []
        for i, (backend, limit) in enumerate(zip(self.backends, self.timeouts)):
            left = end - loop.time()
            if i > 0 and left < self.MIN_ATTEMPT_S:
                break
            try:
                raw = await asyncio.wait_for(backend.generate(req), timeout=min(limit, left))
            except asyncio.TimeoutError:
                attempts.append(f"{backend.name}: timeout")
                continue
            except asyncio.CancelledError:
                raise
            except Exception as e:  # backend down, HTTP error, ...
                attempts.append(f"{backend.name}: {type(e).__name__}: {e}"[:200])
                continue
            out = self.validate(req, raw)
            if out.error:  # malformed reply: worth a try on the fallback
                attempts.append(f"{backend.name}: {out.error}")
                continue
            out.backend = backend.name
            out.attempts = attempts
            if not out.failed:
                self.cache.put(key, tuple(out.lines))
            return out
        reason = "deadline" if end - loop.time() <= 0 or (attempts and attempts[-1].endswith("timeout")) else "backend"
        detail = "; ".join(attempts)
        return Outcome(req.conv_id, error=f"{reason}: {detail}"[:300] if detail else reason, attempts=attempts)

    @staticmethod
    def validate(req: GenRequest, raw: str) -> Outcome:
        try:
            data = json.loads(raw)
            items = data["lines"]
            if not isinstance(items, list):
                raise TypeError("lines is not a list")
        except Exception as e:
            return Outcome(req.conv_id, error=f"malformed reply: {type(e).__name__}")
        by_slot = {}
        for it in items:
            if isinstance(it, dict) and "slot_id" in it:
                by_slot.setdefault(str(it["slot_id"]), it.get("text"))
        out = Outcome(req.conv_id)
        for s in req.slots:
            if s.slot_id not in by_slot:
                out.failed.append(s.slot_id)
                out.rejected[s.slot_id] = "missing"
                continue
            v = validate_line(by_slot[s.slot_id], max_chars=s.max_chars, word=req.word, needs_word=s.needs_word)
            if v.ok:
                out.lines.append({"slot_id": s.slot_id, "text": v.text})
            else:
                out.failed.append(s.slot_id)
                out.rejected[s.slot_id] = v.reason
        return out
