"""Voice commands: a small, local rule set – no AI, no cloud.

The result of a command is a page, a text to read aloud or a draft. Nothing is ever sent.
"""

import re
from dataclasses import dataclass
from typing import Literal

CommandKind = Literal["urgent", "briefing", "open", "reply", "unknown"]
"""What a command asks for: the urgent mails, the briefing, a page, a dictated reply, or nothing known."""

PAGES: tuple[tuple[str, str], ...] = (
    ("frist", "/fristen"),
    ("rechnung", "/rechnungen"),
    ("zahlung", "/rechnungen"),
    ("bewerbung", "/bewerbungen"),
    ("wiedervorlage", "/wiedervorlage"),
    ("unbeantwortet", "/wiedervorlage"),
    ("briefing", "/briefing"),
    ("posteingang", "/"),
    ("übersicht", "/"),
)
"""(spoken word, page) pairs for "öffne …"; the first word found in the command decides."""

_REPLY = re.compile(
    r"^\s*(antworte|antwort|entwurf|schreibe?|schreib zurück)\b[\s,:.\-–]*(?P<text>.*)$",
    re.IGNORECASE | re.DOTALL,
)
"""A dictated reply: the command word, then the words of the reply."""
_URGENT = re.compile(
    r"\b(dringend\w*|eilig\w*|wichtig\w*)\b.*\b(mails?|nachrichten|vor)\b|\bwas ist dringend\b", re.IGNORECASE
)
"""A question for the urgent mails."""
_BRIEFING = re.compile(
    r"\bwas steht (heute )?an\b|\b(morgen-?)?briefing\b.*\bvor\b|\bheute an\b|\btagesüberblick\b",
    re.IGNORECASE,
)
"""A request for the daily briefing."""
_OPEN = re.compile(r"\b(öffne|zeige?|zeig mir|gehe? zu|wechsle zu)\b", re.IGNORECASE)
"""A request to open a page."""
_SENTENCE_END = (".", "!", "?")
"""Characters that already end a dictated sentence."""


@dataclass(frozen=True)
class Command:
    """One recognized voice command."""

    kind: CommandKind
    text: str = ""
    """Dictated reply text (``reply``) or the target path (``open``)."""


def parse_command(transcript: str) -> Command:
    """Recognizes the command in a spoken ``transcript``; a dictated reply is checked first.

    Args:
        transcript: The text the speech recognition heard.

    Returns:
        The command, ``unknown`` if no rule matches.
    """
    text = transcript.strip()
    reply = _REPLY.match(text)
    if reply:
        return Command("reply", _clean_dictation(reply.group("text")))
    if _URGENT.search(text):
        return Command("urgent")
    if _BRIEFING.search(text):
        return Command("briefing")
    if _OPEN.search(text):
        target = _page(text)
        if target:
            return Command("open", target)
    return Command("unknown")


def dictated_reply(salutation: str, dictation: str, signature: str) -> str:
    """Returns salutation, the dictated words as spoken, and signature; a full stop is added if missing."""
    body = dictation if dictation.endswith(_SENTENCE_END) else f"{dictation}."
    return f"{salutation}\n\n{body}\n\n{signature}"


def _clean_dictation(text: str) -> str:
    """Returns the dictated words without surrounding quotes, starting with a capital letter."""
    text = text.strip().strip("\"'„“")
    return text[:1].upper() + text[1:] if text else ""


def _page(text: str) -> str | None:
    """Returns the page of the first known word in ``text``, or ``None``."""
    lowered = text.lower()
    return next((path for word, path in PAGES if word in lowered), None)
