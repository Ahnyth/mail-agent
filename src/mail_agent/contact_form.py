"""Messages from the contact form of the own website (Elementor, sent by PHPMailer).

Such a notification comes from the own mailbox (e.g. ``info@musterpflege.example``) to the same address;
the real sender is the visitor, whose name and e-mail address are only form fields in the body::

    Vorname: Max
    Nachname: Muster
    E-Mail-Adresse: max@example.org
    Telefon: 0170 1234567
    Ihre Nachricht: Wir suchen einen Pflegedienst ...
    on
    ---
    Datum: Oktober 1, 2026
    Seiten URL: https://musterpflege.example/kontakt/
    Benutzer Agent: Mozilla/5.0 ...
    Remote IP: 203.0.113.7
    Unterstützt von: Elementor

The message is rewritten so that the visitor is the sender (display, consistency per sender, reply address)
and only the form content goes to the AI. The visitor typed the address, so it is **unverified**: such a
message is never treated as a known or internal sender (see ``analysis/signals.py``).
"""

import re
from dataclasses import dataclass, replace

from .models import MailMessage

_SEPARATOR = re.compile(r"^-{3,}\s*$")
"""The line between the form fields and the technical data."""
_FIELD = re.compile(r"^(?P<label>[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß \-/]{0,40}?)\s*:\s?(?P<value>.*)$")
"""One ``Label: value`` line."""
_EMAIL = re.compile(r"^[^@\s<>\"',;]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,24}$")
"""A plausible e-mail address; anything else typed into the field is not used as reply address."""

NAME_FIELDS = ("vorname", "nachname", "name")
"""Labels of the name fields, joined in this order."""
EMAIL_FIELDS = ("e-mail-adresse", "e-mail", "email")
"""Labels of the e-mail field; the first one present counts."""
PHONE_FIELDS = ("telefon", "telefonnummer")
"""Labels of the phone field; the first one present counts."""
MESSAGE_FIELDS = ("ihre nachricht", "nachricht", "anliegen")
"""Labels of the message field; the first one present counts."""
FORM_FIELDS = frozenset((*NAME_FIELDS, *EMAIL_FIELDS, *PHONE_FIELDS, *MESSAGE_FIELDS, "betreff"))
"""Labels that start a new field above the separator; other ``Label:`` lines belong to the message."""
TECHNICAL_FIELDS = frozenset(("benutzer agent", "remote ip", "unterstützt von"))
"""Meta data below the separator that marks an Elementor notification. IP address and browser are personal
data the analysis does not need (Guidelines 2.7), so they are dropped."""
PAGE_FIELD = "seiten url"
"""Label of the page the form was sent from."""
CHECKBOX_VALUE = "on"
"""Elementor writes the value of a ticked checkbox (consent) as a line of its own."""
MAX_FIELD_CHARS = 120
"""Longest name or phone number kept, so that pasted text cannot fill the sender field."""
MAX_PAGE_CHARS = 300
"""Longest page address kept; it is only shown for orientation."""

Fields = dict[str, list[str]]
"""The lines of every form field by its lower-case label."""


@dataclass(frozen=True)
class ContactForm:
    """The fields of one contact form notification; empty texts for fields the visitor left out."""

    name: str
    email: str
    """The typed address if it looks valid, else empty; never verified."""
    phone: str
    message: str
    page: str
    """The address of the page with the form."""


def parse_contact_form(body: str) -> ContactForm | None:
    """Returns the form fields, or ``None`` if ``body`` is no Elementor notification with an e-mail field."""
    lines = body.replace("\r\n", "\n").split("\n")
    separator = next((i for i, line in enumerate(lines) if _SEPARATOR.match(line)), None)
    if separator is None:
        return None
    meta = {label: " ".join(value).strip() for label, value in _fields(lines[separator + 1 :], None)}
    if not TECHNICAL_FIELDS & set(meta):
        return None
    form = dict(_fields(lines[:separator], FORM_FIELDS))
    if not any(label in form for label in EMAIL_FIELDS):
        return None
    return _contact_form(form, meta.get(PAGE_FIELD, ""))


def form_text(form: ContactForm) -> str:
    """Returns what the office and the AI read: the form content without technical data."""
    page = f" (Seite: {form.page})" if form.page else ""
    lines = [
        f"Nachricht über das Kontaktformular der Website{page}.",
        f"Name: {form.name or '(nicht angegeben)'}",
        f"E-Mail: {form.email or '(keine gültige Adresse angegeben)'}",
        f"Telefon: {form.phone or '(nicht angegeben)'}",
        "",
        form.message or "(keine Nachricht)",
    ]
    return "\n".join(lines)


def from_contact_form(message: MailMessage) -> MailMessage:
    """Returns ``message`` with the visitor as sender, unchanged if it is no contact form notification."""
    form = parse_contact_form(message.body)
    if form is None:
        return message
    address = form.email or message.sender_address
    return replace(
        message,
        sender_name=form.name,
        sender_address=address,
        reply_to=address,
        body=form_text(form),
        contact_form=True,
    )


def _fields(lines: list[str], starters: frozenset[str] | None) -> list[tuple[str, list[str]]]:
    """Returns the ``Label: value`` blocks; with ``starters`` only those labels open one (multi-line text)."""
    fields: list[tuple[str, list[str]]] = []
    for line in lines:
        match = _FIELD.match(line.strip())
        label = match["label"].strip().lower() if match else ""
        if match and (starters is None or label in starters):
            fields.append((label, [match["value"].strip()]))
        elif fields:
            fields[-1][1].append(line.rstrip())
    return fields


def _contact_form(form: Fields, page: str) -> ContactForm:
    """Builds the contact form from the fields above the separator; long values are cut."""
    email = _first_value(form, EMAIL_FIELDS)
    name = " ".join(value for value in (_value(form, label) for label in NAME_FIELDS) if value)
    return ContactForm(
        name=name[:MAX_FIELD_CHARS],
        email=email if _EMAIL.match(email) else "",
        phone=_first_value(form, PHONE_FIELDS)[:MAX_FIELD_CHARS],
        message=_message(form),
        page=page[:MAX_PAGE_CHARS],
    )


def _value(form: Fields, label: str) -> str:
    """Returns the field ``label`` as one line, empty if it is missing."""
    return " ".join(form.get(label, [])).strip()


def _first_value(form: Fields, labels: tuple[str, ...]) -> str:
    """Returns the first of the fields ``labels`` that is present, empty if none is."""
    return next((_value(form, label) for label in labels if label in form), "")


def _message(form: Fields) -> str:
    """Returns the message field without the checkbox value and blank lines Elementor adds below it."""
    lines = next((form[label] for label in MESSAGE_FIELDS if label in form), [])
    while lines and lines[-1].strip() in {"", CHECKBOX_VALUE}:
        lines = lines[:-1]
    return "\n".join(lines).strip()
