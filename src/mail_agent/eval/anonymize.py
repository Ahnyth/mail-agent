"""Turns a corrected message into an anonymised evaluation case.

Rule-based and therefore incomplete: names in free text, diagnoses or unusual identifiers can slip
through. The case is written to ``tests/eval/neu/`` (not checked in) and must be reviewed by a
person before it is moved to ``tests/eval/faelle/``.
"""

import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from ..storage import Correction, MessageDetail

PLACEHOLDER_IBAN = "DE02 1203 0000 0000 2020 51"
"""A valid-looking placeholder IBAN, so cases about bank details keep their meaning."""
MIN_NAME_PART_CHARS = 3
"""Shorter parts of a sender name ("Dr", initials) are not replaced; they would hit ordinary words."""
YAML_WIDTH = 110
"""Line width of the written case files, the same as the project's line length."""
REVIEW_HEADER = (
    "# AUTOMATISCH ANONYMISIERT – vor der Übernahme nach tests/eval/faelle/ vollständig prüfen:\n"
    "# Namen, Diagnosen, Adressen, Telefonnummern, Kunden- und Versichertennummern.\n"
    "# Domain des Absenders ist erhalten (Firmen-Domain, keine Person) – bei Privatadressen ersetzen.\n"
)
"""Heads every generated case file: the anonymisation is incomplete and must be checked by a person."""
FALLBACK_TIME = "T09:00:00+01:00"
"""Time of day for a case whose message has no date: nine in the morning, German winter time."""

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
_PHONE = re.compile(r"(?<!\w)(?:\+49|0)[\d /()-]{6,}\d")
_BIRTH_DATE = re.compile(
    r"(geb\.|geboren am|Geburtsdatum:?)\s*\d{1,2}\.\s?\d{1,2}\.\s?\d{2,4}", re.IGNORECASE
)
_INSURANCE_NUMBER = re.compile(r"\b[A-Z]\d{9}\b")
_STREET = re.compile(
    r"\b[A-ZÄÖÜ][\w.-]*(?:straße|strasse|str\.|weg|allee|platz|gasse|ring|damm)\s*\d+\s?[a-z]?\b"
)
_POSTCODE_CITY = re.compile(r"\b\d{5}\s+[A-ZÄÖÜ][\w-]+")
_TITLED_NAME = re.compile(r"\b(Frau|Herrn?|Dr\.(?: med\.)?)\s+[A-ZÄÖÜ][\w-]+(?:\s+[A-ZÄÖÜ][\w-]+)?")


def anonymize_text(text: str, names: tuple[str, ...] = ()) -> str:
    """Replaces personal data in ``text`` with placeholders.

    Links, mail addresses, IBANs, birth dates, insurance numbers, streets, postcodes with city, phone
    numbers and names after a title are replaced, then every word of ``names``.

    Args:
        text: The text of the mail.
        names: Further names to replace, e.g. the parts of the sender name.

    Returns:
        The anonymised text.
    """
    text = _URL.sub("https://beispiel.example/link", text)
    text = _EMAIL.sub("person@beispiel.example", text)
    text = _IBAN.sub(PLACEHOLDER_IBAN, text)
    text = _BIRTH_DATE.sub(lambda m: f"{m.group(1)} 01.01.1950", text)
    text = _INSURANCE_NUMBER.sub("A123456789", text)
    text = _STREET.sub("Musterstraße 1", text)
    text = _POSTCODE_CITY.sub("12345 Musterstadt", text)
    text = _PHONE.sub("0123 4567890", text)
    text = _TITLED_NAME.sub(lambda m: f"{m.group(1)} Muster", text)
    # Longest names first, so "Müller-Lüdenscheidt" is replaced before "Müller".
    for name in sorted(names, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(name)}\b", "Muster", text, flags=re.IGNORECASE)
    return text


def _name_parts(sender_name: str) -> tuple[str, ...]:
    """Returns the parts of a sender name that are long enough to be replaced safely."""
    return tuple(part for part in re.split(r"[\s,.]+", sender_name) if len(part) >= MIN_NAME_PART_CHARS)


class _BlockDumper(yaml.SafeDumper):
    """Writes multi-line texts as YAML blocks (``|``), so a case file stays readable."""


def _str_representer(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    """Represents a text with line breaks as a block, every other text as usual."""
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_BlockDumper.add_representer(str, _str_representer)


def _expected(correction: Correction) -> dict[str, Any]:
    """Returns the ``soll`` section: the values the person chose in the correction."""
    expected: dict[str, Any] = {
        "kategorie": correction.category.value,
        "prioritaet": correction.urgency.value,
        "entwurf": correction.reply_needed,
    }
    if correction.responsible is not None:
        expected["zustaendig"] = correction.responsible.value
    return expected


def _mail(detail: MessageDetail, today: date) -> dict[str, Any]:
    """Returns the anonymised ``mail`` section; only the domain of the sender is kept."""
    names = _name_parts(detail.sender_name)
    domain = detail.sender_address.rpartition("@")[2]
    sender_name = "Absender Muster" if detail.sender_name else ""
    sender = f"{sender_name} <absender@{domain}>" if sender_name else f"absender@{domain}"
    received = detail.received_at.isoformat() if detail.received_at else f"{today.isoformat()}{FALLBACK_TIME}"
    return {
        "von": sender,
        "betreff": anonymize_text(detail.subject, names),
        "datum": received,
        "anhaenge": [anonymize_text(d.filename, names) for d in detail.documents],
        "text": anonymize_text(detail.body, names) + "\n",
    }


def case_from_correction(detail: MessageDetail, correction: Correction, today: date) -> tuple[str, str]:
    """Builds an anonymised evaluation case from a message the office corrected.

    Args:
        detail: The corrected message.
        correction: The values chosen by the person, with the original ones.
        today: The day of the export; part of the case id.

    Returns:
        The case id and the YAML text of the case, headed by :data:`REVIEW_HEADER`.
    """
    case_id = f"korrektur-{detail.id}-{today:%Y%m%d}"
    data: dict[str, Any] = {
        "id": case_id,
        "beschreibung": f"Aus Korrektur übernommen (vorher {correction.original_category.value}, "
        f"{correction.original_urgency.value}); {correction.note}".rstrip("; "),
        "tags": ["korrektur"],
        "mail": _mail(detail, today),
        "soll": _expected(correction),
    }
    return case_id, REVIEW_HEADER + yaml.dump(
        data, Dumper=_BlockDumper, allow_unicode=True, sort_keys=False, width=YAML_WIDTH
    )


def write_case(directory: Path, case_id: str, text: str) -> Path:
    """Writes a case file ``<case_id>.yaml`` into ``directory`` and returns its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{case_id}.yaml"
    path.write_text(text, encoding="utf-8")
    return path
