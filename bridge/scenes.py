"""Whole-scene generation: the model writes the rest of a scripted conversation in one call.

First target: the befriend conversations (TroubleFriendUgc), played both for "help me befriend" requests and when
two Miis start a topic chat on their own. A conversation is:
  - an opening said by A, the Mii who starts it (`FriendUgc_Common_00..02`: a greeting, "Do you feel like
    talking about...", the topic word; the variant of line 02 tells the topic's genre),
  - the scene `FriendUgc_<Genre>_<Success|Failure>_<pattern>_<NN>`, lines alternating between B (the Mii asked)
    and A, starting with B at 00,
  - aftermath lines said later: `_Success_00` (A) and `_Success_01` (B), or `_Failure` (A).
Most lines have variants (`_V000`, ...), picked by the game when the line plays.

The game looks up the base scene lines by label at load, and plugin 0.4.0 reports each label's template key.
Plugin 0.5.0 also reports every `FriendUgc_*` label of the file once (`dump` label events), which adds the
opening, the aftermath and the variants. So the bridge knows the script before it plays:
  - lines 00-01 only refer to the topic through placeholders, so they are written ahead, in the background, once
    the scripts are known (one genre at a time, WARM_SLOTS balloons per call), and again after each conversation;
    one call for a whole genre takes 9-15 s on Gemma, too slow to wait for the topic line;
  - once the lines shown narrow it down to at most MAX_CANDIDATES scenes (Success and Failure share their first
    lines), one call writes every remaining line of every candidate, aftermath included, continuing from what
    was actually said.
Generated lines are pushed as ordinary relines (by template key), so whichever branch the game plays gets them; a
variant gets its base line's text when their placeholders line up. Any line not ready plays vanilla.

The game keeps the scene's structure: who speaks, how many turns and balloons, the animations, the topic word.
Each line's voice/emotion tags stay in place, so the text has to fit the acting (the prompt says so). The speaker
order fits the content of one scene played in the game (2026-10-11); who spoke on screen wasn't checked
[UNVERIFIED].
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import relines
from protocol import GenRequest, Slot

LABEL = re.compile(r"^FriendUgc_(Person|Object|Action|Other)_(Success|Failure)_(\d{4})_"
                   r"(?:(\d{2})|Success_(\d{2})|(Failure))(?:_V(\d{3}))?$")
COMMON = re.compile(r"^FriendUgc_Common_(\d{2})(?:_V(\d{3}))?$")
WARM_UPTO = 1       # lines written ahead of the conversation: 00-01
WARM_SLOTS = 10     # balloons per warm-up call (13 took ~9 s, 24-27 timed out at 15 s on Gemma)
MAX_CANDIDATES = 2  # generate once the playing scene is one of at most this many (3 took ~11 s on Gemma)
CONV_GAP_S = 90.0   # lines of one conversation come within this many seconds of each other
AFTER = 100         # line index of the aftermath: AFTER + n for `_Success_0n`, AFTER for `_Failure`
OPENING = -3        # line index of the opening: OPENING + n for `FriendUgc_Common_0n`
TOPIC_LINE = 2      # the opening line that shows the topic word; its variant tells the genre
TOPIC_GENRE = {None: "Person", 0: "Object", 1: "Action"}  # variant of Common_02 -> genre; other variants: "Other"

GENRE = {"Person": "a person", "Object": "a thing", "Action": "an activity", "Other": "something else"}


@dataclass
class SceneLine:
    idx: int
    key: str
    template: str


@dataclass
class Scene:
    sid: str  # "Person_Success_0100"
    genre: str
    outcome: str
    lines: dict[int, SceneLine] = field(default_factory=dict)  # base line of each index
    variants: dict[int, list[SceneLine]] = field(default_factory=dict)

    def ordered(self) -> list[SceneLine]:
        return [self.lines[i] for i in sorted(self.lines)]

    def positions(self) -> dict[str, int]:
        """Template key -> line index, for base lines and variants."""
        pos = {ln.key: ln.idx for ln in self.lines.values()}
        for vs in self.variants.values():
            pos.update({v.key: v.idx for v in vs})
        return pos


class Library:
    """Scenes learned from the plugin's label events (label events carry `key` from plugin 0.4.0)."""

    def __init__(self) -> None:
        self.scenes: dict[str, Scene] = {}
        self.by_key: dict[str, list[tuple[str, int]]] = {}
        self.opening: dict[str, tuple[int, str | None]] = {}  # key -> (line index, genre for the topic line)

    def add_label(self, ev: dict) -> None:
        if not ev.get("found") or not ev.get("key") or not ev.get("text") or ev.get("cut"):
            return
        label = str(ev.get("label", ""))
        m = COMMON.match(label)
        if m:
            n, var = int(m.group(1)), (int(m.group(2)) if m.group(2) else None)
            genre = TOPIC_GENRE.get(var, "Other") if n == TOPIC_LINE else None
            self.opening.setdefault(ev["key"], (OPENING + n, genre))
            return
        m = LABEL.match(label)
        if not m:
            return
        genre, outcome, pattern = m.group(1), m.group(2), m.group(3)
        if m.group(4) is not None:
            idx = int(m.group(4))
        elif m.group(5) is not None:
            idx = AFTER + int(m.group(5))
        else:
            idx = AFTER
        sid = f"{genre}_{outcome}_{pattern}"
        sc = self.scenes.setdefault(sid, Scene(sid, genre, outcome))
        line = SceneLine(idx, ev["key"], ev["text"])
        if m.group(7) is None:
            if idx in sc.lines:
                return
            sc.lines[idx] = line
        else:
            vs = sc.variants.setdefault(idx, [])
            if any(v.key == line.key for v in vs) or (idx in sc.lines and sc.lines[idx].key == line.key):
                return
            vs.append(line)
        self.by_key.setdefault(ev["key"], []).append((sid, idx))

    def __contains__(self, key: str) -> bool:
        return key in self.by_key or key in self.opening

    def of_genre(self, genre: str) -> list[Scene]:
        return [sc for sc in self.scenes.values() if sc.genre == genre]

    def candidates(self, seen: list[tuple[str, int | None]]) -> list[Scene]:
        """Scenes consistent with the scene lines seen so far in this conversation, in order."""
        if not seen:
            return []
        out = []
        for sid, idx in self.by_key.get(seen[-1][0], []):
            sc = self.scenes[sid]
            if sc in out:
                continue
            # every earlier line seen must be an earlier line of this scene
            pos = sc.positions()
            if all(k in pos and pos[k] <= idx for k, _ in seen):
                out.append(sc)
        return out


