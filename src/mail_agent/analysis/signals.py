"""Deterministic signals of a message: sender status, payment data, deadlines, forwards, fraud hints.

Used by the rules, by the decision whether to ask the AI, and for security warnings that must not
depend on a model (changed bank details, unknown sender demanding payment, links to login pages).
Everything here reads the mail as data; nothing in a mail can change what is checked.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..models import Deadline, MailMessage, PaymentDetails
from ..senders import KnownSender, SenderDirectory, normalize_iban

# --- text patterns ------------------------------------------------------------------------------------------

GREETING = re.compile(r"^(sehr geehrte[rns]?|liebe[rs]?|hallo|guten tag|guten morgen|moin)\b", re.IGNORECASE)
"""The salutation line of a mail; it carries no content of its own."""
CLOSING = re.compile(
    r"^(mit )?(freundlichen|besten|herzlichen|liebe[n]?|viele[n]?|beste|schöne) gr(ü|ue)(ß|ss)(e|en)\b"
    r"|^(lg|vg|mfg)\b",
    re.IGNORECASE,
)
"""The closing formula of a mail; the signature follows it."""
FORWARD_MARKER = re.compile(
    r"^-{2,}\s*(forwarded message|weitergeleitete nachricht|original message|ursprüngliche nachricht)"
    r"\s*-{2,}$",
    re.IGNORECASE,
)
"""The separator line that mail programs put above a forwarded or quoted original."""
_FORWARD_SUBJECT = re.compile(r"^\s*(fwd?|wg|weitergeleitet)\s*:", re.IGNORECASE)
"""Subject prefixes of a forward in German and English mail programs."""

_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
"""An IBAN as written in mails and invoices, with or without the usual blocks of four."""
_AMOUNT = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})\s*(?:€|eur\b|euro\b)", re.IGNORECASE)
"""A euro amount in German notation (``1.234,56 €``)."""
_INVOICE_NUMBER = re.compile(
    r"rechnung(?:s?-?\s*(?:nr\.?|nummer))?\s*:?\s*((?:[A-Z]{1,4}-?)?\d[\w\-/]{2,})", re.IGNORECASE
)
"""An invoice number after "Rechnung", "Rechnungsnr." or "Rechnungsnummer"."""
_DATE = r"(\d{1,2})\.\s?(\d{1,2})\.(\d{4}|\d{2})?"
"""A German date (day, month, optional year) as the end of the date patterns below."""
_DUE_DATE = re.compile(r"(?:fällig\w*|zahlbar)\s+(?:am|bis|zum)?\s*(?:zum\s+)?" + _DATE, re.IGNORECASE)
"""The due date of a payment ("zahlbar bis 20.11.")."""
_DEADLINE_DATE = re.compile(
    r"(?:bis\s+(?:spätestens\s+)?(?:zum\s+|am\s+|einschließlich\s+)?|spätestens\s+(?:am\s+|bis\s+(?:zum\s+)?)?"
    r"|frist\w*\s+(?:bis\s+)?(?:zum\s+|am\s+)?)" + _DATE,
    re.IGNORECASE,
)
"""A deadline with a date ("bis spätestens 05.01.", "Frist bis zum 31.12.2026")."""
_PERIOD_BEFORE = re.compile(r"\d{1,2}\.\s?\d{1,2}\.(?:\d{4}|\d{2})?\s*(?:[-–]\s*)?$")
"""A start date directly before "bis …": the text names a period, not a deadline."""
_PAYMENT_TERM_BEFORE = re.compile(
    r"(?:zahlbar|fällig\w*|zahlungsziel\w*|zu\s+(?:be)?zahlen)\s*$", re.IGNORECASE
)
"""A payment word directly before "bis …": a payment term, kept in the payment data instead."""
_MONTH_DEADLINE = re.compile(
    r"innerhalb\s+(?:eines|von\s+einem)\s+monats?|binnen\s+eines\s+monats|einmonatig", re.IGNORECASE
)
"""A deadline of one month without a date, typical for the appeal period of a notice."""

PAYMENT_WORDS = (
    "rechnung", "zahlung", "überweis", "bezahlen", "begleichen", "fällig", "offener betrag",
    "offenen betrag", "mahnung", "inkasso", "zahlbar",
)  # fmt: skip
"""Words that ask for a payment."""
_PAYMENT_REQUEST = re.compile(r"(?<![a-zäöüß])(" + "|".join(PAYMENT_WORDS) + ")", re.IGNORECASE)
"""Only at the start of a word: "Abrechnung", "Berechnung" or "Anzahlungsmodell" are no payment request."""
UNKNOWN_PAYMENT_WARNING = "Zahlungsaufforderung von einem unbekannten Absender"
"""Start of the one fixed warning that rests on a weak signal; it is shown, but does not block a draft."""
PRESSURE_WORDS = (
    "sofort", "umgehend", "unverzüglich", "innerhalb von 24 stunden", "innerhalb von 48 stunden",
    "heute noch", "letzte mahnung", "inkasso", "gesperrt", "sperrung", "vertraulich behandeln",
    "absolut vertraulich", "diskret",
)  # fmt: skip
"""Words that put pressure on the reader, typical for fraud.

