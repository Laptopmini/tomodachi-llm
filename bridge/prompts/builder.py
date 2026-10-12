"""Build the chat prompt for one conversation. Stable prefix first (prompt caching)."""

from __future__ import annotations

import json
import re

from protocol import GenRequest

LANGUAGES = {
    "JPja": "Japanese", "USen": "American English", "USes": "Latin American Spanish", "USfr": "Canadian French",
    "USpt": "Brazilian Portuguese", "EUen": "British English", "EUes": "Spanish (Spain)", "EUfr": "French",
    "EUde": "German", "EUit": "Italian", "EUpt": "European Portuguese", "EUnl": "Dutch", "EUru": "Russian",
    "KRko": "Korean", "CNzh": "Simplified Chinese", "TWzh": "Traditional Chinese",
}

SYSTEM = """You write lines of dialogue for Miis in the life-simulation game Tomodachi Life: Living the Dream.
The tone is warm, silly, a little absurd, and family-friendly. Lines are short and spoken aloud by a text-to-speech voice.

For each slot you get the game's own line (the "vanilla" template) as a guide to its purpose and length.
Write a fresh line with the same purpose (a greeting stays a greeting, a question stays a question),
made specific to the characters and the topic. Keep it about as long as the vanilla line, never longer than max_chars.
Never repeat the vanilla line word for word: change the wording, keep the mood and the energy (shouting stays shouting,
an instruction stays an instruction). Sound like the vanilla lines: casual, with contractions (I'm, don't, it's).

Rules:
- One line of plain text per slot. No markup, no brackets or braces, no emoji, no stage directions, no quotes around the line.
- Spell out symbols and numbers the way a person would say them.
- If needs_word is true, the line must contain the topic word exactly as given.
- Use names exactly as given. Never invent new characters.
- In the template, {200.x} marks a Mii name, {2.0} the topic word, {201.x} grammar helpers and {6.x}/{300.x}/{0.x} voice or style
  tags; they only tell you where those things go. Write natural text instead.
- Some vanilla lines contain placeholders like @1 or @2 (listed in the slot under "keep"). The game fills them in
  (a name, the topic word, an item), so write the placeholder itself, never what it stands for: "@2 is so cool!",
  not "Diaz is so cool!". Use every placeholder in "keep" exactly once; you may move it within the line; never add
  others. "shown_on_screen" and the placeholder descriptions only tell you what the placeholders became this time.
  Keep who each placeholder is: in "Nice to meet you! I'm @2.", @2 is the speaker's own name, so never write
  "Nice to meet @2!".
- If the context has "previous_versions", the game already used those lines: write one that differs from all of them.
- If the context has "pages", the slots are consecutive speech balloons of one turn: write them as one flowing
  turn, one balloon per slot.
- If the conversation kind is "caption", the slot is not dialogue but a narrator's caption shown at the bottom of
  the screen: describe what the Miis are doing in the third person and the present tense, as one sentence about as
  long as the vanilla caption. It is not spoken aloud.
- Write in the language you are told to use.
- Reply with JSON only: {"lines": [{"slot_id": "...", "text": "..."}]}, one entry per slot."""

RESPONSE_SCHEMA = {
    "name": "lines",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"slot_id": {"type": "string"}, "text": {"type": "string"}},
                    "required": ["slot_id", "text"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["lines"],
        "additionalProperties": False,
    },
}

_TAG = re.compile(r"\{[0-9]+(?:\.[0-9]+)?\}|\{/\}")


def readable_template(template: str) -> str:
    """Turn the plugin's tag notation into placeholders a model can read."""
    t = template.replace("{2.0}", "[TOPIC WORD]").replace("{200.0}", "[NAME]").replace("{200.1}", "[NAME 2]")
    return re.sub(r"\s+", " ", _TAG.sub("", t)).strip()


def build_messages(req: GenRequest) -> list[dict[str, str]]:
    lang = LANGUAGES.get(req.lang, "the same language as the vanilla lines")
    ctx = {k: v for k, v in req.context.items() if v}
    slots = [
        {
            "slot_id": s.slot_id,
            "vanilla": readable_template(s.template),
            "max_chars": s.max_chars,
            "needs_word": s.needs_word,
            **({"keep": s.keep} if s.keep else {}),
            **({"placeholders": s.placeholders} if s.placeholders else {}),
            **({"about": s.label} if s.label else {}),
        }
        for s in req.slots
    ]
    user = (
        f"Language: {lang}\n"
        f"Conversation kind: {req.kind}\n"
        f"Context: {json.dumps(ctx, ensure_ascii=False)}\n"
        f"Slots: {json.dumps(slots, ensure_ascii=False)}"
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
