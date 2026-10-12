"""OpenRouter spend cap: the total cost of paid calls, kept in a file, with a configurable limit.

Every OpenRouter call reports its cost (`usage.cost`, US dollars; the request asks for it with `usage.include`).
If a reply has no cost, it is computed from the token counts and the model's prices (`GET /models`, free). If
neither is known, the call is counted at `UNKNOWN_COST_USD`, so the cap errs on the side of stopping early.
Before each call, the backend checks the total: at or over the cap, it refuses (SpendCapReached), and the
bridge falls back to LM Studio or the vanilla line like any other backend failure.

The file is shared by the bridge and the benchmark (`bench`), and re-read before each update, so both count
toward the same cap. Calls already in flight when the cap is reached still finish (a few cents at most).
To start counting again, raise `spend_cap_usd` or delete the file.

File: {"version": 1, "total_usd": 0.0123, "calls": 42, "since": "2026-10-11T13:50:00"}
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import threading

log = logging.getLogger("bridge")
UNKNOWN_COST_USD = 0.01


class SpendCapReached(RuntimeError):
    pass


class SpendTracker:
    def __init__(self, path: str, cap_usd: float):
        self.path = os.path.expanduser(path)
        self.cap_usd = cap_usd
        self._lock = threading.Lock()
        self._warned = False

    def _read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("total_usd"), (int, float)):
                return data
            log.warning("spend file %s has an unknown format: treating the cap as reached", self.path)
            return {"total_usd": float("inf"), "calls": 0}
        except FileNotFoundError:
            return {"version": 1, "total_usd": 0.0, "calls": 0,
                    "since": datetime.datetime.now().isoformat(timespec="seconds")}
        except (OSError, ValueError) as e:
            log.warning("spend file %s unreadable (%s): treating the cap as reached", self.path, e)
            return {"total_usd": float("inf"), "calls": 0}

    @property
    def total_usd(self) -> float:
        with self._lock:
            return float(self._read()["total_usd"])

    def check(self) -> None:
        """Raise SpendCapReached if the total is at or over the cap."""
        total = self.total_usd
        if total >= self.cap_usd:
            if not self._warned:
                log.warning("OpenRouter spend cap reached ($%.4f of $%.2f): no more paid calls (raise "
                            "spend_cap_usd or delete %s to allow more)", total, self.cap_usd, self.path)
                self._warned = True
            raise SpendCapReached(f"spend cap reached (${total:.4f} of ${self.cap_usd:.2f})")

    def add(self, cost_usd: float) -> float:
        """Count one call's cost; returns the new total."""
        with self._lock:
            data = self._read()
            data["total_usd"] = float(data["total_usd"]) + max(0.0, cost_usd)
            data["calls"] = int(data.get("calls", 0)) + 1
            data.setdefault("version", 1)
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                os.replace(tmp, self.path)
            except OSError as e:
                log.warning("spend file %s not saved (%s)", self.path, e)
            return data["total_usd"]


def call_cost(usage: dict | None, prices: dict | None) -> float:
    """The cost of one call from the reply's `usage` (and the model's per-token prices as a fallback)."""
    usage = usage or {}
    cost = usage.get("cost")
    if isinstance(cost, (int, float)):
        return float(cost)
    try:
        if prices:
            return (int(usage["prompt_tokens"]) * float(prices["prompt"])
                    + int(usage["completion_tokens"]) * float(prices["completion"]))
    except (KeyError, TypeError, ValueError):
        pass
    return UNKNOWN_COST_USD