Not plain "vertraulich": every confidentiality footer ("enthält vertrauliche Informationen") has it.
"""
_BANK_CHANGE = re.compile(
    r"(neue|neuen|geänderte|geänderten|aktualisierte)\s+(bank|konto)(verbindung|daten)"
    r"|(bank|konto)(verbindung|daten)\w*\s+((hat|haben)\s+sich\s+|(wurde|wurden|ist|sind)\s+)?geändert"
    r"|neue[sn]?\s+(geschäfts)?konto",
    re.IGNORECASE,
)
"""A notice of new or changed bank details, the most common invoice fraud."""
_LOGIN_URL = re.compile(
    r"https?://\S*(login|log-in|signin|sign-in|anmeld|verif|passwort|password|account|konto|secure)\S*",
    re.IGNORECASE,
)
"""A link that leads to a login or verification page."""
_LOGIN_PHRASE = re.compile(
    r"(konto|zugang|zugangsdaten|login|passwort|identität)\w*\s+(zu\s+)?(bestätigen|verifizieren|"
    r"aktualisieren|entsperren)|jetzt\s+(anmelden|einloggen)|hier\s+(anmelden|einloggen)",
    re.IGNORECASE,
)
"""A request to log in or to confirm an account, typical for phishing."""

ASK_FOR_REPLY = re.compile(
    r"\?|bitte\s+(um\s+)?(eine\s+)?(kurze\s+)?(rückmeldung|rückruf|bestätigung|antwort|nachricht)"
    r"|(teilen|geben)\s+sie\s+(uns\s+)?(bitte\s+)?(mit|bescheid)|bitten\s+(wir\s+)?(sie\s+)?um\s+(ihre\s+)?"
    r"(rückmeldung|stellungnahme|antwort|bestätigung)|melden\s+sie\s+sich|bitte\s+bestätigen",
    re.IGNORECASE,
)
"""A question or a request for an answer in the sender's own words."""

# --- numbers ------------------------------------------------------------------------------------------------

