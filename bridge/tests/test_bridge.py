"""Bridge tests. Mock backend only: no network, no API keys, no cost (CLAUDE.md)."""

import asyncio
import json

import pytest

import config as config_mod
from backends.mock import MockBackend
from cache import LRU
from generator import Generator
from prompts.builder import build_messages, readable_template
from protocol import encode, parse_prefetch
from server import Bridge
from validator import sanitize_input, validate_line

PREFETCH = {
    "t": "prefetch",
    "conv_id": "c1",
    "kind": "tap_talk",
    "lang": "USen",
    "context": {"speaker": {"name": "Cortez\x08\x08\x08z"}, "word": {"text": "Cameron Diaz"}},
    "slots": [
        {"slot_id": "0", "template": "Something on your mind?", "max_chars": 80},
        {"slot_id": "1", "template": "I didn't know {200.0} was such a fan of {2.0}.", "max_chars": 80, "needs_word": True},
    ],
}


# --- validator / sanitizing -------------------------------------------------------------------

def test_sanitize_strips_control_characters_from_names():
    assert sanitize_input("Cortez\x08\x08\x08z") == "Cortezz"
    assert sanitize_input("  a\tb\n c ") == "a b c"
    assert sanitize_input(None) == ""


@pytest.mark.parametrize("text,reason", [
    ("", "empty"),
    ("two\nlines", "multiple lines"),
    ("hi {2.0}", "forbidden"),
    ("<vtml_break/>", "forbidden"),
    ("smile 😀", "emoji"),
    ("bell\x07", "control"),
    ("x" * 81, "too long"),
    (42, "not a string"),
])
def test_validator_rejects(text, reason):
    v = validate_line(text, max_chars=80)
    assert not v.ok and reason in v.reason


def test_validator_accepts_and_trims_quotes():
    v = validate_line('  "Do you dip your garlic fries in ketchup or mayo?"  ', max_chars=80)
    assert v.ok and v.text == "Do you dip your garlic fries in ketchup or mayo?"


def test_validator_requires_word_when_asked():
    assert not validate_line("Nice day!", max_chars=80, word="garlic fries", needs_word=True).ok
    assert validate_line("Garlic Fries rule!", max_chars=80, word="garlic fries", needs_word=True).ok


def test_symbols_are_allowed_only_when_the_games_line_has_them():
    assert not validate_line("♪La la la!♪", max_chars=80).ok
    assert validate_line("♪La la la!♪", max_chars=80, allow="♪Hm hmm... Yeah...♪").ok
    assert not validate_line("♪La la 😀♪", max_chars=80, allow="♪Hm hmm♪").ok  # only the symbols it has
    assert not validate_line("{La}", max_chars=80, allow="{La}").ok  # markup stays forbidden


# --- protocol / prompt --------------------------------------------------------------------------

def test_parse_prefetch_sanitizes_context_and_clamps():
    req = parse_prefetch(PREFETCH, "USen")
    assert req.context["speaker"]["name"] == "Cortezz"
    assert req.word == "Cameron Diaz"
    assert [s.slot_id for s in req.slots] == ["0", "1"]
    with pytest.raises(ValueError):
        parse_prefetch({"conv_id": "x", "slots": []}, "USen")


def test_encode_is_one_line():
    data = encode({"t": "result", "text": "a\nb"})
    assert data.endswith(b"\n") and data.count(b"\n") == 1


def test_prompt_mentions_language_and_readable_template():
    msgs = build_messages(parse_prefetch(dict(PREFETCH, lang="EUfr"), "USen"))
    assert "French" in msgs[1]["content"]
    assert readable_template("{6.6}{6.11}Hi, {200.0}! About {2.0}{200.9}.") == "Hi, [NAME]! About [TOPIC WORD]."


def test_lru_evicts_oldest():
    c = LRU(2)
    c.put("a", 1); c.put("b", 2); c.get("a"); c.put("c", 3)
    assert c.get("b") is None and c.get("a") == 1 and len(c) == 2


# --- generator ------------------------------------------------------------------------------------

def run(coro):
    return asyncio.run(coro)


def test_generator_template_mode_validates_and_caches():
    backend = MockBackend("template")
    gen = Generator(backend, deadline_s=1.0, cache_size=8)
    req = parse_prefetch(PREFETCH, "USen")
    out = run(gen.run(req))
    assert out.error is None and not out.failed
    assert {l["slot_id"] for l in out.lines} == {"0", "1"}
    assert "Cameron Diaz" in out.lines[1]["text"]
    out2 = run(gen.run(req))
    assert out2.cached and backend.calls == 1


@pytest.mark.parametrize("mode,expected", [("fail", "backend"), ("malformed", "malformed")])
def test_generator_failures_become_errors(mode, expected):
    out = run(Generator(MockBackend(mode), 1.0, 8).run(parse_prefetch(PREFETCH, "USen")))
    assert out.error and expected in out.error
    assert out.message()["t"] == "error"


def test_generator_deadline():
    out = run(Generator(MockBackend("hang"), 0.05, 8).run(parse_prefetch(PREFETCH, "USen")))
    assert out.error.startswith("deadline")


def test_generator_rejects_bad_slot_but_keeps_good_one():
    req = parse_prefetch(PREFETCH, "USen")
    raw = json.dumps({"lines": [{"slot_id": "0", "text": "Hey you!"}, {"slot_id": "1", "text": "no word here"}]})
    out = Generator.validate(req, raw)
    assert out.lines == [{"slot_id": "0", "text": "Hey you!"}]
    assert out.failed == ["1"] and "word" in out.rejected["1"]


# --- end to end over TCP ------------------------------------------------------------------------

def test_tcp_roundtrip_push_result(tmp_path):
    async def scenario():
        cfg = config_mod.from_dict({"server": {"host": "127.0.0.1", "port": 0, "log_dir": str(tmp_path)},
                                    "generation": {"backend": "mock", "deadline_s": 2.0}})
        bridge = Bridge(cfg, backend=MockBackend("template", delay_s=0.05))
        port = await bridge.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(encode({"t": "hello", "ver": "test", "lang": "USen"}))
        writer.write(encode({"t": "line", "src": "x", "out": "y"}))
        writer.write(encode({"t": "ping", "id": 7}))
        writer.write(b"not json\n")
        writer.write(encode(PREFETCH))
        await writer.drain()
        welcome = json.loads(await asyncio.wait_for(reader.readline(), 2))
        pong = json.loads(await asyncio.wait_for(reader.readline(), 2))
        assert pong == {"t": "pong", "id": 7}
        result = json.loads(await asyncio.wait_for(reader.readline(), 2))
        writer.close()
        await bridge.close()
        return welcome, result

    welcome, result = run(scenario())
    assert welcome["t"] == "welcome" and welcome["backend"] == "mock"
    assert result["t"] == "result" and result["conv_id"] == "c1" and len(result["lines"]) == 2
    logs = list(tmp_path.glob("events-*.jsonl"))
    assert logs and '"t":"line"' in logs[0].read_text()


