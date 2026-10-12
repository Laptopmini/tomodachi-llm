"""Load the bridge config (TOML). Secrets come from the environment only (CLAUDE.md rule 2)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BackendConfig:
    name: str
    base_url: str = ""
    model: str = ""
    api_key: str | None = None
    timeout_s: float = 20.0
    extra_body: dict[str, Any] = field(default_factory=dict)
    # openrouter only: paid calls stop once their total cost reaches the cap (spend.py)
    spend_cap_usd: float = 5.0
    spend_file: str = "openrouter-spend.json"  # relative paths are next to the config file
    # mock only
    mode: str = "template"
    delay_s: float = 0.0


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8765
    log_dir: str = "logs"  # relative paths (here and line_store) are next to the config file
    # "file": exchange JSON lines with the plugin through files on the emulated SD card (default; the
    # emulators run guest socket calls one at a time, ~1 s each). "socket": TCP on host:port.
    transport: str = "file"
    file_dir: str = "~/Library/Application Support/Ryujinx/sdcard/tomodachi-llm"
    poll_ms: int = 20
    relines: bool = True  # generate replacements for Mii speech seen once (relines.py)
    reline_refresh: bool = True  # after a generated line is played, generate a fresh one for next time
    scenes: bool = True  # write the rest of a befriend conversation in one call (scenes.py; plugin 0.4.0+)
    scene_deadline_s: float = 15.0
    scene_warm_delay_s: float = 5.0
    # Generated lines kept between game sessions and sent at game start (store.py); "" = don't keep them
    line_store: str = ""
    line_store_max: int = 4000
    reline_page_wait_s: float = 20.0  # multi-page lines: push once all pages were shown, or after this much quiet
    deadline_s: float = 8.0
    cache_size: int = 512
    # Tried in order: the first is the user's choice; a second entry exists only when both
    # LM Studio and OpenRouter are configured and fallback is on (docs/how-it-works.md).
    backends: list[BackendConfig] = field(default_factory=lambda: [BackendConfig(name="mock")])

    @property
    def backend(self) -> BackendConfig:
        return self.backends[0]


def load(path: str) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    cfg = from_dict(raw)
    here = os.path.dirname(os.path.abspath(path))

    def near(p: str) -> str:
        p = os.path.expanduser(p)
        return p if not p or os.path.isabs(p) else os.path.join(here, p)

    cfg.log_dir, cfg.line_store = near(cfg.log_dir), near(cfg.line_store)
    for b in cfg.backends:
        spend = os.path.expanduser(b.spend_file)
        b.spend_file = spend if os.path.isabs(spend) else os.path.join(here, spend)
    return cfg


def _backend(name: str, raw: dict[str, Any]) -> BackendConfig:
    section = raw.get(name, {})
    key_env = section.get("api_key_env")
    return BackendConfig(
        name=name,
        base_url=section.get("base_url", ""),
        model=section.get("model", ""),
        api_key=os.environ.get(key_env) if key_env else None,
        timeout_s=float(section.get("timeout_s", 20.0)),
        extra_body=dict(section.get("extra_body", {})),
        spend_cap_usd=float(section.get("spend_cap_usd", 5.0)),
        spend_file=str(section.get("spend_file", "openrouter-spend.json")),
        mode=section.get("mode", "template"),
        delay_s=float(section.get("delay_s", 0.0)),
    )


def is_provided(b: BackendConfig) -> bool:
    """LM Studio counts as provided when it has an endpoint and a model (its token is optional);
    OpenRouter only when its API key is actually present in the environment."""
    if b.name == "lmstudio":
        return bool(b.base_url and b.model)
    if b.name == "openrouter":
        return bool(b.base_url and b.model and b.api_key)
    return b.name == "mock"


def select_backends(raw: dict[str, Any]) -> list[BackendConfig]:
    """Pick the backend(s) from [generation] backend = "auto" | "lmstudio" | "openrouter" | "mock".

    - "auto": use whichever of [lmstudio] / [openrouter] is provided. If both are, LM Studio first
      and OpenRouter as the fallback.
    - "lmstudio" / "openrouter": that one first; the other is the fallback only if it is also provided.
    - fallback = false turns the fallback off. "mock" never has a fallback.
    """
    gen = raw.get("generation", {})
    choice = gen.get("backend", "auto")
    if choice not in ("auto", "mock", "lmstudio", "openrouter"):
        raise ValueError(f"unknown backend {choice!r}")
    if choice == "mock":
        return [_backend("mock", raw)]
    lm, orr = _backend("lmstudio", raw), _backend("openrouter", raw)
    if choice == "auto":
        order = [b for b in (lm, orr) if is_provided(b)]
        if not order:
            raise ValueError("backend = \"auto\" but neither [lmstudio] (base_url + model) nor [openrouter] "
                             "(model + API key in the environment) is configured")
    else:
        first, other = (lm, orr) if choice == "lmstudio" else (orr, lm)
        if not is_provided(first):
            need = "base_url and model" if choice == "lmstudio" else f"model and ${first_key(raw, choice)} in the environment"
            raise ValueError(f"backend {choice} needs {need}")
        order = [first] + ([other] if is_provided(other) else [])
    if not gen.get("fallback", True):
        order = order[:1]
    return order


def first_key(raw: dict[str, Any], name: str) -> str:
    return raw.get(name, {}).get("api_key_env") or "OPENROUTER_API_KEY"


def _transport(t: str) -> str:
    if t not in ("file", "socket"):
        raise ValueError(f"unknown transport {t!r} (expected \"file\" or \"socket\")")
    return t


def from_dict(raw: dict[str, Any]) -> Config:
    server = raw.get("server", {})
    gen = raw.get("generation", {})
    return Config(
        host=server.get("host", "127.0.0.1"),
        port=int(server.get("port", 8765)),
        log_dir=server.get("log_dir", "logs"),
        transport=_transport(server.get("transport", "file")),
        file_dir=server.get("file_dir", "~/Library/Application Support/Ryujinx/sdcard/tomodachi-llm"),
        poll_ms=int(server.get("poll_ms", 20)),
        relines=bool(gen.get("relines", True)),
        reline_refresh=bool(gen.get("reline_refresh", True)),
        reline_page_wait_s=float(gen.get("reline_page_wait_s", 20.0)),
        line_store=str(gen.get("line_store", "")),
        line_store_max=int(gen.get("line_store_max", 4000)),
        scenes=bool(gen.get("scenes", True)),
        scene_deadline_s=float(gen.get("scene_deadline_s", 15.0)),
        scene_warm_delay_s=float(gen.get("scene_warm_delay_s", 5.0)),
        deadline_s=float(gen.get("deadline_s", 8.0)),
        cache_size=int(gen.get("cache_size", 512)),
        backends=select_backends(raw),
    )