IBAN_MIN_LENGTH = 15
"""Shortest IBAN in the ISO 13616 register (Norway)."""
IBAN_MAX_LENGTH = 34
"""Longest IBAN that ISO 13616 allows."""
_IBAN_HEADER_CHARS = 4
"""Country code and check digits at the start of an IBAN; the check moves them to the end (ISO 13616)."""
_IBAN_DIGIT_BASE = 36
"""Base in which letters become numbers for the check: A = 10 … Z = 35 (ISO 13616)."""
_IBAN_MODULUS = 97
"""Divisor of the IBAN check (ISO 7064 MOD 97-10); a valid IBAN leaves the remainder 1."""
_CENTURY = 2000
"""Added to a two-digit year ("05.01.27" means 2027); mails here never refer to the 20th century."""
_SHORT_YEAR_DIGITS = 2
"""Number of digits of a short year ("27")."""
PAST_DATE_TOLERANCE_DAYS = 60
"""A date without year up to this many days before the mail stays in the mail's year (a deadline that
just passed); an earlier one is read as next year ("bis zum 05.01." written in December)."""
DECEMBER = 12
"""Month number of December; a month later is January of the next year."""
DEADLINE_CONTEXT_CHARS = 120
"""Longest text shown next to a deadline: the line around the date, shortened if needed."""
_LOOK_BEHIND_CHARS = 40
"""How far before a "bis …" the text is checked for a start date or a payment word."""
_ELLIPSIS = " …"
"""Marks a shortened text."""


@dataclass(frozen=True)
class MailSignals:
    """The deterministic facts about one message that the rules, the routing and the warnings use."""

    sender: KnownSender | None
    """The entry in the list of known senders, ``None`` for an unknown sender or a contact form."""
    internal: bool
    """Sent from an own domain, and replies go back there."""
    forwarded: bool
    """The mail forwards another one (subject prefix or separator line)."""
    own_text: str
    """The sender's own words: above a forwarded part, without greeting, closing and signature."""
    ibans: tuple[str, ...]
    """Valid IBANs in the mail or its attachments, normalized, without duplicates."""
    payment_request: bool
    """The mail or an attachment asks for a payment."""
    pressure: tuple[str, ...]
    """The pressure words found (see :data:`PRESSURE_WORDS`)."""
    bank_change: bool
    """The mail announces new or changed bank details."""
    iban_mismatch: tuple[str, ...]
    """IBANs that differ from the ones deposited for the known sender."""
    login_links: bool
    """The mail links to a login page or asks to log in."""
    deadlines: tuple[Deadline, ...]
    """Deadlines in the body or the attachments, earliest first."""
    has_attachments: bool
    """The mail has at least one attachment."""
    reply_elsewhere: str | None = None
    """Domain of a Reply-To that does not belong to the sender (contact forms, impersonation)."""
    impersonation_risk: bool = False
    """The sender looks internal or known, but replies would go to a foreign domain."""

    @property
    def known(self) -> bool:
        """Known sender or colleague – only if replies also go back there (From is easy to fake)."""
        return (self.sender is not None or self.internal) and self.reply_elsewhere is None

    @property
    def pure_forward(self) -> bool:
        """A forward without own words, or a forward by a colleague (answered internally, not by draft)."""
        return self.forwarded and (self.internal or not self.own_text)


@dataclass(frozen=True)
class _SenderCheck:
    """Who a message claims to come from and whether replies would go back there."""

    sender: KnownSender | None
    claims_internal: bool
    reply_elsewhere: str | None

    @property
    def internal(self) -> bool:
        """Internal only if replies also stay internal."""
        return self.claims_internal and self.reply_elsewhere is None

    @property
    def impersonation_risk(self) -> bool:
        """Looks internal or known, but replies would go to a foreign domain."""
        return self.reply_elsewhere is not None and (self.claims_internal or self.sender is not None)


# --- IBANs, payments and deadlines --------------------------------------------------------------------------


def iban_is_valid(iban: str) -> bool:
    """Checks the length and the ISO 13616 check digits (mod 97) of an IBAN."""
    value = normalize_iban(iban)
    if not IBAN_MIN_LENGTH <= len(value) <= IBAN_MAX_LENGTH:
        return False
    rearranged = value[_IBAN_HEADER_CHARS:] + value[:_IBAN_HEADER_CHARS]
    digits = "".join(str(int(ch, _IBAN_DIGIT_BASE)) for ch in rearranged)
    return int(digits) % _IBAN_MODULUS == 1