def test_cancel_drops_job(tmp_path):
    async def scenario():
        cfg = config_mod.from_dict({"server": {"port": 0, "log_dir": str(tmp_path)}, "generation": {"backend": "mock"}})
        bridge = Bridge(cfg, backend=MockBackend("template", delay_s=0.3))
        port = await bridge.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(encode(PREFETCH))
        writer.write(encode({"t": "cancel", "conv_id": "c1"}))
        await writer.drain()
        try:
            got = await asyncio.wait_for(reader.readline(), 0.6)
        except asyncio.TimeoutError:
            got = b""
        writer.close()
        await bridge.close()
        return got

    assert run(scenario()) == b""


# --- backend selection and fallback ---------------------------------------------------------

LM = {"base_url": "http://127.0.0.1:1234/v1", "model": "local-model"}
OR = {"base_url": "https://openrouter.ai/api/v1", "model": "cloud-model", "api_key_env": "TOMODACHI_LLM_TEST_OR_KEY"}


def names(raw):
    return [b.name for b in config_mod.select_backends(raw)]


def test_auto_uses_the_only_configured_backend(monkeypatch):
    monkeypatch.delenv("TOMODACHI_LLM_TEST_OR_KEY", raising=False)
    assert names({"generation": {"backend": "auto"}, "lmstudio": LM, "openrouter": OR}) == ["lmstudio"]
    monkeypatch.setenv("TOMODACHI_LLM_TEST_OR_KEY", "k")
    assert names({"generation": {"backend": "auto"}, "openrouter": OR}) == ["openrouter"]


def test_auto_with_both_puts_cloud_as_fallback(monkeypatch):
    monkeypatch.setenv("TOMODACHI_LLM_TEST_OR_KEY", "k")
    assert names({"generation": {"backend": "auto"}, "lmstudio": LM, "openrouter": OR}) == ["lmstudio", "openrouter"]
    assert names({"generation": {"backend": "auto", "fallback": False}, "lmstudio": LM, "openrouter": OR}) == ["lmstudio"]


def test_explicit_choice_first_and_other_as_fallback_only_if_provided(monkeypatch):
    monkeypatch.setenv("TOMODACHI_LLM_TEST_OR_KEY", "k")
    assert names({"generation": {"backend": "openrouter"}, "lmstudio": LM, "openrouter": OR}) == ["openrouter", "lmstudio"]
    assert names({"generation": {"backend": "lmstudio"}, "lmstudio": LM}) == ["lmstudio"]
    assert names({"generation": {"backend": "mock"}, "lmstudio": LM, "openrouter": OR}) == ["mock"]


def test_auto_with_nothing_configured_is_an_error(monkeypatch):
    monkeypatch.delenv("TOMODACHI_LLM_TEST_OR_KEY", raising=False)
    with pytest.raises(ValueError):
        config_mod.select_backends({"generation": {"backend": "auto"}, "openrouter": OR})


def test_fallback_is_used_when_primary_fails():
    primary, fallback = MockBackend("fail"), MockBackend("template")
    primary.name, fallback.name = "lmstudio", "openrouter"
    out = run(Generator([primary, fallback], 2.0, 8).run(parse_prefetch(PREFETCH, "USen")))
    assert out.error is None and out.backend == "openrouter" and out.attempts and "lmstudio" in out.attempts[0]


def test_slow_primary_times_out_then_fallback_answers():
    primary, fallback = MockBackend("hang"), MockBackend("template")
    gen = Generator([primary, fallback], deadline_s=2.0, cache_size=8, timeouts=[0.1, 2.0])
    out = run(gen.run(parse_prefetch(PREFETCH, "USen")))
    assert out.error is None and fallback.calls == 1


def test_single_backend_failure_has_no_fallback():
    out = run(Generator([MockBackend("malformed")], 1.0, 8).run(parse_prefetch(PREFETCH, "USen")))
    assert out.error and "malformed" in out.error


def test_config_requires_openrouter_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError):
        config_mod.from_dict({"generation": {"backend": "openrouter"}, "openrouter": {"api_key_env": "OPENROUTER_API_KEY"}})


