"""Relines: generated replacements for lines the game has already shown once ("learn as you go").

The plugin reports each expanded line with `key` (a hash of the raw template). For Mii speech, the
bridge asks the model for a fresh line with the same purpose and pushes it back as
`{"t": "reline", "key": ..., "text": ...}`. The next time the game shows that template, the plugin
plays the generated line instead (balloon, voice and conversation log alike).

The game's own tags stay in place: the plugin splices the k-th tag run of the original template
wherever the text says `[k]`. Leading and trailing runs (voice style, catchphrase wrapper) are kept
as they are; tag runs inside the line (names, the topic word, items) become placeholders `@k` that
the model must keep, so the game still fills in names, articles and catchphrases itself.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from protocol import GenRequest, Slot

BALLOON_CALLER = "0x71001424b0"  # Text_Expand caller that builds Mii speech balloons (re/offsets.md)
# Narrator captions (FocusTelop): this caller also builds the conversation log and dialogs, so the plugin marks
# captions (`caption: 1`, plugin 0.6.5+) by the caption window's frame on the call stack.
CAPTION_CALLER = "0x710045e9cc"

_TAG = re.compile(r"\{[0-9]+(?:\.[0-9]+)?\}|\{/\}")
_PLACEHOLDER = re.compile(r"@(\d+)")
# Catchphrase wrapper (WordShortStart / WordShortEnd): always a tag run of its own, on both sides (the plugin
# numbers runs the same way). The start stays in front of the line, the end goes before the final punctuation.
_SOLO = ("{200.8}", "{200.9}")
MAX_PAGES = 4
MAX_BYTES = 600  # the plugin stores at most 599 bytes of text per line (kRelineBytes)
_END_PUNCT = re.compile(r"[.!?…。！？]+$")

# What a tag run stands for, by its most telling tag group (re/msbt.md).
# Tag runs that only style the text (voice/emotion 6, 8, highlight 0, pauses 300, closing tags): inside a line
# they are not shown to the model; they go at the end of the generated line, so open/close pairs stay balanced.
_STYLE_GROUPS = {"0", "6", "8", "300"}

# More specific prefixes first.
_KIND = [("200.19", "a word the game fits to the Mii (his, him, himself, He's, he has, thinks...; see what it was on "
                    "screen): use it in the same role, and never put a verb or 's right after it"),
         ("200.18", "a pronoun (he, his, him, she, they...; see what it was on screen)"), ("200.17", "a Mii's name"),
         ("200.", "a Mii's name or pronoun"), ("5.", "the player's name"), ("2.", "a word or item name"),
         ("100.", "a number"), ("201.", "a grammar helper")]


@dataclass
class Segment:
    text: str = ""       # plain text, or
    run: int = 0         # 1-based index of a tag run in the original template
    tags: str = ""       # that run's tags in plugin notation


def split_template(template: str) -> list[Segment]:
    """Split plugin-notation text into plain-text segments and numbered tag runs."""
    segs: list[Segment] = []
    pos, runs = 0, 0
    for m in _TAG.finditer(template):
        if m.start() > pos:
            segs.append(Segment(text=template[pos:m.start()]))
        tag = m.group()
        if (segs and segs[-1].run and m.start() == pos and tag not in _SOLO
                and segs[-1].tags not in _SOLO):
            segs[-1].tags += tag
        else:
            runs += 1
            segs.append(Segment(run=runs, tags=tag))
        pos = m.end()
    if pos < len(template):
        segs.append(Segment(text=template[pos:]))
    return segs


def is_caption(ev: dict) -> bool:
    return bool(ev.get("caption")) and ev.get("lr") == CAPTION_CALLER


def eligible(ev: dict) -> bool:
    """Mii speech with real words, up to MAX_PAGES speech balloons, and narrator captions. Mii-language turns
    and lines with hand-placed line breaks are left alone."""
    src = ev.get("src") or ""
    if (ev.get("lr") != BALLOON_CALLER and not is_caption(ev)) or not ev.get("key") or ev.get("cut"):
        return False  # "cut": the plugin reported only part of the template
    if "{6.11}" in src or "\n" in src or src.count("{0.4}") >= MAX_PAGES:
        return False
    plain = _TAG.sub("", src)
    return sum(c.isalpha() for c in plain) >= 2  # "Oh, {5.0}!" is a greeting worth rewriting


def describe(tags: str) -> str:
    for prefix, kind in _KIND:
        if "{" + prefix in tags:
            return kind
    return "a game-filled part"


@dataclass
class Page:
    """One speech balloon of a line (lines with page breaks {0.4} have several)."""
    prefix: str   # "[1][2]" kept before the text: style tags, catchphrase start
    suffix: str   # style tags kept after it
    middle_runs: dict[int, str]  # run number -> what it stands for (placeholder @k)
    readable: str  # the text with @k placeholders, for the model; "" = nothing to rewrite, keep the page as is
    end_run: int = 0  # catchphrase end ({200.9}) inside the text: goes before the final punctuation
    style_runs: str = ""  # style-only runs inside the text, appended after the generated text
    verb_runs: tuple[int, ...] = ()  # {200.19} runs (a word fitted to the Mii): no verb may follow
    lower_runs: tuple[int, ...] = ()  # pronoun runs shown lowercase (no {201.3}, not first): can't start the line
    fixed: str = ""  # a page without text: its runs, kept verbatim


@dataclass
class RelinePlan:
    key: str
    pages: list[Page]
    breaks: list[int]  # run number of the page break before pages[1], pages[2], ...

    @property
    def slots(self) -> list[tuple[int, Page]]:
        """Pages the model writes, with their index."""
        return [(i, pg) for i, pg in enumerate(self.pages) if pg.readable]


def is_style(tags: str) -> bool:
    return all(t == "{/}" or t[1:].split(".")[0] in _STYLE_GROUPS for t in _TAG.findall(tags))


def _edge(s: Segment) -> bool:
    """Whitespace, or a tag run that may stay outside the text (style, catchphrase wrapper)."""
    return (not s.run and not s.text.strip()) or (bool(s.run) and (s.tags in _SOLO or is_style(s.tags)))


def _plan_page(segs: list[Segment]) -> Page:
    first = next((i for i, s in enumerate(segs) if not _edge(s)), len(segs))
    if not any(not s.run and s.text.strip() for s in segs):
        return Page("", "", {}, "", fixed="".join(f"[{s.run}]" if s.run else s.text for s in segs))
    last = max(i for i, s in enumerate(segs) if not _edge(s))
    prefix = "".join(f"[{s.run}]" for s in segs[:first] if s.run)
    suffix = "".join(f"[{s.run}]" for s in segs[last + 1:] if s.run)
    middle, runs, end_run, style, verbs, lower = [], {}, 0, "", [], []
    for s in segs[first:last + 1]:
        if s.run and s.tags == "{200.9}":
            end_run = s.run
        elif s.run and is_style(s.tags):
            style += f"[{s.run}]"
        elif s.run and s.tags == "{200.8}":
            prefix += f"[{s.run}]"
        elif s.run:
            runs[s.run] = describe(s.tags)
            if "{200.19}" in s.tags:
                verbs.append(s.run)
            # Lowercase unless capitalized ({201.3}) or at the start of the game's line, where the form itself is
            # capitalized ({200.19} he="He's").
            if ("{200.18}" in s.tags or "{200.19}" in s.tags) and "{201.3}" not in s.tags and middle:
                lower.append(s.run)
            middle.append(f"@{s.run}")
        else:
            middle.append(s.text)
    readable = re.sub(r"\s+", " ", "".join(middle)).strip()
    return Page(prefix, suffix, runs, readable, end_run, style, verb_runs=tuple(verbs), lower_runs=tuple(lower))


def plan(key: str, template: str) -> RelinePlan:
    pages, breaks, cur = [], [], []
    for s in split_template(template):
        if s.run and "{0.4}" in s.tags:
            pages.append(_plan_page(cur))
            breaks.append(s.run)
            cur = []
        else:
            cur.append(s)
    pages.append(_plan_page(cur))
    return RelinePlan(key, pages, breaks)


def fill_values(template: str, shown: str) -> dict[int, str]:
    """What each tag run of one page became on screen ("{2.0}" -> "Cameron Diaz"), by matching the shown text
    (one page) against each page of the template. Returns {} if they don't line up."""
    plain = _TAG.sub("", shown)
    pages, cur = [], []
    for s in split_template(template):
        if s.run and "{0.4}" in s.tags:
            pages.append(cur)
            cur = []
        else:
            cur.append(s)
    pages.append(cur)
    for page in pages:
        pat, runs = "", []
        for s in page:
            if s.run:
                pat += "(.*?)"
                runs.append(s.run)
            else:
                pat += re.escape(s.text)
        m = re.fullmatch(pat, plain, flags=re.S)
        if m:
            return {r: v.strip() for r, v in zip(runs, m.groups()) if v.strip()}
    return {}