def find_ibans(text: str) -> tuple[str, ...]:
    """Returns the valid IBANs in ``text``, normalized and without duplicates, in order of appearance."""
    found = (normalize_iban(m.group(0)) for m in _IBAN.finditer(text))
    return tuple(dict.fromkeys(i for i in found if iban_is_valid(i)))


def find_deadlines(text: str, received: datetime | None) -> tuple[Deadline, ...]:
    """Returns the deadlines in ``text``, earliest first.

    Periods ("vom 16.11. bis 29.11.") and payment terms ("zahlbar bis …") are no deadlines. A deadline
    of one month without a date counts only if no dated deadline was found.

    Args:
        text: The body and the attachment texts.
        received: When the mail arrived; dates without year are read relative to it (today if unknown).

    Returns:
        One deadline per due date.
    """
    reference = (received or datetime.now()).date()
    deadlines = _dated_deadlines(text, reference)
    if not deadlines and _MONTH_DEADLINE.search(text):
        deadlines.append(_month_deadline(text, reference))
    return tuple(sorted(deadlines, key=lambda d: d.due))


def find_payment(text: str, received: datetime | None) -> PaymentDetails | None:
    """Returns amount, IBAN, due date and invoice number, or ``None`` without amount, invoice and IBAN.

    Args:
        text: Subject, body and attachment texts.
        received: When the mail arrived; a due date without year is read relative to it (today if unknown).

    Returns:
        The payment data; nothing is computed, the amount stays text.
    """
    amount = _AMOUNT.search(text)
    invoice = _INVOICE_NUMBER.search(text)
    ibans = find_ibans(text)
    due = _due_date(text, (received or datetime.now()).date())
    if not (amount or invoice or ibans):
        return None
    return PaymentDetails(
        amount=f"{amount.group(1)} EUR" if amount else None,
        iban=ibans[0] if ibans else None,
        due=due,
        invoice_number=invoice.group(1) if invoice else None,
    )


def _dated_deadlines(text: str, reference: date) -> list[Deadline]:
    """Returns the deadlines with a date, the first mention of every due date only."""
    result: dict[date, Deadline] = {}
    for match in _DEADLINE_DATE.finditer(text):
        if _not_a_deadline(text, match.start()):
            continue
        due = _to_date(match.group(1), match.group(2), match.group(3), reference)
        if due is not None and due not in result:
            result[due] = Deadline(due, _line_around(text, match.start(), match.end()))
    return list(result.values())


def _month_deadline(text: str, reference: date) -> Deadline:
    """Returns the one-month deadline counted from ``reference``; the exact date has to be checked by hand."""
    kind = "Widerspruchsfrist" if "widerspruch" in text.lower() else "Frist"
    return Deadline(_add_month(reference), f"{kind}: ein Monat ab Zugang – genaues Datum prüfen")


def _due_date(text: str, reference: date) -> date | None:
    """Returns the first due date of a payment in ``text``, or ``None``."""
    match = _DUE_DATE.search(text)
    return _to_date(match.group(1), match.group(2), match.group(3), reference) if match else None


def _not_a_deadline(text: str, start: int) -> bool:
    """Checks whether a "bis …" ends a period ("vom 16.11. bis 29.11.") or payment term ("zahlbar bis …")."""
    before = text[max(0, start - _LOOK_BEHIND_CHARS) : start]
    return bool(_PERIOD_BEFORE.search(before) or _PAYMENT_TERM_BEFORE.search(before))


def _to_date(day: str, month: str, year: str | None, reference: date) -> date | None:
    """Builds the date of a match, or ``None`` for an impossible date like 31.02.

    A date without year is read in the year of ``reference``; one that lies well before it means the
    next year.
    """
    try:
        if year:
            return date(_full_year(year), int(month), int(day))
        candidate = date(reference.year, int(month), int(day))
    except ValueError:
        return None
    # "bis zum 05.01." written in December means next year
    if candidate >= reference - timedelta(days=PAST_DATE_TOLERANCE_DAYS):
        return candidate
    try:
        return candidate.replace(year=reference.year + 1)
    except ValueError:  # 29.02. of a leap year does not exist in the following year
        return None


