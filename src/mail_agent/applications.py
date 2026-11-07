"""Applications: checklist of documents and the fixed confirmation of receipt (no AI text)."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TEMPLATE = """{anrede}

vielen Dank für Ihre Bewerbung und Ihr Interesse an der {firma}. Sie ist bei uns eingegangen, \
wir melden uns nach Durchsicht bei Ihnen.
{fehlende_unterlagen}"""
"""The confirmation of receipt when ``config/vorlagen/`` has none; the placeholders are filled in."""

COVER_LETTER = "Anschreiben"
"""Label of the cover letter; a long mail body counts as one."""
EXAM = "Examen / Berufserlaubnis"
"""Label of the exam certificate; not needed for trainees and helpers."""
DOCUMENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (COVER_LETTER, ("anschreiben", "bewerbungsschreiben", "motivationsschreiben", "cover")),
    ("Lebenslauf", ("lebenslauf", "vita", "curriculum", "cv")),
    ("Zeugnisse", ("zeugnis", "zeugnisse", "beurteilung")),
    (EXAM, ("examen", "urkunde", "berufserlaubnis", "erlaubnis zum führen", "anerkennung")),
)
"""(label, keywords in file names or the first part of the attachment text) of every expected document."""
REQUESTED = ("Lebenslauf", "Zeugnisse", EXAM)
"""Only these are asked for in the confirmation; a missing cover letter is shown in the overview only."""
_NO_EXAM = re.compile(r"ausbildung|azubi|praktikum|schüler|helfer|hilfskraft|fsj|bufdi", re.IGNORECASE)
"""Positions that need no exam certificate."""
_TEXT_PREFIX = 2_000
"""How much of an attachment text is searched; the kind of document shows at the beginning."""
_OWN_LETTER_CHARS = 250
"""A mail body of at least this many characters counts as cover letter."""
_SHORT_KEYWORD_CHARS = 3
"""Keywords up to this length (cv) match only as a whole word; inside other words they are chance hits."""


@dataclass(frozen=True)
class Checklist:
    """Which expected documents an application contains and which are missing."""

    present: tuple[str, ...]
    missing: tuple[str, ...]
    exam_required: bool
    """The position needs an exam certificate."""

    @property
    def complete(self) -> bool:
        """``True`` when no expected document is missing."""
        return not self.missing


def checklist(subject: str, body: str, documents: Iterable[tuple[str, str | None]]) -> Checklist:
    """Checks which expected documents an application contains.

    Args:
        subject: The subject of the mail.
        body: The text of the mail; a long one counts as cover letter.
        documents: (file name, extracted text or ``None``) of every attachment.

    Returns:
        The present and missing documents in the order of :data:`DOCUMENTS`.
    """
    haystacks = [f"{name} {(text or '')[:_TEXT_PREFIX]}".lower() for name, text in documents]
    exam_required = not _NO_EXAM.search(f"{subject}\n{body}")
    present: list[str] = []
    missing: list[str] = []
    for label, keywords in DOCUMENTS:
        if label == EXAM and not exam_required:
            continue
        (present if _is_present(label, keywords, haystacks, body) else missing).append(label)
    return Checklist(tuple(present), tuple(missing), exam_required)


def load_template(path: Path | None) -> str:
    """Reads a reply template from ``config/vorlagen/`` (default: applications); ``#`` lines are comments."""
    if path is None:
        return DEFAULT_TEMPLATE
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.startswith("#")).strip("\n")


def fill_template(template: str, *, salutation: str, organization: str) -> str:
    """Returns a reply template with ``{anrede}`` and ``{firma}`` filled in (e.g. for care requests)."""
    return _fill(template, salutation, organization).strip()


def confirmation(template: str, *, salutation: str, organization: str, items: Checklist) -> str:
    """Returns the confirmation of receipt with a request for the missing documents.

    Args:
        template: The template with ``{anrede}``, ``{firma}`` and ``{fehlende_unterlagen}``.
        salutation: The first line of the letter.
        organization: The own company name.
        items: The checklist of the application.

    Returns:
        The filled-in text without signature.
    """
    text = _fill(template, salutation, organization)
    return text.replace("{fehlende_unterlagen}", _missing_text(items)).rstrip()


def _is_present(label: str, keywords: tuple[str, ...], haystacks: list[str], body: str) -> bool:
    """Checks whether a document is among the attachments; a long mail body is a cover letter."""
    if any(_mentions(h, k) for k in keywords for h in haystacks):
        return True
    return label == COVER_LETTER and len(body.strip()) >= _OWN_LETTER_CHARS


def _mentions(haystack: str, keyword: str) -> bool:
    """Checks for ``keyword``: short ones (cv) as a whole word only, longer ones in compounds too."""
    if len(keyword) > _SHORT_KEYWORD_CHARS:
        return keyword in haystack
    return re.search(rf"(?<![a-zäöü]){re.escape(keyword)}(?![a-zäöü])", haystack) is not None


def _fill(template: str, salutation: str, organization: str) -> str:
    """Fills in ``{anrede}`` and ``{firma}``."""
    return template.replace("{anrede}", salutation).replace("{firma}", organization)


def _missing_text(items: Checklist) -> str:
    """Returns the request for the missing documents that are asked for; empty if none is missing."""
    requested = [item for item in items.missing if item in REQUESTED]
    if not requested:
        return ""
    return (
        "\nDamit wir Ihre Bewerbung vollständig prüfen können, schicken Sie uns bitte noch: "
        f"{', '.join(requested)}.\n"
    )
