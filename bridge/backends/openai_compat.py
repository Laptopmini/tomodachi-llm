"""One OpenAI-compatible client for both LM Studio and OpenRouter."""

from __future__ import annotations

import asyncio

import httpx

import spend
from config import BackendConfig
from prompts.builder import RESPONSE_SCHEMA, build_messages
from protocol import GenRequest


class OpenAICompatBackend:
    def __init__(self, cfg: BackendConfig, client: httpx.AsyncClient | None = None):
        self.name = cfg.name
        self.model = cfg.model
        self.cfg = cfg
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
        if cfg.name == "openrouter":
            headers["X-Title"] = "Tomodachi LLM"
        self._client = client or httpx.AsyncClient(base_url=cfg.base_url.rstrip("/"), headers=headers,
                                                  timeout=cfg.timeout_s)
        # OpenRouter is paid: every call's cost is counted against the spend cap (spend.py).
        self.spend = spend.SpendTracker(cfg.spend_file, cfg.spend_cap_usd) if cfg.name == "openrouter" else None
        self._info: dict | None = None  # the model's entry in GET /models (OpenRouter: prices, supported parameters)
        self._reasoning_floor: dict | None = None  # set when the model refuses to run without reasoning

    async def health(self) -> str:
        """GET /models: free on both backends. Never makes a paid call."""
        r = await self._client.get("/models")
        r.raise_for_status()
        ids = [m.get("id") for m in r.json().get("data", [])]
        found = "listed" if self.model in ids else "NOT listed"
        return f"{self.name} at {self.cfg.base_url}: {len(ids)} models, configured model {self.model!r} {found}"

    async def _model_info(self) -> dict:
        """The configured model's entry from GET /models (free); {} if unknown."""
        if self._info is None:
            try:
                r = await self._client.get("/models")
                r.raise_for_status()
                self._info = next((m for m in r.json().get("data", []) if m.get("id") == self.model), {})
            except (httpx.HTTPError, ValueError):
                self._info = {}
        return self._info

    async def _model_prices(self) -> dict | None:
        """Per-token prices of the configured model; None if unknown."""
        return (await self._model_info()).get("pricing") or None

    async def generate(self, req: GenRequest) -> str:
        if self.spend is None:
            return await self._generate(req)
        self.spend.check()  # raises SpendCapReached: the bridge falls back like any backend failure
        try:
            r = await self._generate(req, raw=True)
        except (asyncio.CancelledError, httpx.TimeoutException):
            # The call may have run (and cost) although we stopped waiting: count it, pessimistically.
            self.spend.add(spend.UNKNOWN_COST_USD)
            raise
        usage = r.get("usage")
        cost = spend.call_cost(usage, None if (usage or {}).get("cost") is not None else await self._model_prices())
        self.spend.add(cost)
        return r["choices"][0]["message"]["content"]

    async def _generate(self, req: GenRequest, raw: bool = False):
        body = {
            "model": self.model,
            "messages": build_messages(req),
            "response_format": {"type": "json_schema", "json_schema": RESPONSE_SCHEMA},
            "temperature": 0.9,
            "max_tokens": 120 * len(req.slots) + 100,
        }
        body.update(self.cfg.extra_body)
        if self.spend is not None:
            body["usage"] = {"include": True}  # OpenRouter: report the call's cost in usage.cost
            # Leave out what the model doesn't accept: with provider.require_parameters, one unsupported
            # parameter (temperature on some models) leaves no provider at all (404).
            supported = (await self._model_info()).get("supported_parameters")
            if supported:
                for k in ("temperature", "reasoning"):
                    if k not in supported:
                        body.pop(k, None)
            if self._reasoning_floor is not None:
                body["reasoning"] = self._reasoning_floor
        r = await self._client.post("/chat/completions", json=body)
        if r.status_code == 400 and "reasoning is mandatory" in r.text.lower() and self._reasoning_floor is None:
            # Some models can't turn reasoning off: use as little as they allow (the refused call is free).
            self._reasoning_floor = {"effort": "low"}
            body["reasoning"] = self._reasoning_floor
            r = await self._client.post("/chat/completions", json=body)
        r.raise_for_status()
        data = r.json()
        return data if raw else data["choices"][0]["message"]["content"]

    async def aclose(self) -> None:
        await self._client.aclose()


def make_backend(cfg: BackendConfig):
    if cfg.name == "mock":
        from backends.mock import MockBackend

        return MockBackend(cfg.mode, cfg.delay_s)
    return OpenAICompatBackend(cfg)