def _full_year(year: str) -> int:
    """Returns the four-digit year of a written year ("27" -> 2027)."""
    return int(year) + (_CENTURY if len(year) == _SHORT_YEAR_DIGITS else 0)


def _add_month(value: date) -> date:
    """Returns the same day one month later, or the last day of that month if it is shorter."""
    year, month = (value.year + 1, 1) if value.month == DECEMBER else (value.year, value.month + 1)
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(value.day, last_day))


def _line_around(text: str, start: int, end: int, limit: int = DEADLINE_CONTEXT_CHARS) -> str:
    """Returns the line that contains ``text[start:end]``, with whitespace collapsed and shortened."""
    right = text.find("\n", end)
    line = " ".join(text[text.rfind("\n", 0, start) + 1 : right if right != -1 else len(text)].split())
    return line if len(line) <= limit else line[: limit - len(_ELLIPSIS)].rstrip() + _ELLIPSIS


# --- the signals of one message -----------------------------------------------------------------------------


def attachment_text(message: MailMessage) -> str:
    """Returns the texts read from the attachments, one after the other."""
    return "\n".join(a.text for a in message.attachments if a.text)


def detect(message: MailMessage, senders: SenderDirectory) -> MailSignals:
    """Collects the signals of ``message``.

    Payment data, bank details and deadlines also count when they only appear in an attachment.

    Args:
        message: The parsed message; its content is data, never instructions.
        senders: The known senders and the own domains.

    Returns:
        The signals of the message.
    """
    mail_text = f"{message.subject}\n{message.body}"
    attachments = attachment_text(message)
    text = f"{mail_text}\n{attachments}"
    lowered = text.lower()
    check = _check_sender(message, senders)
    ibans = find_ibans(text)
    forwarded = _is_forwarded(message)
    return MailSignals(
        reply_elsewhere=check.reply_elsewhere,
        impersonation_risk=check.impersonation_risk,
        sender=check.sender,
        internal=check.internal,
        forwarded=forwarded,
        own_text=_own_text(message.body) if forwarded else message.body.strip(),
        ibans=ibans,
        # an amount alone (e.g. the sum in dispute in a lawyer's letter) is no payment request
        payment_request=bool(_PAYMENT_REQUEST.search(text)),
        pressure=tuple(word for word in PRESSURE_WORDS if word in lowered),
        bank_change=bool(_BANK_CHANGE.search(text)),
        iban_mismatch=_iban_mismatch(ibans, check.sender),
        login_links=bool(_LOGIN_URL.search(mail_text) or _LOGIN_PHRASE.search(mail_text)),
        deadlines=find_deadlines(f"{message.body}\n{attachments}", message.received_at),
        has_attachments=bool(message.attachments),
    )


def _check_sender(message: MailMessage, senders: SenderDirectory) -> _SenderCheck:
    """Looks up the sender and checks where replies would go."""
    # A contact form address was typed by the visitor: never known, never internal (anyone could type ours).
    if message.contact_form:
        return _SenderCheck(sender=None, claims_internal=False, reply_elsewhere=None)
    # invoices sent via Lexware & Co. are identified by their Reply-To, not the shared From address.
    sender = senders.lookup_message(message.sender_address, message.reply_to)
    return _SenderCheck(
        sender=sender,
        claims_internal=senders.is_internal(message.sender_address),
        reply_elsewhere=_reply_elsewhere(message, senders, sender),
    )


def _reply_elsewhere(
    message: MailMessage, senders: SenderDirectory, sender: KnownSender | None
) -> str | None:
    """Returns the domain of a Reply-To that belongs neither to the sender nor to the same known company."""
    reply = message.reply_to.strip().lower()
    address = message.sender_address.strip().lower()
    if not reply or "@" not in reply or reply == address:
        return None
    reply_domain = reply.rsplit("@", 1)[1]
    if reply_domain == address.rsplit("@", 1)[-1]:
        return None
    if senders.is_internal(address) and senders.is_internal(reply):
        return None
    if sender is not None and senders.lookup(reply) is sender:
        return None
    return reply_domain


