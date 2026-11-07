"""Evaluation cases: one fictitious mail with its expected result per YAML file (tests/eval/faelle)."""

import mimetypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..attachments import read_attachments
from ..models import Category, MailMessage, Responsible, Urgency
from ..parsing import parse_message
from ..sample_data import SampleMail, build_message, minimal_pdf


class EvalCaseError(ValueError):
    """A case file is invalid; the message names the file and the field."""


@dataclass(frozen=True)
class Expected:
    """The expected result of one case; ``None`` fields are not checked.

    Attributes:
        category: The expected category.
        urgency: The expected urgency.
        reply_needed: Whether a reply draft is expected.
        responsible: Who should handle the mail.
        warning: Whether a security warning is expected.
        deadline: Whether a deadline should be found.
        attachments_match: Whether the attachments fit the mail.
    """

    category: Category
    urgency: Urgency
    reply_needed: bool
    responsible: Responsible | None = None
    warning: bool | None = None
    deadline: bool | None = None
    attachments_match: bool | None = None


@dataclass(frozen=True)
class EvalCase:
    """One fictitious mail with its expected result.

    Attributes:
        id: The id of the case; equals the file name without ``.yaml``.
        description: What the case is about.
        tags: Free tags, e.g. ``weiterleitung``.
        message: The mail, parsed and with its attachments read like in production.
        expected: The expected result.
    """

    id: str
    description: str
    tags: tuple[str, ...]
    message: MailMessage
    expected: Expected


def _field(data: dict[str, Any], key: str, kind: type, source: str, *, required: bool = True) -> Any:
    """Returns ``data[key]`` after checking its type.

    Args:
        data: One mapping of the case file.
        key: The field to read.
        kind: The type the value must have.
        source: The file name, for the error message.
        required: ``False`` returns ``None`` for a missing field instead of failing.

    Raises:
        EvalCaseError: The field is missing or has the wrong type.
    """
    value = data.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, kind):
        raise EvalCaseError(f"{source}: {key} fehlt oder hat den falschen Typ")
    return value


def _enum[E: (Category, Urgency, Responsible)](enum: type[E], value: str, key: str, source: str) -> E:
    """Returns the enum member with ``value``.

    Raises:
        EvalCaseError: ``value`` is not a value of ``enum``.
    """
    try:
        return enum(value)
    except ValueError:
        raise EvalCaseError(f"{source}: {key} = {value!r} ist nicht erlaubt") from None


def _attachment(entry: str | dict[str, Any], source: str) -> tuple[str, str, bytes]:
    """Builds an attachment from a file name, or from ``{name, text}`` for a PDF with a real text layer."""
    if isinstance(entry, dict):
        name, text = entry.get("name"), entry.get("text")
        if not isinstance(name, str) or not isinstance(text, str):
            raise EvalCaseError(f"{source}: mail.anhaenge: name und text angeben")
        return name, "application/pdf", minimal_pdf(text)
    name = entry
    if name.lower().endswith(".pdf"):
        return name, "application/pdf", minimal_pdf(f"Testdokument {name}")
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return name, content_type, b"Testanhang"


def _sample_mail(case_id: str, mail: dict[str, Any], source: str) -> SampleMail:
    """Builds the mail of a case from its ``mail`` section.

    Raises:
        EvalCaseError: A field is missing, has the wrong type or the date is no ISO date.
    """
    sent = _field(mail, "datum", str, source)
    try:
        sent_at = datetime.fromisoformat(sent)
    except ValueError:
        raise EvalCaseError(f"{source}: mail.datum ist kein ISO-Datum") from None
    attachments = mail.get("anhaenge") or []
    if not isinstance(attachments, list) or not all(isinstance(a, str | dict) for a in attachments):
        raise EvalCaseError(f"{source}: mail.anhaenge muss eine Liste sein")
    return SampleMail(
        filename=f"{case_id}.eml",
        sender=_field(mail, "von", str, source),
        subject=_field(mail, "betreff", str, source),
        sent=sent_at,
        body=_field(mail, "text", str, source),
        html=bool(mail.get("html", False)),
        attachments=tuple(_attachment(a, source) for a in attachments),
    )


def _expected(soll: dict[str, Any], responsible: str | None, source: str) -> Expected:
    """Builds the expected result from the ``soll`` section; ``responsible`` was read from it before.

    Raises:
        EvalCaseError: A field is missing, has the wrong type or an unknown value.
    """
    return Expected(
        category=_enum(Category, _field(soll, "kategorie", str, source), "soll.kategorie", source),
        urgency=_enum(Urgency, _field(soll, "prioritaet", str, source), "soll.prioritaet", source),
        reply_needed=_field(soll, "entwurf", bool, source),
        responsible=_enum(Responsible, responsible, "soll.zustaendig", source) if responsible else None,
        warning=_field(soll, "warnung", bool, source, required=False),
        deadline=_field(soll, "frist", bool, source, required=False),
        attachments_match=_field(soll, "anhang_passt", bool, source, required=False),
    )


def _tags(data: dict[str, Any]) -> tuple[str, ...]:
    """Returns the tags of a case as text; anything but a list counts as no tags."""
    tags = data.get("tags") or []
    return tuple(str(t) for t in tags) if isinstance(tags, list) else ()


def parse_case(data: object, source: str) -> EvalCase:
    """Builds a case from the content of one case file.

    Args:
        data: The loaded YAML content.
        source: The file name, for error messages.

    Returns:
        The case with its parsed mail and expected result.

    Raises:
        EvalCaseError: The content is no mapping, or a field is missing or invalid.
    """
    if not isinstance(data, dict):
        raise EvalCaseError(f"{source}: keine Zuordnung")
    case_id = _field(data, "id", str, source)
    mail = _field(data, "mail", dict, source)
    expected = _field(data, "soll", dict, source)
    sample = _sample_mail(case_id, mail, source)
    responsible = _field(expected, "zustaendig", str, source, required=False)
    return EvalCase(
        id=case_id,
        description=str(data.get("beschreibung", "")),
        tags=_tags(data),
        # attachments are read like in production; generated PDFs have a text layer, no OCR needed
        message=read_attachments(parse_message(bytes(build_message(sample))), None),
        expected=_expected(expected, responsible, source),
    )


def load_cases(directory: Path, only: str | None = None) -> list[EvalCase]:
    """Loads every case file of ``directory``, sorted by name.

    Args:
        directory: The folder with the ``.yaml`` files.
        only: Only cases whose file name contains this text; ``None`` loads all.

    Returns:
        The cases.

    Raises:
        EvalCaseError: A file is no valid YAML, a case is invalid or its id differs from the file name.
    """
    cases = []
    for path in sorted(directory.glob("*.yaml")):
        if only and only not in path.stem:
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise EvalCaseError(f"{path.name}: kein gültiges YAML ({exc})") from None
        case = parse_case(data, path.name)
        if case.id != path.stem:
            raise EvalCaseError(f"{path.name}: id {case.id!r} passt nicht zum Dateinamen")
        cases.append(case)
    return cases