CAPTION_NOTE = ("A narrator's caption shown at the bottom of the screen, not spoken: it describes what the Miis are "
                "doing, in the third person and the present tense, like the vanilla caption.")
# Only for captions with placeholders: without them, the model invented "@1 and @2 are ..." (2026-10-11).
CAPTION_PLACEHOLDERS = ("Names and pronouns are placeholders here: write @1, @2... never the names. A placeholder "
                        "that was shown as \"He's\", \"he is\" or \"he has\" already includes its verb: write the "
                        "next words to follow it directly (\"@1 trailing behind\", not \"@1 is trailing\").")
CAPTION_NO_PLACEHOLDERS = ("This caption has no placeholders: refer to the Miis in words (\"they\", \"the two "
                           "of them\"), never with @.")


def _clean(text: str) -> str:
    """Drop control and format characters (Mii names can hold U+0008 from keyboard input; a model that copies one
    gets its line rejected)."""
    return "".join(ch for ch in text if not unicodedata.category(ch).startswith("C") or ch in "\n")


def request(p: RelinePlan, said_as: str, lang: str, template: str = "", caption: bool = False) -> GenRequest:
    """One slot per page; the vanilla line as shown on screen helps the model with names (each placeholder's
    description says what it became, when the shown text matches the template)."""
    said_as = _clean(said_as)
    shown_pages = [re.sub(r"\s+", " ", _TAG.sub("", pg)).strip() for pg in re.split(r"\{0\.4\}", said_as)]
    if len(shown_pages) != len(p.pages):
        shown_pages = [re.sub(r"\s+", " ", _TAG.sub("", said_as)).strip()]
    ctx: dict = {"shown_on_screen": " / ".join(shown_pages)}
    runs = {k: v for _, pg in p.slots for k, v in pg.middle_runs.items()}
    values = fill_values(template, said_as) if template else {}
    runs = {k: f"{v}, on screen: \"{values[k]}\"" if values.get(k) and len(values[k]) <= 40 else v
            for k, v in runs.items()}
    if runs:
        ctx["placeholders"] = {f"@{k}": v for k, v in runs.items()}
    if caption:
        ctx["caption"] = CAPTION_NOTE + " " + (CAPTION_PLACEHOLDERS if runs else CAPTION_NO_PLACEHOLDERS)
    elif len(p.slots) > 1:
        ctx["pages"] = ("One line shown as several speech balloons in a row, one per slot, in order; "
                        "together they are one turn of the conversation.")
    slots = []
    for i, pg in p.slots:
        shown = shown_pages[i] if len(shown_pages) == len(p.pages) else pg.readable
        # A caption is one line in a fixed-width box: about as long as the game's, at most 110 characters.
        most = min(110, len(shown) + 25) if caption else min(120, len(shown) * 2 + 10)
        slots.append(Slot(slot_id=str(i), template=pg.readable, max_chars=max(40, most),
                          keep=[f"@{k}" for k in sorted(pg.middle_runs)]))
    return GenRequest(conv_id=f"reline-{p.key}", kind="caption" if caption else "reline", lang=lang, context=ctx,
                      slots=slots)