def test_connection_reset_is_a_clean_disconnect(tmp_path):
    """The game closing (Ryujinx stop) resets the socket; the session must end without a traceback."""
    import socket, struct

    async def scenario():
        cfg = config_mod.from_dict({"server": {"port": 0, "log_dir": str(tmp_path)}, "generation": {"backend": "mock"}})
        bridge = Bridge(cfg, backend=MockBackend("template"))
        port = await bridge.start()
        loop = asyncio.get_running_loop()
        errors = []
        loop.set_exception_handler(lambda l, ctx: errors.append(ctx))
        s = socket.create_connection(("127.0.0.1", port))
        s.sendall(encode({"t": "hello", "ver": "test", "lang": "USen"}))
        await asyncio.sleep(0.1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        s.close()  # RST
        await asyncio.sleep(0.2)
        await bridge.close()
        return errors

    assert run(scenario()) == []


# --- file transport (emulated SD card) ------------------------------------------------------------

from server import FileTransport  # noqa: E402


def _bridge(tmp_path, delay_s=0.0, **gen):
    cfg = config_mod.from_dict({"server": {"log_dir": str(tmp_path / "logs")}, "generation": {"backend": "mock", **gen}})
    return Bridge(cfg, backend=MockBackend("template", delay_s=delay_s))


def _lines(path):
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def test_file_transport_roundtrip(tmp_path):
    sd = tmp_path / "sd"
    sd.mkdir()

    async def scenario():
        ft = FileTransport(_bridge(tmp_path), str(sd), 0.01)
        await ft.step(first=True)  # nothing there yet
        out = sd / "out.jsonl"
        out.write_bytes(encode({"t": "hello", "ver": "test", "lang": "EUfr"}) + encode({"t": "ping", "id": 3})
                        + b'{"t":"line","src":"x"')  # last line still being written
        await ft.step()
        with open(out, "ab") as f:
            f.write(b',"out":"y"}\n' + encode(PREFETCH))
        await ft.step()
        await asyncio.sleep(0.1)  # let the generation job finish
        return ft

    ft = run(scenario())
    got = _lines(sd / "in.jsonl")
    assert [m["t"] for m in got] == ["welcome", "pong", "result"]
    assert got[1]["id"] == 3 and got[2]["conv_id"] == "c1"
    assert ft.session.lang == "EUfr"
    log = next((tmp_path / "logs").glob("events-*.jsonl")).read_text()
    assert '"t":"line","src":"x","out":"y"' in log


def test_file_transport_new_plugin_session_resets_replies(tmp_path):
    sd = tmp_path / "sd"
    sd.mkdir()

    async def scenario():
        ft = FileTransport(_bridge(tmp_path), str(sd), 0.01)
        await ft.step(first=True)
        (sd / "out.jsonl").write_bytes(encode({"t": "hello", "ver": "a"}))
        await ft.step()
        first = _lines(sd / "in.jsonl")
        # the game restarts: the plugin deletes both files and creates a new out.jsonl
        (sd / "in.jsonl").unlink()
        (sd / "out.jsonl").unlink()
        (sd / "out.jsonl").write_bytes(encode({"t": "ping", "id": 1}))
        await ft.step()
        return first, _lines(sd / "in.jsonl")

    first, second = run(scenario())
    assert [m["t"] for m in first] == ["welcome"]
    assert second == [{"t": "pong", "id": 1}]


def test_file_transport_bridge_restart_does_not_replay(tmp_path):
    sd = tmp_path / "sd"
    sd.mkdir()
    (sd / "out.jsonl").write_bytes(encode({"t": "ping", "id": 1}))

    async def scenario():
        ft = FileTransport(_bridge(tmp_path), str(sd), 0.01)
        await ft.step(first=True)
        with open(sd / "out.jsonl", "ab") as f:
            f.write(encode({"t": "ping", "id": 2}))
        await ft.step()

    run(scenario())
    assert _lines(sd / "in.jsonl") == [{"t": "pong", "id": 2}]


def test_unknown_transport_is_rejected():
    with pytest.raises(ValueError):
        config_mod.from_dict({"server": {"transport": "carrier-pigeon"}, "generation": {"backend": "mock"}})


# --- relines (learn-as-you-go substitution) ---------------------------------------------------------

import relines  # noqa: E402


def test_reline_plan_keeps_edge_tags_and_numbers_middle_runs():
    p = relines.plan("k", "{200.8}I want to be friends with {201.3}{200.0}{200.9}.")
    pg = p.pages[0]
    assert pg.prefix == "[1]" and pg.suffix == "" and pg.end_run == 3
    assert pg.readable == "I want to be friends with @2."
    assert pg.middle_runs == {2: "a Mii's name or pronoun"}
    # the catchphrase end goes before the final punctuation, wherever the name moved
    assert relines.compose(p, "@2 seems super fun, let's hang out!") == "[1][2] seems super fun, let's hang out[3]!"
    pg = relines.plan("k", "{6.6}I love the sea!").pages[0]
    assert (pg.prefix, pg.suffix, pg.readable, pg.middle_runs) == ("[1]", "", "I love the sea!", {})


def test_reline_style_runs_inside_the_line_move_to_the_end():
    p = relines.plan("k", "{8.0}{6.6}{6.2}{6.10}Yeaaaah{/}! I love the sea!")
    assert p.pages[0].readable == "Yeaaaah! I love the sea!" and p.pages[0].middle_runs == {}
    assert relines.compose(p, "Woohoo! The ocean rules!") == "[1]Woohoo! The ocean rules![2]"


def test_reline_compose_rejects_bad_placeholders():
    p = relines.plan("k", "Got it. I'll go talk to {200.18} about {2.0}{200.9}.")
    assert relines.compose(p, "I'll chat with @1 about @2!") == "I'll chat with [1] about [2][3]!"
    assert relines.compose(p, "I'll chat about @2!") is None          # dropped @1
    assert relines.compose(p, "@1, @1 and @2!") is None               # duplicated
    assert relines.compose(p, "@1 @2 @7") is None                     # invented


@pytest.mark.parametrize("src,lr,ok", [
    ("{6.6}I love the sea!", relines.BALLOON_CALLER, True),
    ("{6.6}{6.11}Barkle, {2.12} pom.", relines.BALLOON_CALLER, False),      # Mii language
    ("Hello.{0.4}I'm {200.0}.", relines.BALLOON_CALLER, True),              # two pages
    ("A.{0.4}B.{0.4}C.{0.4}D.{0.4}Eeee.", relines.BALLOON_CALLER, False),         # more than MAX_PAGES
    ("Don't let anyone tell you what\nyou can do.", relines.BALLOON_CALLER, False),  # hand-placed line break
    ("{6.6}{6.10}{/}{6.5}", relines.BALLOON_CALLER, False),           # icon only
    ("Oh, {5.0}!", relines.BALLOON_CALLER, True),                            # short greeting
    ("{2.0}?", relines.BALLOON_CALLER, False),                               # nothing to reword
    ("Would you like to save?", "0x710045e9cc", False),                      # not a speech balloon
])
def test_reline_eligibility(src, lr, ok):
    assert relines.eligible({"t": "line", "lr": lr, "src": src, "key": "ab"}) is ok


CAPTION = ("{201.3}{200.0} is saying that {200.18} {200.19} the two of them will get along well because "
           "they're about the same age.")
CAPTION_SHOWN = ("{0.3}Abiz{0.3} is saying that he thinks the two of them will get along well because they're "
                 "about the same age.")


def test_captions_are_eligible_only_when_the_plugin_marks_them():
    ev = {"t": "line", "lr": relines.CAPTION_CALLER, "src": CAPTION, "key": "ab"}
    assert not relines.eligible(ev)                                    # the conversation log shares this caller
    assert relines.eligible(dict(ev, caption=1))
    assert not relines.eligible(dict(ev, caption=1, lr=relines.BALLOON_CALLER, src="{6.6}{6.11}Barkle!"))


def test_caption_request_and_the_fitted_word_check():
    p = relines.plan("k", CAPTION)
    assert p.pages[0].readable.startswith("@1 is saying that @2 @3 the two") and p.pages[0].verb_runs == (3,)
    req = relines.request(p, CAPTION_SHOWN, "USen", CAPTION, caption=True)
    assert req.kind == "caption" and "pages" not in req.context and "never the names" in req.context["caption"]
    plain = "They're sharing stories about codependent relationships."
    req2 = relines.request(relines.plan("k", plain), plain, "USen", plain, caption=True)
    assert "no placeholders" in req2.context["caption"] and "never the names" not in req2.context["caption"]
    assert req.slots[0].max_chars == 110
    assert "his, him, himself" in req.context["placeholders"]["@3"] and '"thinks"' in req.context["placeholders"]["@3"]
    assert relines.compose(p, "@1 says @2 @3 they'll click since they're the same age.") is not None
    assert relines.compose(p, "@1 says @2 @3 is sure they'll click.") is None          # "he thinks is sure"
    assert relines.compose(p, "@1 says @2 @3's sure they'll click.") is None
    assert relines.compose(p, "@1 says @2 @3 really they'll click.") is not None       # not a verb
    q = relines.plan("k", "It seems {200.19} on {200.18} way home after remembering an urgent task.")
    assert relines.compose(q, "@1 rushing on @2 way home.") is None                   # "he is rushing ...": lowercase
    assert relines.compose(q, "Looks like @1 rushing on @2 way home.") is not None
    assert relines.compose(relines.plan("k", "{201.3}{200.19} following {200.0}."), "@1 trailing @2.") is not None
    assert relines.compose(relines.plan("k", "{200.19} following {200.0}."), "@1 shadowing @2.") is not None


def test_caption_reline_is_kept_after_speech_lines(tmp_path):
    sd = tmp_path / "sd"
    sd.mkdir()
    store_path = str(tmp_path / "lines.json")

    async def scenario():
        bridge = _bridge(tmp_path, line_store=store_path)
        ft = FileTransport(bridge, str(sd), 0.01)
        await ft.step(first=True)
        cap = {"t": "line", "lr": relines.CAPTION_CALLER, "caption": 1, "key": "0c0c", "src": CAPTION,
               "out": CAPTION_SHOWN}
        speech = {"t": "line", "lr": relines.BALLOON_CALLER, "key": "0e0f", "src": "{6.6}Sigh...", "out": "Sigh..."}
        log = dict(cap, key="1010", caption=None)                      # same text in the conversation log
        (sd / "out.jsonl").write_bytes(b"".join(encode(e) for e in ({"t": "hello", "lang": "USen"}, cap, log)))
        await ft.step()
        await asyncio.sleep(0.2)
        with open(sd / "out.jsonl", "ab") as f:
            f.write(encode(speech))
        await ft.step()
        await asyncio.sleep(0.2)
        ft._end_session()
        await bridge.close()
        return bridge.generator.backends[0].requests

    reqs = run(scenario())
    assert [r.kind for r in reqs] == ["caption", "reline"]
    got = {m["key"]: m["text"] for m in _lines(sd / "in.jsonl") if m["t"] == "reline"}
    assert got["0c0c"].startswith("Mock: [1] is saying that [2] [3] the two") and "1010" not in got
    st = LineStore(store_path, 10)
    assert st.get("USen", "0c0c")["cap"] is True and "cap" not in st.get("USen", "0e0f")
    assert [k for k, _ in st.lines("USen", 10)] == ["0e0f", "0c0c"]   # speech first, although older


def test_reline_name_at_the_start_is_a_placeholder_not_a_prefix():
    p = relines.plan("k", "{300.0}{201.3}{2.0}? Funny you should bring that name up.")
    assert p.pages[0].prefix == "" and p.pages[0].readable == "@1? Funny you should bring that name up."
    assert relines.compose(p, "Whoa, @1? What a coincidence!") == "Whoa, [1]? What a coincidence!"


def test_reline_keeps_the_speakers_own_name():
    p = relines.plan("k", "{200.8}Hey, nice to meet you{200.9}! I'm {200.0}.")
    assert p.pages[0].readable == "Hey, nice to meet you! I'm @3."
    assert relines.compose(p, "Oh, hello! It is super wonderful to meet @3!") is None    # greets themselves
    assert relines.compose(p, "Hi there! Great to meet you. I am @3.") is not None
    assert relines.compose(p, "Hiya! The name's... well, call me @3!") is not None
    q = relines.plan("k", "{6.2}Oh, so you're {200.0}!{6.2}I'm {200.0}! Don't forget!")
    assert relines.compose(q, "Aha, so you are @2! And I'm @4!") is not None
    assert relines.compose(q, "Aha, so you are @4! And I'm @2!") is None              # names swapped


def test_kept_lines_are_rechecked():
    src = "{200.8}Um, hello. It's nice to meet you{200.9}. My name is {200.0}."
    assert not relines.kept_line_ok("k", src, "Oh, hi there! It is such a pleasure to meet [3][2].")
    assert relines.kept_line_ok("k", src, "[1]Oh, hi there! Lovely to meet you[2]. I'm [3].")


def test_reline_two_pages():
    src = ("{300.1}{300.0}Yeah? I feel the same way{200.9}!{0.4}{300.0}I think we're on the same wavelength, "
           "{200.0}.{6.5} Let's be friends{200.9}!")
    p = relines.plan("k", src)
    assert p.breaks == [3] and [pg.readable for pg in p.pages] == [
        "Yeah? I feel the same way!", "I think we're on the same wavelength, @4. Let's be friends!"]
    req = relines.request(p, "Yeah? I feel the same way!", "USen")
    assert [s.slot_id for s in req.slots] == ["0", "1"] and req.slots[1].keep == ["@4"] and "pages" in req.context
    out = relines.compose(p, {"0": "Really? Me too!", "1": "@4, we totally click. Friends?"})
    assert out == "[1]Really? Me too[2]![3][4], we totally click. Friends[6]?[5]"
    assert relines.compose(p, {"0": "Really? Me too!"}) is None                   # a page missing


def test_reline_page_without_text_is_kept():
    p = relines.plan("k", "Look at this!{0.4}{2.0}")   # "{0.4}{2.0}" is one tag run: the page break
    assert [s for s, _ in p.slots] == [0]
    assert relines.compose(p, "Check it out!") == "Check it out![1]"


def test_reline_pushed_over_file_transport(tmp_path):
    sd = tmp_path / "sd"
    sd.mkdir()

    async def scenario():
        ft = FileTransport(_bridge(tmp_path), str(sd), 0.01)
        await ft.step(first=True)
        line = {"t": "line", "lr": relines.BALLOON_CALLER, "key": "00ff",
                "src": "{200.8}I want to be friends with {200.0}{200.9}.",
                "out": "I want to be friends with {0.3}Diaz{0.3}."}
        (sd / "out.jsonl").write_bytes(encode({"t": "hello", "lang": "USen"}) + encode(line) + encode(line))
        await ft.step()
        await asyncio.sleep(0.1)

    run(scenario())
    got = [m for m in _lines(sd / "in.jsonl") if m["t"] == "reline"]
    assert got == [{"t": "reline", "key": "00ff", "text": "[1]Mock: I want to be friends with [2][3]."}]


def test_two_page_reline_waits_for_the_last_page_then_refreshes(tmp_path):
    sd = tmp_path / "sd"
    sd.mkdir()
    src = "Yeah? I feel the same way!{0.4}I think we're on the same wavelength, {200.0}."
    page1 = {"t": "line", "lr": relines.BALLOON_CALLER, "key": "0a0b", "src": src, "out": "Yeah? I feel the same way!"}
    page2 = dict(page1, out="I think we're on the same wavelength, {0.3}Diaz{0.3}.")

    def pushed():
        return [m for m in _lines(sd / "in.jsonl") if m["t"] == "reline"]

    async def scenario():
        ft = FileTransport(_bridge(tmp_path), str(sd), 0.01)
        await ft.step(first=True)
        out = sd / "out.jsonl"
        out.write_bytes(encode({"t": "hello", "lang": "USen"}) + encode(page1))
        await ft.step()
        await asyncio.sleep(0.6)
        assert pushed() == []                       # page 2 of this showing still vanilla: not yet
        with out.open("ab") as f:
            f.write(encode(page2))
        await ft.step()
        await asyncio.sleep(0.6)
        assert len(pushed()) == 1
        with out.open("ab") as f:                   # next showing plays the generated line: make a new one
            f.write(encode(dict(page1, relined=1)) + encode(dict(page2, relined=1)))
        await ft.step()
        await asyncio.sleep(0.6)

    run(scenario())
    got = pushed()
    assert len(got) == 2 and got[0]["text"] == ("Mock: Yeah? I feel the same way![1]"
                                                "Mock: I think we're on the same wavelength, [2].")


def test_reline_placeholders_say_what_they_showed():
    src = "{200.8}Hey, nice to meet you{200.9}! I'm {200.0}."
    p = relines.plan("k", src)
    req = relines.request(p, "Hey, nice to meet you! I'm {0.3}Abiz{0.3}.", "USen", src)
    assert req.context["placeholders"] == {"@3": 'a Mii\'s name or pronoun, on screen: "Abiz"'}
    req = relines.request(p, "something else entirely", "USen", src)   # doesn't line up: plain description
    assert req.context["placeholders"] == {"@3": "a Mii's name or pronoun"}


def test_refresh_tells_the_model_the_last_three_versions(tmp_path):
    sd = tmp_path / "sd"
    sd.mkdir()
    line = {"t": "line", "lr": relines.BALLOON_CALLER, "key": "0c0d", "src": "{6.6}Sigh...", "out": "Sigh..."}
    bridge = _bridge(tmp_path)

    async def scenario():
        ft = FileTransport(bridge, str(sd), 0.01)
        await ft.step(first=True)
        out = sd / "out.jsonl"
        out.write_bytes(encode({"t": "hello", "lang": "USen"}) + encode(line))
        for _ in range(5):                           # each showing of the generated line asks for a new one
            await ft.step()
            await asyncio.sleep(0.3)
            with out.open("ab") as f:
                f.write(encode(dict(line, relined=1)))
        await ft.step()
        await asyncio.sleep(0.3)

    run(scenario())
    reqs = bridge.generator.backends[0].requests
    assert "previous_versions" not in reqs[0].context
    # the mock always writes the same line, so once three are remembered the request repeats and is cached
    assert [len(r.context["previous_versions"]) for r in reqs[1:]] == [1, 2, 3]


# --- scenes (whole-scene generation) -------------------------------------------------------------------------

import scenes  # noqa: E402

# An invented two-branch scene (not game text): lines 00-01 shared, 02-03 differ by outcome.
_SCENE = {
    "00": ("k00", "{300.0}Sure thing!"),
    "01": ("k01", "{300.1}I really like {2.0}.{0.4}Do you?"),
    "S02": ("ks2", "{300.0}Me too! Let's be pals, {200.0}{200.9}!"),
    "F02": ("kf2", "Hmm, {2.0}? Not my thing."),
    "F03": ("kf3", "Oh... OK."),
}


def _scene_library():
    lib = scenes.Library()
    for outcome, rows in (("Success", ["00", "01", "S02"]), ("Failure", ["00", "01", "F02", "F03"])):
        for i, r in enumerate(rows):
            key, text = _SCENE[r]
            lib.add_label({"t": "label", "label": f"FriendUgc_Person_{outcome}_0100_{i:02d}", "found": 1,
                           "key": key, "text": text})
    lib.add_label({"t": "label", "label": "FriendUgc_Person_Success_0200_00", "found": 1, "key": "k00",
                   "text": "{300.0}Sure thing!"})
    return lib


def test_scene_candidates_narrow_as_lines_are_seen():
    lib = _scene_library()
    assert {c.sid for c in lib.candidates([("k00", None)])} == {"Person_Success_0100", "Person_Failure_0100",
                                                                 "Person_Success_0200"}
    assert {c.sid for c in lib.candidates([("k00", None), ("k01", None)])} == {"Person_Success_0100",
                                                                               "Person_Failure_0100"}
    assert [c.sid for c in lib.candidates([("k01", None), ("kf2", None)])] == ["Person_Failure_0100"]


def test_scene_plan_covers_both_branches_and_composes():
    lib = _scene_library()
    cands = lib.candidates([("k00", None), ("k01", None)])
    said = [{"key": "k00", "idx": 0, "text": "Sure thing!"}, {"key": "k01", "idx": 1, "text": "I really like tea."}]
    sp = scenes.plan_scene(cands, said, "tea", "USen")
    assert set(sp.lines) == {"ks2", "kf2", "kf3"}
    ids = [s.slot_id for s in sp.request.slots]
    assert len(ids) == 3 and "said_so_far" in sp.request.context
    kf2 = next(s for s in sp.request.slots if s.slot_id.startswith("kf2"))
    assert kf2.keep == ["@1"] and "B" in kf2.label
    texts = {ids[0]: "Same here! Friends, @2?", ids[1]: "@1? Nah.", ids[2]: "Oh well."}
    made = scenes.compose_scene(sp, texts)
    assert made == {"ks2": "[1]Same here! Friends, [2][3]?", "kf2": "[1]? Nah.", "kf3": "Oh well."}
    assert scenes.compose_scene(sp, {ids[0]: "No placeholder!"}) == {}
    # the topic written out instead of its placeholder is put back
    assert scenes.compose_scene(sp, {ids[1]: "Tea? Nah."}, topic="tea")["kf2"] == "[1]? Nah."


def test_topic_word_from_a_shown_line():
    assert scenes.topic_word("{300.1}I really like {2.0}.{0.4}Do you?", "I really like {0.3}green tea{0.3}.") == "green tea"
    assert scenes.topic_word("Hmm, {2.0}? Not my thing.", "Something else entirely") == ""


def test_scene_lines_pushed_before_they_show(tmp_path):
    sd = tmp_path / "sd"
    sd.mkdir()
    lib = _scene_library()

    async def scenario():
        ft = FileTransport(_bridge(tmp_path), str(sd), 0.01)
        await ft.step(first=True)
        labels = b"".join(encode({"t": "label", "label": f"FriendUgc_Person_{o}_0100_{i:02d}", "found": 1,
                                  "key": _SCENE[r][0], "text": _SCENE[r][1]})
                          for o, rows in (("Success", ["00", "01", "S02"]), ("Failure", ["00", "01", "F02", "F03"]))
                          for i, r in enumerate(rows))
        def line(r, out):
            return encode({"t": "line", "lr": relines.BALLOON_CALLER, "key": _SCENE[r][0], "src": _SCENE[r][1],
                           "out": out})
        (sd / "out.jsonl").write_bytes(encode({"t": "hello", "lang": "USen"}) + labels
                                       + line("00", "Sure thing!") + line("01", "I really like {0.3}tea{0.3}."))
        await ft.step()
        await asyncio.sleep(0.3)

    run(scenario())
    got = {m["key"]: m["text"] for m in _lines(sd / "in.jsonl") if m["t"] == "reline"}
    assert {"ks2", "kf2", "kf3"} <= set(got)
    assert got["kf3"] == "Mock: Oh... OK."


def _label(label, key, text, **kw):
    return {"t": "label", "label": label, "found": 1, "key": key, "text": text, **kw}


def test_library_learns_variants_aftermath_and_opening():
    lib = _scene_library()
    lib.add_label(_label("FriendUgc_Person_Success_0100_01_V000", "k01v", "{300.1}{2.0} is the best.{0.4}Right?"))
    lib.add_label(_label("FriendUgc_Person_Success_0100_Success_00", "ka0", "{200.8}{200.0} and I are pals now{200.9}!"))
    lib.add_label(_label("FriendUgc_Person_Failure_0100_Failure", "kaf", "Oh well, {200.0} wasn't into it."))
    lib.add_label(_label("FriendUgc_Common_01", "kc1", "Want to talk about..."))
    lib.add_label(_label("FriendUgc_Common_02_V001", "kc2a", "{2.0}?"))
    lib.add_label(_label("FriendUgc_Person_Success_0100_02", "kcut", "x", cut=1))     # cut templates are ignored
    sc = lib.scenes["Person_Success_0100"]
    assert [v.key for v in sc.variants[1]] == ["k01v"] and sc.lines[scenes.AFTER].key == "ka0"
    assert lib.scenes["Person_Failure_0100"].lines[scenes.AFTER].key == "kaf"
    assert lib.opening == {"kc1": (scenes.OPENING + 1, None), "kc2a": (scenes.OPENING + 2, "Action")}
    assert "kc1" in lib and "kcut" not in lib
    # a variant shown counts as its line
    assert [c.sid for c in lib.candidates([("k00", None), ("k01v", None)])] == ["Person_Success_0100"]
    assert scenes.speaker(scenes.AFTER).startswith("A") and scenes.speaker(scenes.AFTER + 1).startswith("B")
    assert scenes.speaker(scenes.OPENING).startswith("A")


def test_variant_gets_the_base_text_when_placeholders_line_up():
    base = relines.plan("b", "{300.1}I really like {2.0}.{0.4}Do you?")
    var = relines.plan("v", "{300.1}{2.0} is the best.{0.4}Right?")
    other = relines.plan("o", "{300.1}I like {2.0} and {200.0}.{0.4}You?")
    texts = {"0": "@2 is my jam.", "1": "You too?"}
    assert scenes.retarget(base, texts, var) == "[1] is my jam.[2]You too?"  # "{300.1}{2.0}" is one tag run
    assert scenes.retarget(base, texts, other) is None        # another placeholder: vanilla
    assert scenes.retarget(base, {"0": "Hi @2."}, relines.plan("p", "{2.0}!")) is None   # one balloon, not two


def test_scene_plan_upto_and_aftermath():
    lib = _scene_library()
    lib.add_label(_label("FriendUgc_Person_Success_0100_01_V000", "k01v", "{300.1}{2.0} is the best.{0.4}Right?"))
    lib.add_label(_label("FriendUgc_Person_Success_0100_Success_00", "ka0", "{200.8}{200.0} and I are pals now{200.9}!"))
    opening = [{"key": "kc2", "idx": scenes.OPENING + 2, "text": "Tea?"}]
    sp = scenes.plan_scene(lib.of_genre("Person"), opening, "tea", "USen", upto=1)
    assert set(sp.lines) == {"k00", "k01"} and "how_it_continues" not in sp.request.context
    made = scenes.compose_scene(sp, {"k00.0": "You bet!", "k01.0": "@2 rules.", "k01.1": "Agreed?"})
    assert made["k01v"] == "[1] rules.[2]Agreed?"          # the variant got the same line
    cands = lib.candidates([("k00", None), ("k01", None)])
    said = opening + [{"key": "k00", "idx": 0, "text": "You bet!"}, {"key": "k01", "idx": 1, "text": "Tea rules."}]
    sp = scenes.plan_scene(cands, said, "tea", "USen")
    assert "ka0" in sp.lines and "later" in next(s.label for s in sp.request.slots if s.slot_id.startswith("ka0"))
    assert sp.request.context["said_so_far"][0] == "A: Tea?"


def test_lines_00_01_are_written_ahead_once_the_scripts_are_known(tmp_path):
    sd = tmp_path / "sd"
    sd.mkdir()
    bridge = _bridge(tmp_path, scene_warm_delay_s=0.05)

    async def scenario():
        ft = FileTransport(bridge, str(sd), 0.01)
        await ft.step(first=True)
        labels = b"".join(encode(_label(f"FriendUgc_Person_{o}_0100_{i:02d}", _SCENE[r][0], _SCENE[r][1], dump=1))
                          for o, rows in (("Success", ["00", "01", "S02"]), ("Failure", ["00", "01", "F02", "F03"]))
                          for i, r in enumerate(rows))
        (sd / "out.jsonl").write_bytes(encode({"t": "hello", "lang": "USen"}) + labels)
        await ft.step()
        await asyncio.sleep(0.4)

    run(scenario())
    got = {m["key"] for m in _lines(sd / "in.jsonl") if m["t"] == "reline"}
    assert got == {"k00", "k01"}                                        # nothing past line 01, no topic needed
    req = bridge.generator.backends[0].requests[-1]
    assert "not known yet" in req.context["scene"] and len(req.slots) == 3


def test_warm_groups_split_a_genre_into_small_calls():
    lib = _scene_library()
    assert [[s.sid for s in g] for g in scenes.warm_groups(lib, "Person", max_slots=10)] == [
        ["Person_Failure_0100", "Person_Success_0100", "Person_Success_0200"]]
    assert [len(g) for g in scenes.warm_groups(lib, "Person", max_slots=3)] == [3]     # shared lines cost nothing
    lib.add_label(_label("FriendUgc_Person_Success_0300_00", "kn", "{300.0}Nope!"))
    assert [[s.sid for s in g] for g in scenes.warm_groups(lib, "Person", max_slots=3)] == [
        ["Person_Failure_0100", "Person_Success_0100", "Person_Success_0200"], ["Person_Success_0300"]]


# --- line store (generated lines kept between sessions) ------------------------------------------------------

from store import LineStore  # noqa: E402


def test_line_store_roundtrip_and_prune(tmp_path):
    path = str(tmp_path / "lines.json")
    st = LineStore(path, max_lines=2)
    st.put("USen", "a", "A1", "src a", "out a", ["h1"])
    st.put("USen", "b", "B1", "src b", "out b", [])
    st.langs["USen"]["a"]["used"] = 1.0              # "a" is the oldest
    st.put("USen", "c", "C1", "src c", "out c", [])  # over the limit: "a" goes
    st.save()
    again = LineStore(path, max_lines=2)
    assert sorted(again.langs["USen"]) == ["b", "c"]
    assert [k for k, _ in again.lines("USen", 1)] == ["c"]                       # most recently used first
    assert again.lines("EUfr", 10) == []
    (tmp_path / "bad.json").write_text("{not json")
    assert LineStore(str(tmp_path / "bad.json"), 10).langs == {}
    assert not LineStore("", 10).path                # no file: nothing kept, nothing written
    st = LineStore(str(tmp_path / "caps.json"), max_lines=2)
    st.put("USen", "s", "S1", "src s", "out s", [])
    st.langs["USen"]["s"]["used"] = 1.0              # the oldest, but a speech line
    st.put("USen", "c", "C1", "src c", "out c", [], caption=True)
    st.put("USen", "d", "D1", "src d", "out d", [])  # over the limit: the caption goes first
    assert sorted(st.langs["USen"]) == ["d", "s"]


def test_kept_lines_are_sent_at_hello_and_refreshed_on_first_showing(tmp_path):
    sd = tmp_path / "sd"
    sd.mkdir()
    store_path = str(tmp_path / "lines.json")
    line = {"t": "line", "lr": relines.BALLOON_CALLER, "key": "0e0f", "src": "{6.6}Sigh...", "out": "Sigh..."}

    async def session(hello: dict, events: list[dict]) -> list[dict]:
        bridge = _bridge(tmp_path, line_store=store_path)
        ft = FileTransport(bridge, str(sd), 0.01)
        await ft.step(first=True)
        out = sd / "out.jsonl"
        if out.exists():
            out.unlink()
        await ft.step()
        out.write_bytes(encode(hello) + b"".join(encode(e) for e in events))
        await ft.step()
        await asyncio.sleep(0.3)
        ft._end_session()
        await bridge.close()
        return bridge.generator.backends[0].requests

    # session 1: the line is seen, generated and kept
    run(session({"t": "hello", "lang": "USen", "relines_max": 1024}, [line]))
    kept = LineStore(store_path, 10).get("USen", "0e0f")
    assert kept and kept["text"] == "[1]Mock: Sigh..." and kept["hist"] == ["Mock: Sigh..."]

    # session 2: sent at hello; its first showing (already generated) asks for a fresh one, avoiding the kept one
    reqs = run(session({"t": "hello", "lang": "USen", "relines_max": 1024}, [dict(line, relined=1)]))
    pushed = [m for m in _lines(sd / "in.jsonl") if m["t"] == "reline"]
    assert pushed[0] == {"t": "reline", "key": "0e0f", "text": "[1]Mock: Sigh..."}
    assert len(reqs) == 1 and reqs[0].context["previous_versions"] == ["Mock: Sigh..."]

    # an older plugin (no relines_max) gets nothing at hello
    run(session({"t": "hello", "lang": "USen"}, []))
    assert [m for m in _lines(sd / "in.jsonl") if m["t"] == "reline"] == []

    # plugin 0.6.2+ says how much text it holds: kept lines take at most half of it
    run(session({"t": "hello", "lang": "USen", "relines_max": 1024, "relines_bytes": 2 * len("[1]Mock: Sigh...") - 2}, []))
    assert [m for m in _lines(sd / "in.jsonl") if m["t"] == "reline"] == []
    run(session({"t": "hello", "lang": "USen", "relines_max": 1024, "relines_bytes": 2 * len("[1]Mock: Sigh...")}, []))
    assert len([m for m in _lines(sd / "in.jsonl") if m["t"] == "reline"]) == 1


def test_scene_lines_are_not_kept(tmp_path):
    bridge = _bridge(tmp_path, line_store=str(tmp_path / "lines.json"))
    bridge.store.put("USen", "k00", "old", "{300.0}Sure thing!", "Sure thing!", [])

    async def scenario():
        from server import Session

        async def write(data):
            pass

        s = Session(bridge, write)
        await s.handle({"t": "label", "label": "FriendUgc_Person_Success_0100_00", "found": 1, "key": "k00",
                        "text": "{300.0}Sure thing!"}, "")
        s.close()

    run(scenario())
    assert bridge.store.get("USen", "k00") is None


import httpx  # noqa: E402

import spend  # noqa: E402
from backends.openai_compat import OpenAICompatBackend  # noqa: E402
from config import BackendConfig  # noqa: E402
from protocol import GenRequest, Slot  # noqa: E402


def test_spend_tracker_counts_and_stops_at_the_cap(tmp_path):
    path = str(tmp_path / "spend.json")
    t = spend.SpendTracker(path, cap_usd=0.05)
    t.check()                                    # nothing spent yet
    assert t.add(0.02) == pytest.approx(0.02)
    assert spend.SpendTracker(path, 0.05).add(0.03) == pytest.approx(0.05)  # shared through the file
    with pytest.raises(spend.SpendCapReached):
        t.check()
    assert json.loads(open(path).read())["calls"] == 2
    (tmp_path / "bad.json").write_text("{")
    with pytest.raises(spend.SpendCapReached):   # unreadable: errs on the side of stopping
        spend.SpendTracker(str(tmp_path / "bad.json"), 5.0).check()


def test_call_cost_prefers_the_reported_cost():
    assert spend.call_cost({"cost": 0.0004, "prompt_tokens": 10}, {"prompt": "1", "completion": "1"}) == 0.0004
    assert spend.call_cost({"prompt_tokens": 1000, "completion_tokens": 100},
                           {"prompt": "0.0000001", "completion": "0.0000004"}) == pytest.approx(0.00014)
    assert spend.call_cost({}, None) == spend.UNKNOWN_COST_USD


def _openrouter(tmp_path, cap, handler):
    cfg = BackendConfig(name="openrouter", base_url="https://openrouter.test/api/v1", model="m", api_key="x",
                        spend_cap_usd=cap, spend_file=str(tmp_path / "spend.json"))
    client = httpx.AsyncClient(base_url=cfg.base_url, transport=httpx.MockTransport(handler))
    return OpenAICompatBackend(cfg, client=client)


def test_openrouter_backend_counts_each_call_and_refuses_over_the_cap(tmp_path):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"lines": []}'}}],
                                         "usage": {"cost": 0.03, "prompt_tokens": 50, "completion_tokens": 9}})

    b = _openrouter(tmp_path, 0.05, handler)
    req = GenRequest(conv_id="c", kind="reline", lang="USen", context={}, slots=[Slot(slot_id="0", template="Hi")])

    async def scenario():
        await b.generate(req)
        await b.generate(req)                    # 0.06 now: over the cap
        with pytest.raises(spend.SpendCapReached):
            await b.generate(req)

    run(scenario())
    assert len(bodies) == 2 and bodies[0]["usage"] == {"include": True}
    assert b.spend.total_usd == pytest.approx(0.06)


