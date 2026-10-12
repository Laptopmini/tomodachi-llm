"""Tomodachi LLM bridge: talks to the plugin, logs its events, runs generation jobs and pushes
results back (docs/how-it-works.md).

Two transports carry the same JSON lines ([server] transport):
- "file" (default): the plugin appends to <file_dir>/out.jsonl and the bridge appends to
  <file_dir>/in.jsonl, on the emulated SD card. Emulators run guest socket calls one at a time
  (~1 s each on Ryujinx 1.3.351), file I/O doesn't have that cost.
- "socket": the plugin connects over TCP to host:port.

    uv run python server.py --config config.toml
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import signal
import os
import re
import sys
import time

import config as config_mod
from backends.openai_compat import make_backend
from generator import Generator
import relines
import scenes
from store import LineStore
from protocol import MAX_LINE_BYTES, encode, parse_prefetch

log = logging.getLogger("bridge")
VERSION = "0.7.4"
REFRESH_HISTORY = 3  # earlier generated versions of a line the model is told to avoid
STORE_SAVE_S = 30.0  # how often the line store is written while lines are being generated
STALE_OUT_S = 20.0  # out.jsonl untouched this long at bridge start: the game isn't running (a running plugin writes every 5 s)
LOG_TYPES = {"label", "line", "stats", "dropped", "used", "rx", "script_dump", "index_label"}


class Session:
    """One plugin session. `write` delivers one encoded message to the plugin (transport-specific)."""

    def __init__(self, server: "Bridge", write):
        self.server = server
        self.write = write
        self.lang = "USen"
        self.jobs: dict[str, asyncio.Task] = {}
        self.write_lock = asyncio.Lock()
        self.event_log = None
        self.relined: set[str] = set()  # template keys with a generated line (or one in progress)
        self.reline_busy: set[str] = set()  # keys being generated or waiting to be pushed
        self.vanilla: dict[str, dict] = {}  # key -> first line event (vanilla text as shown)
        self.last_reline: dict[str, list[str]] = {}  # key -> last generated texts, newest last (model notation,
        # pages joined by " / "); the model is told to avoid them all, so refreshes don't alternate between two
        self.shown: dict[str, list] = {}  # key -> [line events so far, loop time of the last one]
        self.reline_slots = asyncio.Semaphore(2)
        self.library = scenes.Library()
        self.scene_conv: dict | None = None  # the befriend conversation playing now (scenes.py)
        self.topic = ""  # the last topic word seen in a Mii line ({2.0}), for scene prompts
        self.scene_covered: set[str] = set()  # scene line keys written by a scene job (no per-line relines)
        self.warm_pending: set[str] = set()  # genres whose lines 00-01 are to be written ahead

    async def send(self, obj: dict) -> None:
        async with self.write_lock:
            await self.write(encode(obj))

    def open_event_log(self) -> None:
        d = os.path.expanduser(self.server.cfg.log_dir)
        if not d:
            return
        os.makedirs(d, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.event_log = open(os.path.join(d, f"events-{stamp}.jsonl"), "a", encoding="utf-8")
        log.info("logging session events to %s", self.event_log.name)

    def record(self, line: str) -> None:
        if self.event_log:
            self.event_log.write(line.rstrip("\n") + "\n")
            self.event_log.flush()

    async def feed(self, line: str) -> None:
        """Handle one line received from the plugin."""
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            log.warning("bad json from plugin: %r", line[:120])
            return
        if isinstance(msg, dict):
            await self.handle(msg, line)

    def close(self) -> None:
        for t in self.jobs.values():
            t.cancel()
        self.server.store.save()
        if self.event_log:
            self.event_log.close()
            self.event_log = None

    async def handle(self, msg: dict, line: str) -> None:
        t = msg.get("t")
        if t in LOG_TYPES:
            self.record(line)
            if t == "label" and msg.get("key"):
                n = len(self.library.scenes)
                self.library.add_label(msg)
                if msg["key"] in self.library:  # a scene line: written per conversation, not kept (store.py)
                    self.server.store.drop(self.lang, msg["key"])
                if self.server.cfg.scenes and self.server.relines and len(self.library.scenes) > n:
                    self.warm_later({self.library.scenes[s].genre for s in list(self.library.scenes)[n:]},
                                    self.server.cfg.scene_warm_delay_s)
            if t == "script_dump":
                log.info("plugin read %s labels with prefix %s", msg.get("labels"), msg.get("prefix"))
            if t == "line" and "{2.0}" in str(msg.get("src")) and not msg.get("relined"):
                self.topic = scenes.topic_word(msg["src"], msg.get("out", "")) or self.topic
            if t == "line" and self.server.relines:
                if (self.server.cfg.scenes and msg.get("lr") == relines.BALLOON_CALLER and not msg.get("cut")
                        and msg.get("key") in self.library):
                    self.on_scene_line(msg)
                if relines.eligible(msg) and msg["key"] not in self.scene_covered:
                    self.on_speech(msg)
        elif t == "hello":
            self.record(line)
            self.lang = str(msg.get("lang") or self.lang)
            log.info("hello: plugin %s, lang %s", msg.get("ver"), self.lang)
            b = self.server.generator.backend
            await self.send({"t": "welcome", "ver": VERSION, "backend": b.name, "model": b.model})
            if self.server.relines:
                await self.push_stored(int(msg.get("relines_max") or 0), int(msg.get("relines_bytes") or 0))
        elif t == "prefetch":
            self.record(line)
            try:
                req = parse_prefetch(msg, self.lang)
            except (KeyError, ValueError, TypeError) as e:
                await self.send({"t": "error", "conv_id": str(msg.get("conv_id", "")), "reason": f"bad prefetch: {e}"})
                return
            old = self.jobs.pop(req.conv_id, None)
            if old:
                old.cancel()
            self.jobs[req.conv_id] = asyncio.create_task(self.job(req))
        elif t == "ping":
            # Answered at once: the plugin times the round trip (receive-side latency, plugin 0.1.4).
            await self.send({"t": "pong", "id": msg.get("id")})
        elif t == "cancel":
            job = self.jobs.pop(str(msg.get("conv_id")), None)
            if job:
                job.cancel()

    async def push_stored(self, room: int, text_bytes: int = 0) -> None:
        """Send the lines kept from earlier sessions (store.py), most recently used first, up to what the plugin
        can hold (`relines_max` in hello; plugins before 0.6.0 don't say, and get none). Plugin 0.6.2+ also says
        how much text it holds (`relines_bytes`): the kept lines take at most half, so lines written this session
        don't overwrite them soon. Each one is then known here as generated, so its first showing asks for a fresh
        version, like a line generated this session."""
        kept = self.server.store.lines(self.lang, room)
        stale = [k for k, e in kept if not relines.kept_line_ok(k, e["src"], e["text"])]
        for k in stale:  # written before a check that would reject it now
            self.server.store.drop(self.lang, k)
        if stale:
            log.info("dropped %d kept lines that fail the current checks", len(stale))
            kept = [(k, e) for k, e in kept if k not in stale]
        if text_bytes:
            budget, fit = text_bytes // 2, 0
            for _, e in kept:
                budget -= len(e["text"].encode("utf-8"))
                if budget < 0:
                    break
                fit += 1
            kept = kept[:fit]
        if not kept:
            return
        data = b""
        for key, e in kept:
            self.relined.add(key)
            self.vanilla[key] = {"t": "line", "lr": relines.CAPTION_CALLER if e.get("cap") else relines.BALLOON_CALLER,
                                 "key": key, "src": e["src"], "out": e.get("out", ""), "stored": True,
                                 **({"caption": 1} if e.get("cap") else {})}
            self.last_reline[key] = [h for h in e.get("hist", []) if isinstance(h, str)][-REFRESH_HISTORY:]
            data += encode({"t": "reline", "key": key, "text": e["text"]})
        async with self.write_lock:
            await self.write(data)
        log.info("sent %d kept lines (%s, room for %d lines, %d bytes)", len(kept), self.lang, room, text_bytes)
        self.record(json.dumps({"t": "bridge_stored", "lines": len(kept), "room": room, "bytes": text_bytes}))

    def on_scene_line(self, ev: dict) -> None:
        """A line of a scripted befriend conversation came up (scenes.py): follow the conversation; once few scenes
        fit what was said, write all the lines still to come in one call. (Lines 00-01 are written ahead: warm.)"""
        key, now = ev["key"], asyncio.get_running_loop().time()
        text = ev.get("out", "")

        def new_conv() -> dict:
            self.scene_conv = {"seen": [], "said": [], "t": now, "started": False, "genre": None}
            return self.scene_conv

        conv = self.scene_conv
        if conv is None or now - conv["t"] > scenes.CONV_GAP_S:
            conv = new_conv()
        conv["t"] = now
        if conv["said"] and conv["said"][-1]["key"] == key:  # next page of the same line
            conv["said"][-1]["text"] += " " + text
            return
        opening = self.library.opening.get(key)
        if opening:
            idx, genre = opening
            if conv["seen"] or (conv["said"] and conv["said"][-1]["idx"] >= idx):  # a new conversation starts
                conv = new_conv()
            conv["said"].append({"key": key, "idx": idx, "text": text})
            conv["genre"] = genre or conv["genre"]
            return
        conv["seen"].append((key, None))
        cands = self.library.candidates(conv["seen"])
        if not cands:  # doesn't continue the conversation we were following: a new one
            conv = new_conv()
            conv["seen"].append((key, None))
            cands = self.library.candidates(conv["seen"])
        if conv["genre"]:
            cands = [c for c in cands if c.genre == conv["genre"]] or cands
        idx = cands[0].positions().get(key, 0) if cands else 0
        conv["said"].append({"key": key, "idx": idx, "text": text})
        if conv["started"] or not cands or len(cands) > scenes.MAX_CANDIDATES:
            return
        conv["started"] = True
        self.jobs[f"scene-{key}"] = asyncio.create_task(self.scene_job(cands, list(conv["said"]), key))
        self.warm_later({cands[0].genre}, scenes.CONV_GAP_S)  # fresh lines 00-01 for the next conversation

    def warm_later(self, genres: set[str], delay_s: float) -> None:
        """Write lines 00-01 of these genres' scenes ahead (scenes.py), after `delay_s` without new scripts."""
        self.warm_pending |= genres
        old = self.jobs.pop("scene-warm", None)
        if old:
            old.cancel()
        self.jobs["scene-warm"] = asyncio.create_task(self.warm(delay_s))

    async def warm(self, delay_s: float) -> None:
        try:
            await asyncio.sleep(delay_s)
            while self.warm_pending:
                genre = sorted(self.warm_pending)[0]
                done: set[str] = set()
                for group in scenes.warm_groups(self.library, genre):
                    done |= await self.scene_job(group, [], f"warm-{genre}", upto=scenes.WARM_UPTO, warm=True,
                                                 skip=done)
                self.warm_pending.discard(genre)
        except asyncio.CancelledError:
            return
        finally:
            if self.jobs.get("scene-warm") is asyncio.current_task():
                self.jobs.pop("scene-warm", None)

    async def scene_job(self, cands: list, said: list[dict], key: str, upto: int | None = None,
                        warm: bool = False, skip: set[str] | None = None) -> set[str]:
        """Write the scene lines still to come (or lines up to `upto`) and push them; returns the keys pushed."""
        topic = "" if warm else self.topic
        shown = [{"key": s["key"], "idx": s["idx"], "text": re.sub(r"\{[0-9./]+\}", "", s["text"])} for s in said]
        sp = scenes.plan_scene(cands, shown, topic, self.lang, upto, skip or frozenset())
        if sp is None:
            return set()
        gen = self.server.warm_generator if warm else self.server.scene_generator
        try:
            out = await gen.run(sp.request)
        except asyncio.CancelledError:
            if warm:
                raise
            return set()
        finally:
            if not warm:
                self.jobs.pop(f"scene-{key}", None)
        texts = {ln["slot_id"]: ln["text"] for ln in out.lines}
        made = scenes.compose_scene(sp, texts, topic)
        self.record(json.dumps({"t": "bridge_scene", "scenes": [c.sid for c in cands], "topic": topic,
                                "upto": upto, "slots": len(sp.request.slots), "lines": made,
                                "backend": out.backend, "error": out.error, "rejected": out.rejected},
                               ensure_ascii=False))
        log.info("scene %s (%s)%s: %d/%d lines (+%d variants)%s",
                 "/".join(c.sid for c in cands) if len(cands) <= 2 else f"{len(cands)} scenes", topic or "?",
                 f" lines <= {upto}" if upto is not None else "", len([k for k in made if k in sp.lines]),
                 len(sp.lines), len([k for k in made if k not in sp.lines]), f" ({out.error})" if out.error else "")
        for k, text in made.items():
            self.scene_covered.add(k)
            self.relined.add(k)
            try:
                await self.send({"t": "reline", "key": k, "text": text})
            except (ConnectionError, OSError):
                break
        return set(made)

    def on_speech(self, ev: dict) -> None:
        """A Mii line (one page of it) was shown: generate a replacement the first time, and a fresh one each
        time a generated line was played (reline_refresh)."""
        key = ev["key"]
        seen = self.shown.setdefault(key, [0, 0.0])
        seen[0] += 1
        seen[1] = asyncio.get_running_loop().time()
        if not ev.get("relined"):
            if self.vanilla.get(key, {}).get("stored"):  # this session's vanilla text beats the kept one
                self.vanilla[key] = ev
            self.vanilla.setdefault(key, ev)
        else:
            self.server.store.touch(self.lang, key)
        if key in self.reline_busy or key not in self.vanilla:
            return
        if key in self.relined and not (ev.get("relined") and self.server.reline_refresh):
            return
        self.relined.add(key)
        self.reline_busy.add(key)
        self.jobs[f"reline-{key}"] = asyncio.create_task(self.reline(key, seen[0] - 1))

    async def reline(self, key: str, base: int) -> None:
        """Generate a replacement for a line; push it for the next time the game shows it (relines.py).
        `base` = page events of this key before the showing that started the job: a line with several pages
        is pushed only once all of its pages have been shown, so one showing never mixes two versions."""
        ev = self.vanilla[key]
        try:
            async with self.reline_slots:
                p = relines.plan(key, ev["src"])
                req = relines.request(p, ev.get("out", ""), self.lang, ev["src"], caption=bool(ev.get("caption")))
                if key in self.last_reline:  # a refresh: ask for something new
                    req.context["previous_versions"] = list(self.last_reline[key])
                out = await self.server.generator.run(req)
            texts = {ln["slot_id"]: ln["text"] for ln in out.lines}
            text = relines.compose(p, texts) if not out.failed else None
            reason = out.error or next(iter(out.rejected.values()), None) or (None if text else "placeholders")
            if text and len(text.encode("utf-8")) >= relines.MAX_BYTES:
                text, reason = None, "too long for the plugin"
            self.record(json.dumps({"t": "bridge_reline", "key": key, "src": ev["src"], "text": text,
                                    "backend": out.backend, "error": reason}, ensure_ascii=False))
            if text is None:
                log.info("reline %s: no line (%s)", key, reason)
                return
            hist = self.last_reline.setdefault(key, [])
            hist.append(" / ".join(texts[str(i)] for i, _ in p.slots))
            del hist[:-REFRESH_HISTORY]
            loop = asyncio.get_running_loop()
            while (self.shown[key][0] < base + len(p.pages)
                   and loop.time() - self.shown[key][1] < self.server.cfg.reline_page_wait_s):
                await asyncio.sleep(0.25)
            log.info("reline %s: %s", key, text)
            try:
                await self.send({"t": "reline", "key": key, "text": text})
            except (ConnectionError, OSError):
                pass
            if key not in self.library and key not in self.scene_covered:
                self.server.store.put(self.lang, key, text, ev["src"], ev.get("out", ""), hist,
                                      caption=bool(ev.get("caption")))
                self.server.save_store_later()
        except asyncio.CancelledError:
            return
        finally:
            self.reline_busy.discard(key)
            self.jobs.pop(f"reline-{key}", None)

    async def job(self, req) -> None:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        try:
            out = await self.server.generator.run(req)
        except asyncio.CancelledError:
            return
        finally:
            if self.jobs.get(req.conv_id) is asyncio.current_task():
                del self.jobs[req.conv_id]
        dt = loop.time() - t0
        log.info("conv %s (%s, %d slots): %s in %.2fs via %s%s%s", req.conv_id, req.kind, len(req.slots),
                 out.error or f"{len(out.lines)} ok / {len(out.failed)} failed", dt, out.backend or "-",
                 f" rejected={out.rejected}" if out.rejected else "",
                 f" after {out.attempts}" if out.attempts and not out.error else "")
        self.record(json.dumps({"t": "bridge_result", "conv_id": req.conv_id, "secs": round(dt, 3),
                                "backend": out.backend, "attempts": out.attempts,
                                "out": out.message(), "rejected": out.rejected}, ensure_ascii=False))
        try:
            await self.send(out.message())
        except (ConnectionError, OSError):
            pass


class Bridge:
    def __init__(self, cfg: config_mod.Config, backend=None):
        self.cfg = cfg
        backends = [backend] if backend else [make_backend(b) for b in cfg.backends]
        timeouts = [cfg.deadline_s] if backend else [b.timeout_s for b in cfg.backends]
        self.generator = Generator(backends, cfg.deadline_s, cfg.cache_size, timeouts)
        # Whole scenes are one bigger call: their own deadline, no cache (a scene is written fresh each time)
        self.scene_generator = Generator(backends, cfg.scene_deadline_s, 0,
                                         [max(t, cfg.scene_deadline_s) for t in timeouts])
        # Lines written ahead (warm) aren't waited for: a long deadline, primary backend only (no fallback)
        self.warm_generator = Generator(backends[:1], 60.0, 0, [60.0])
        self.server: asyncio.base_events.Server | None = None
        self.store = LineStore(cfg.line_store, cfg.line_store_max)
        self.store_saver: asyncio.Task | None = None
        self.relines = cfg.relines
        self.reline_refresh = cfg.reline_refresh

    async def start(self) -> int:
        """Start the TCP server (transport "socket"); returns the bound port."""
        self.server = await asyncio.start_server(self.on_connect, self.cfg.host, self.cfg.port, limit=MAX_LINE_BYTES)
        return self.server.sockets[0].getsockname()[1]

    async def on_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        log.info("plugin connected from %s", writer.get_extra_info("peername"))

        async def write(data: bytes) -> None:
            writer.write(data)
            await writer.drain()

        session = Session(self, write)
        session.open_event_log()
        try:
            while True:
                try:
                    raw = await reader.readuntil(b"\n")
                except (asyncio.IncompleteReadError, ConnectionError):
                    break
                except asyncio.LimitOverrunError:
                    log.warning("line longer than %d bytes; closing", MAX_LINE_BYTES)
                    break
                await session.feed(raw.decode("utf-8", errors="replace"))
        finally:
            session.close()
            writer.close()
            log.info("plugin disconnected")

    async def health(self) -> list[str | None]:
        """Check each backend (free calls only). Returns, per backend, None if it's ready, else what's wrong."""
        problems: list[str | None] = []
        for i, b in enumerate(self.generator.backends):
            role = "primary" if i == 0 else "fallback"
            try:
                msg = await b.health()
                log.info("%s backend: %s", role, msg)
                problems.append(f"the model {b.model!r} isn't loaded" if "NOT listed" in msg else None)
            except Exception as e:
                log.warning("%s backend %s health check failed (%s)", role, b.name, e)
                problems.append(f"no answer from {getattr(b, 'cfg', None) and b.cfg.base_url or b.name}")
        return problems

    def save_store_later(self) -> None:
        """Write the line store within STORE_SAVE_S (one write for a burst of new lines)."""
        if self.store_saver is None or self.store_saver.done():
            self.store_saver = asyncio.create_task(self._save_store())

    async def _save_store(self) -> None:
        await asyncio.sleep(STORE_SAVE_S)
        self.store.save()

    async def close(self) -> None:
        self.store.save()
        if self.server:
            self.server.close()
            await self.server.wait_closed()


NAMES = {"lmstudio": "LM Studio", "openrouter": "OpenRouter", "mock": "no model (test mode)"}


def say(text: str) -> None:
    """A plain-language message for the player, set apart from the log lines."""
    print(f"\n{text}\n", flush=True)


def ready_banner(cfg: config_mod.Config, problems: list[str | None]) -> str:
    """What the player sees once the bridge is up: which model, what to do next, and any setup problem."""
    rule = "-" * 72
    first = cfg.backends[0]
    model = f"{first.model} ({NAMES.get(first.name, first.name)})" if first.model else NAMES.get(first.name, first.name)
    if len(cfg.backends) > 1:
        model += f"; {NAMES.get(cfg.backends[1].name, cfg.backends[1].name)} if that fails"
    lines = [rule, f" Tomodachi LLM bridge {VERSION}", f" Model: {model}", ""]
    if problems and problems[0]:
        name = NAMES.get(first.name, first.name)
        lines += [f" Not ready: {name}: {problems[0]}.",
                  f" Start {name}'s server and load the model, then restart this bridge" if first.name == "lmstudio"
                  else " Check bridge/config.toml and your API key, then restart this bridge",
                  f" (meanwhile {NAMES.get(cfg.backends[1].name, cfg.backends[1].name)} writes the lines)."
                  if len(problems) > 1 and problems[1] is None else " (the game still works meanwhile, with its own lines)."]
    else:
        lines += [" Ready. Start the game in Ryujinx and play.",
                  " Lines are rewritten from their second showing (lines kept from earlier",
                  " sessions from their first)."]
    lines += ["", " Keep this window open while you play. Press Ctrl-C to stop.", rule]
    return "\n".join(lines)


class FileTransport:
    """Follows <dir>/out.jsonl (written by the plugin) and appends replies to <dir>/in.jsonl.

    A new out.jsonl (the plugin recreates it at every game start) starts a new session; the bridge then
    recreates in.jsonl from empty. At bridge start, an existing out.jsonl is followed from its end, so an
    old session isn't replayed. The bridge never creates the directory; the plugin does, on the SD card.
    """

    def __init__(self, bridge: Bridge, directory: str, poll_s: float):
        self.bridge = bridge
        self.dir = os.path.expanduser(directory)
        self.out_path = os.path.join(self.dir, "out.jsonl")
        self.in_path = os.path.join(self.dir, "in.jsonl")
        self.poll_s = poll_s
        self.session: Session | None = None
        self.file_id = None
        self.offset = 0
        self.partial = b""

    def _write_in(self, data: bytes) -> None:
        with open(self.in_path, "ab") as f:
            f.write(data)

    async def _write(self, data: bytes) -> None:
        self._write_in(data)

    def _end_session(self) -> None:
        if self.session:
            self.session.close()
            self.session = None
            log.info("plugin session ended")
            say("The game closed. Start it again whenever you like; this window can stay open.")

    def _new_session(self) -> None:
        self._end_session()
        open(self.in_path, "wb").close()  # fresh replies file for the new session
        self.session = Session(self.bridge, self._write)
        self.session.open_event_log()
        log.info("plugin session started (%s)", self.out_path)
        say("Game connected: Miis' lines are being rewritten as you play.")

    async def step(self, first: bool = False) -> None:
        try:
            st = os.stat(self.out_path)
        except FileNotFoundError:
            if self.file_id is not None:
                self._end_session()
                self.file_id = None
            return
        fid = (st.st_dev, st.st_ino)
        if fid != self.file_id or st.st_size < self.offset:
            self.file_id = fid
            self.partial = b""
            if first and time.time() - st.st_mtime > STALE_OUT_S:
                self.offset = st.st_size  # a finished session's file: the game isn't running; wait for a new one
                log.info("%s is from an earlier game session; waiting for the game", self.out_path)
            elif first:
                self.offset = st.st_size  # bridge (re)started mid-session: don't replay
                log.info("following existing %s from byte %d", self.out_path, self.offset)
                say("Reconnected to the game that's already running.")
                self.session = Session(self.bridge, self._write)
                self.session.open_event_log()
            else:
                self.offset = 0
                self._new_session()
        if st.st_size <= self.offset:
            return
        with open(self.out_path, "rb") as f:
            f.seek(self.offset)
            data = f.read(st.st_size - self.offset)
        self.offset += len(data)
        lines = (self.partial + data).split(b"\n")
        self.partial = lines.pop()
        if len(self.partial) > MAX_LINE_BYTES:
            log.warning("line longer than %d bytes; dropped", MAX_LINE_BYTES)
            self.partial = b""
        for raw in lines:
            if raw and self.session:
                await self.session.feed(raw.decode("utf-8", errors="replace"))

    async def run(self) -> None:
        log.info("file transport: waiting for the plugin in %s", self.dir)
        await self.step(first=True)
        while True:
            await asyncio.sleep(self.poll_s)
            await self.step()


async def amain(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = config_mod.load(a.config)
    bridge = Bridge(cfg)
    backends = " -> ".join(b.name for b in cfg.backends)
    if cfg.transport == "socket":
        port = await bridge.start()
        log.info("bridge %s listening on %s:%d (backends: %s)", VERSION, cfg.host, port, backends)
        runner = asyncio.Event().wait()
    else:
        log.info("bridge %s using files in %s (backends: %s)", VERSION, cfg.file_dir, backends)
        runner = FileTransport(bridge, cfg.file_dir, cfg.poll_ms / 1000).run()
    say(ready_banner(cfg, await bridge.health()))
    task = asyncio.ensure_future(runner)
    for sig in (signal.SIGINT, signal.SIGTERM):  # a background bridge ignores Ctrl-C; either one stops it cleanly
        asyncio.get_running_loop().add_signal_handler(sig, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        log.info("bridge stopping")
        say("Bridge stopped. The game keeps playing its own lines until you start it again.")
    finally:
        await bridge.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(amain(sys.argv[1:])))
    except KeyboardInterrupt:
        pass