fill_values = relines.fill_values


def topic_word(template: str, shown: str) -> str:
    """The topic word ({2.0}) as shown in one page of a line, or ""."""
    runs = {s.run: s.tags for s in relines.split_template(template) if s.run}
    for run, value in fill_values(template, shown).items():
        if "{2.0}" in runs.get(run, "") and len(value) <= 60:
            return value
    return ""


@dataclass
class ScenePlan:
    """One generation request covering the lines still to come of every candidate scene."""
    request: GenRequest
    lines: dict[str, tuple[relines.RelinePlan, list[str]]]  # key -> (plan, slot ids of its pages)
    variants: dict[str, list[SceneLine]] = field(default_factory=dict)  # base key -> its variants


def speaker(idx: int) -> str:
    if idx < 0 or idx == AFTER:
        return "A (the Mii who wants to be friends)"
    if idx > AFTER:
        return "B (the Mii being asked to be friends)"
    return "B (the Mii being asked to be friends)" if idx % 2 == 0 else "A (the Mii who wants to be friends)"


def plan_scene(cands: list[Scene], said: list[dict], topic: str, lang: str, upto: int | None = None,
               skip: set[str] | frozenset[str] = frozenset()) -> ScenePlan | None:
    """`said`: the lines shown so far (opening included), as {"key", "idx", "text"} (text as shown on screen).
    `upto`: only lines up to this index (warm-up). `skip`: lines not to write (already written in this warm-up)."""
    said_keys = {s["key"] for s in said} | set(skip)
    last_idx = max((s["idx"] for s in said), default=-1)
    slots: list[Slot] = []
    lines: dict[str, tuple[relines.RelinePlan, list[str]]] = {}
    variants: dict[str, list[SceneLine]] = {}
    branches = []
    for sc in cands:
        rest = [ln for ln in sc.ordered()
                if ln.idx > last_idx and ln.key not in said_keys and (upto is None or ln.idx <= upto)]
        branch = {"if": "the friendship works out" if sc.outcome == "Success" else "the friendship doesn't work out",
                  "lines": []}
        for ln in rest:
            branch["lines"].append(f"line {ln.key[:6]}" + (" (later)" if ln.idx >= AFTER else ""))
            if ln.key in lines:
                continue
            variants[ln.key] = list(sc.variants.get(ln.idx, []))
            p = relines.plan(ln.key, ln.template)
            ids = []
            later = ", said later, looking back on the conversation" if ln.idx >= AFTER else ""
            for i, pg in p.slots:
                sid = f"{ln.key[:6]}.{i}"
                ids.append(sid)
                desc = {f"@{k}": v for k, v in pg.middle_runs.items()}
                slots.append(Slot(slot_id=sid, template=pg.readable,
                                  max_chars=max(40, min(120, int(len(pg.readable) * 1.6) + 10)),
                                  keep=[f"@{k}" for k in sorted(pg.middle_runs)],
                                  label=f"line {ln.key[:6]}, page {i + 1}, said by {speaker(ln.idx)}{later}",
                                  placeholders=desc))
            lines[ln.key] = (p, ids)
        branches.append(branch)
    if not slots:
        return None
    sc0 = cands[0]
    kind = GENRE.get(sc0.genre, "something")
    ctx = {
        "scene": (f"Two Miis talk so they can become friends. The topic is {kind}: \"{topic}\" (@ placeholders for "
                  "the topic word stand for it)." if topic else
                  f"Two Miis talk so they can become friends. The topic is {kind}, not known yet: the @ placeholder for "
                  "the topic word stands for it, so the line must work for any topic of that kind."),
        "said_so_far": [f"{speaker(s['idx']).split(' ')[0]}: {s['text']}" for s in said],
    }
    if upto is None:
        ctx["how_it_continues"] = branches if len(branches) > 1 else branches[0]["lines"]
        ctx["instructions"] = (
            "Write the rest of this conversation as one coherent dialogue that follows on from what was said. Each "
            "slot is one speech balloon of a line; a line may have several balloons. Keep each line's role in the "
            "scene (a question stays a question, the ending that agrees to be friends stays that, a disappointed "
            "reply stays disappointed): the game plays the same animations and moods as for its own lines. If there "
            "are several possible continuations, write every one; only one will play. Lines marked later are said "
            "after the conversation, when each Mii thinks back on it.")
    else:
        ctx["instructions"] = (
            "A asked B to talk about the topic. The game will pick one of several scripts for how the conversation "
            "goes on. Each slot is one possible next line (B answers, then A says something about the topic); write "
            "each so it keeps the line's role and mood. Only one of them will play.")
    req = GenRequest(conv_id=f"scene-{sc0.sid}", kind="scene", lang=lang, context=ctx, slots=slots)
    return ScenePlan(req, lines, variants)