def test_openrouter_timeout_is_counted(tmp_path):
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    b = _openrouter(tmp_path, 5.0, handler)
    req = GenRequest(conv_id="c", kind="reline", lang="USen", context={}, slots=[Slot(slot_id="0", template="Hi")])
    with pytest.raises(httpx.TimeoutException):
        run(b.generate(req))
    assert b.spend.total_usd == pytest.approx(spend.UNKNOWN_COST_USD)


def test_spend_file_is_next_to_the_config(tmp_path):
    cfgfile = tmp_path / "c.toml"
    cfgfile.write_text('[generation]\nbackend = "mock"\n')
    assert config_mod.load(str(cfgfile)).backends[0].spend_file == str(tmp_path / "openrouter-spend.json")


def test_openrouter_leaves_out_unsupported_parameters_and_retries_mandatory_reasoning(tmp_path):
    sent = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "m", "supported_parameters": ["reasoning",
                                                                                         "response_format"]}]})
        body = json.loads(request.content)
        sent.append(body)
        if body.get("reasoning", {}).get("effort") == "none":
            return httpx.Response(400, json={"error": {"message": "Reasoning is mandatory for this endpoint"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"lines": []}'}}],
                                         "usage": {"cost": 0.001}})

    b = _openrouter(tmp_path, 5.0, handler)
    b.cfg.extra_body = {"reasoning": {"effort": "none"}, "provider": {"require_parameters": True}}
    req = GenRequest(conv_id="c", kind="reline", lang="USen", context={}, slots=[Slot(slot_id="0", template="Hi")])
    run(b.generate(req))
    run(b.generate(req))
    assert "temperature" not in sent[0]                        # the model doesn't list it
    assert [s["reasoning"]["effort"] for s in sent] == ["none", "low", "low"]  # learned once
    assert b.spend.total_usd == pytest.approx(0.002)            # the refused call isn't counted