# Words that can't follow {200.19} (ThirdPersonWord: the game picks his/him/himself/He's/he has/thinks... to fit the
# Mii): "@1 is trailing" would show as "He's is trailing", "his is". Holds for every form the captions use.
_AUX = {"is", "are", "was", "were", "am", "be", "been", "being", "has", "have", "had", "does", "do", "did", "will",
        "would", "can", "could", "should", "shall", "may", "might", "must", "isn't", "aren't", "wasn't", "doesn't",
        "don't", "didn't", "won't", "can't", "hasn't", "haven't", "seems", "keeps", "gets", "thinks", "loves",
        "likes", "wants", "feels", "says"}


# The speaker's own name: "I'm @2", "My name is @2". A rewrite must keep it that way; "It is so wonderful to meet
# @2!" from "Nice to meet you! I'm @2." had the speaker greet themselves (2026-10-11).
_SELF_NAME = r"(?:I'm|I’m|I am|[Mm]y name is|[Mm]y name's|[Cc]all me|[Kk]nown as|[Tt]his is|[Ii]t's|[Ii]t is|me,)\s+(?:@\d+\s*)*@{k}(?!\d)"


def self_name_lost(pg: Page, text: str) -> bool:
    """The game's line names the speaker ("I'm @2") but the rewrite uses that placeholder some other way."""
    for k in pg.middle_runs:
        pat = _SELF_NAME.replace("{k}", str(k))
        if re.search(pat, pg.readable) and not re.search(pat, text):
            return True
    return False