def _is_forwarded(message: MailMessage) -> bool:
    """Checks the subject prefix and the separator line of a forward."""
    return bool(_FORWARD_SUBJECT.match(message.subject)) or any(
        FORWARD_MARKER.match(line.strip()) for line in message.body.split("\n")
    )


def _own_text(body: str) -> str:
    """Returns the own words above a forwarded part, without greeting, closing and signature."""
    parts = []
    for raw_line in body.split("\n"):
        line = raw_line.strip()
        if FORWARD_MARKER.match(line):
            break
        if line == "--" or CLOSING.match(line):
            break
        if line and not GREETING.match(line) and not line.startswith(">"):
            parts.append(line)
    return " ".join(parts)


def _iban_mismatch(ibans: tuple[str, ...], sender: KnownSender | None) -> tuple[str, ...]:
    """Returns the IBANs that differ from the deposited ones; empty if the sender has none deposited."""
    known_ibans = set(sender.ibans) if sender else set()
    return tuple(i for i in ibans if known_ibans and i not in known_ibans)


# --- warnings -----------------------------------------------------------------------------------------------


def security_warnings(signals: MailSignals) -> tuple[str, ...]:
    """Returns the warnings that never depend on a model. Known senders get none of the blanket ones."""
    warnings = [_impersonation_warning(signals), _bank_warning(signals)]
    if not signals.known:
        warnings += [_unknown_payment_warning(signals), _login_warning(signals)]
    return tuple(warning for warning in warnings if warning is not None)


def _impersonation_warning(signals: MailSignals) -> str | None:
    """Warns when a familiar-looking sender asks about money but replies would go elsewhere."""
    if not (
        signals.impersonation_risk
        and signals.payment_request
        and (signals.ibans or signals.bank_change or signals.pressure)
    ):
        return None
    return (
        f"Der Absender wirkt vertraut, Antworten gingen aber an „{signals.reply_elsewhere}“. Bei "
        "Zahlungs- oder Kontoanfragen ist das eine typische Betrugsmasche – über eine bekannte "
        "Telefonnummer prüfen."
    )


def _bank_warning(signals: MailSignals) -> str | None:
    """Warns about an IBAN that differs from the deposited one, otherwise about announced new bank details."""
    if signals.iban_mismatch and signals.sender is not None:
        return (
            f"Die Bankverbindung weicht von der hinterlegten von „{signals.sender.name}“ ab. Vor jeder "
            "Zahlung telefonisch über eine bekannte Nummer bestätigen lassen."
        )
    if signals.bank_change and signals.payment_request:
        return (
            "Die Mail meldet eine geänderte Bankverbindung – häufige Betrugsmasche. Nur nach telefonischer "
            "Rückfrage über eine bekannte Nummer ändern."
        )
    return None


def _unknown_payment_warning(signals: MailSignals) -> str | None:
    """Warns about an unknown sender demanding payment with pressure or an IBAN (not for forwards)."""
    if not (signals.payment_request and (signals.pressure or signals.ibans) and not signals.forwarded):
        return None
    pressure = f" mit Druck („{signals.pressure[0]}“)" if signals.pressure else ""
    return UNKNOWN_PAYMENT_WARNING + pressure + ". Nicht zahlen, bevor die Forderung geprüft ist."


def _login_warning(signals: MailSignals) -> str | None:
    """Warns about an unknown sender leading to a login page."""
    if not signals.login_links:
        return None
    return (
        "Die Mail eines unbekannten Absenders führt zu einer Anmeldeseite bzw. fordert eine "
        "Anmeldung. Keine Zugangsdaten eingeben."
    )