def test_relative_paths_are_next_to_the_config(tmp_path):
    cfgfile = tmp_path / "c.toml"
    cfgfile.write_text('[server]\nlog_dir = "logs"\n[generation]\nbackend = "mock"\nline_store = "lines.json"\n')
    cfg = config_mod.load(str(cfgfile))
    assert cfg.log_dir == str(tmp_path / "logs") and cfg.line_store == str(tmp_path / "lines.json")


# --- the player-facing banner -------------------------------------------------------------------------------------

import server  # noqa: E402


def test_ready_banner_says_what_to_do(monkeypatch):
    monkeypatch.setenv("TOMODACHI_LLM_TEST_OR_KEY", "k")
    cfg = config_mod.from_dict({"generation": {"backend": "auto"}, "lmstudio": LM, "openrouter": OR})
    text = server.ready_banner(cfg, [None, None])
    assert "Ready. Start the game" in text and "Ctrl-C" in text
    assert "(LM Studio); OpenRouter if that fails" in text


def test_ready_banner_explains_a_missing_model():
    cfg = config_mod.from_dict({"generation": {"backend": "lmstudio"}, "lmstudio": LM})
    text = server.ready_banner(cfg, ["the model 'local-model' isn't loaded"])
    assert "Not ready: LM Studio: the model 'local-model' isn't loaded." in text
    assert "Ready. Start the game" not in text


def test_file_transport_ignores_a_finished_sessions_file(tmp_path):
    """At bridge start, an out.jsonl nobody has written to for a while is from a closed game: no session yet."""
    import os
    import time
    sd = tmp_path / "sd"
    sd.mkdir()
    out = sd / "out.jsonl"
    out.write_bytes(encode({"t": "ping", "id": 1}))
    old = time.time() - 600
    os.utime(out, (old, old))

    async def scenario():
        ft = FileTransport(_bridge(tmp_path), str(sd), 0.01)
        await ft.step(first=True)
        waiting = ft.session is None
        out.unlink()  # the game starts: the plugin recreates out.jsonl
        out.write_bytes(encode({"t": "ping", "id": 2}))
        await ft.step()
        return waiting, ft.session is not None, _lines(sd / "in.jsonl")

    waiting, started, replies = run(scenario())
    assert waiting and started
    assert replies == [{"t": "pong", "id": 2}]