def lowercase_start(pg: Page, text: str) -> bool:
    """The line starts with a pronoun the game shows in lowercase ("he is rushing ...")."""
    m = re.match(r"\s*@(\d+)", text)
    return bool(m) and int(m.group(1)) in pg.lower_runs


def verb_clash(pg: Page, text: str) -> bool:
    """A {200.19} placeholder followed by a verb or 's: the line would read "He's is ..." or "his is" on screen."""
    for k in pg.verb_runs:
        m = re.search(rf"@{k}(?!\d)('s|’s|\s+([A-Za-z’']+))", text)
        if m and (m.group(1) in ("'s", "’s") or (m.group(2) or "").lower() in _AUX):
            return True
    return False


def _compose_page(pg: Page, text: str) -> str | None:
    found = [int(n) for n in _PLACEHOLDER.findall(text)]
    if (sorted(found) != sorted(pg.middle_runs) or verb_clash(pg, text) or lowercase_start(pg, text)
            or self_name_lost(pg, text)):
        return None
    body = _PLACEHOLDER.sub(lambda m: f"[{m.group(1)}]", text)
    if pg.end_run:
        m = _END_PUNCT.search(body)
        at = m.start() if m else len(body)
        body = body[:at] + f"[{pg.end_run}]" + body[at:]
    return pg.prefix + body + pg.style_runs + pg.suffix


def compose(p: RelinePlan, texts: dict[str, str] | str) -> str | None:
    """Check the placeholders and build the plugin's text: per page, prefix runs + text with [k] + suffix runs,
    pages joined by their page-break runs. `texts` maps slot id (page index) to the generated text; a plain
    string is the text of a one-page line. Returns None if a page is missing or the model dropped, duplicated
    or invented a placeholder."""
    if isinstance(texts, str):
        texts = {str(p.slots[0][0]): texts} if p.slots else {}
    out = ""
    for i, pg in enumerate(p.pages):
        if i:
            out += f"[{p.breaks[i - 1]}]"
        if not pg.readable:
            out += pg.fixed
            continue
        if str(i) not in texts:
            return None
        part = _compose_page(pg, texts[str(i)])
        if part is None:
            return None
        out += part
    return out


def kept_line_ok(key: str, src: str, text: str) -> bool:
    """A line kept from an earlier session (plugin notation, [k]) still passes the checks added since it was
    written (verb after {200.19}, lowercase start, the speaker's own name)."""
    p = plan(key, src)
    model_text = text
    for _, pg in p.slots:
        for k in pg.middle_runs:
            model_text = model_text.replace(f"[{k}]", f"@{k}")
    model_text = re.sub(r"\[\d+\]", "", model_text)
    for _, pg in p.slots:
        if verb_clash(pg, model_text) or self_name_lost(pg, model_text):
            return False
    first = p.slots[0][1] if p.slots else None
    return not (first and lowercase_start(first, model_text))