def _repair(text: str, keep: list[str], placeholders: dict[str, str], topic: str) -> str:
    """The model sometimes writes the topic word itself instead of its placeholder: put the placeholder back."""
    if not topic:
        return text
    for ph in keep:
        if ph not in text and placeholders.get(ph) == relines.describe("{2.0}") and topic.lower() in text.lower():
            i = text.lower().index(topic.lower())
            text = text[:i] + ph + text[i + len(topic):]
    return text


def retarget(base: relines.RelinePlan, page_texts: dict[str, str], var: relines.RelinePlan) -> str | None:
    """The generated text of a base line, fitted to one of its variants: page by page, each placeholder of the base
    becomes the variant's placeholder of the same kind in the same order. None if they don't line up (different
    number of balloons, or different placeholders); the variant then plays vanilla."""
    b_slots, v_slots = base.slots, var.slots
    if len(b_slots) != len(v_slots):
        return None
    out = {}
    for (bi, bpg), (vi, vpg) in zip(b_slots, v_slots):
        b_order = [int(n) for n in re.findall(r"@(\d+)", bpg.readable)]
        v_order = [int(n) for n in re.findall(r"@(\d+)", vpg.readable)]
        if [bpg.middle_runs[k] for k in b_order] != [vpg.middle_runs[k] for k in v_order]:
            return None
        mapping = dict(zip(b_order, v_order))
        text = page_texts.get(str(bi))
        if text is None:
            return None
        out[str(vi)] = re.sub(r"@(\d+)", lambda m: f"@{mapping.get(int(m.group(1)), 0)}", text)
    return relines.compose(var, out)


def compose_scene(sp: ScenePlan, texts: dict[str, str], topic: str = "") -> dict[str, str]:
    """key -> plugin text, for every line whose pages all came back valid, and for the variants they fit."""
    slots = {s.slot_id: s for s in sp.request.slots}
    out = {}
    for key, (p, ids) in sp.lines.items():
        page_texts = {sid.split(".")[1]: _repair(texts[sid], slots[sid].keep, slots[sid].placeholders, topic)
                      for sid in ids if sid in texts}
        if len(page_texts) != len(ids):
            continue
        text = relines.compose(p, page_texts)
        if not text or len(text.encode("utf-8")) >= relines.MAX_BYTES:
            continue
        out[key] = text
        for v in sp.variants.get(key, []):
            vt = retarget(p, page_texts, relines.plan(v.key, v.template))
            if vt and len(vt.encode("utf-8")) < relines.MAX_BYTES:
                out.setdefault(v.key, vt)
    return out


def warm_groups(lib: Library, genre: str, max_slots: int = WARM_SLOTS) -> list[list[Scene]]:
    """The scenes of a genre in groups whose lines 00-01 fit in one warm-up call."""
    groups, cur, cur_keys, n = [], [], set(), 0
    for sc in sorted(lib.of_genre(genre), key=lambda s: s.sid):
        new = {ln.key: len(relines.plan(ln.key, ln.template).slots)
               for ln in sc.ordered() if 0 <= ln.idx <= WARM_UPTO and ln.key not in cur_keys}
        add = sum(new.values())
        if cur and add and n + add > max_slots:  # a scene sharing all its lines joins the group for free
            groups.append(cur)
            cur, cur_keys, n = [], set(), 0
            new = {ln.key: len(relines.plan(ln.key, ln.template).slots)
                   for ln in sc.ordered() if 0 <= ln.idx <= WARM_UPTO}
            add = sum(new.values())
        cur.append(sc)
        cur_keys |= set(new)
        n += add
    if cur:
        groups.append(cur)
    return groups
