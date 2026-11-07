"""Transparent keyword rules: works offline, sends no data anywhere.

Used when no AI is configured or available, and as a fallback. Every decision lists the
keywords that triggered it, so the team can see why a message landed where it did.

Each field of the analysis has its own small decision: :func:`decide_category`,
:func:`decide_urgency` and :func:`decide_reply_needed`; the responsibility follows from the category
and :class:`ReplyWriter` writes the draft from fixed texts. The mail is only ever read as data.
"""

import re
from dataclasses import dataclass, replace
from datetime import date
from typing import Self

from ..applications import DEFAULT_TEMPLATE, checklist, confirmation, fill_template
from ..config import OrganizationSettings
from ..models import (
    Analysis,
    Category,
    DocumentClassification,
    MailMessage,
    PaymentDetails,
    Responsible,
    Urgency,
)
from ..parsing import reply_subject
from ..senders import SenderDirectory
from .signals import (
    ASK_FOR_REPLY,
    CLOSING,
    FORWARD_MARKER,
    GREETING,
    MailSignals,
    attachment_text,
    detect,
    find_payment,
    security_warnings,
)

ENGINE_NAME = "regeln"
"""Engine name stored with every analysis of the rules; the interface and the evaluation show it."""
MAX_SUMMARY = 280
"""Longest summary in characters; a longer text is shortened and ends with " …"."""
_ELLIPSIS = " …"
"""Marks a shortened summary."""

# --- categories ---------------------------------------------------------------------------------------------

CATEGORY_KEYWORDS: dict[Category, tuple[str, ...]] = {
    Category.CARE_REQUEST: (
        "pflegeanfrage", "pflege für", "versorgung", "pflegedienst gesucht", "beratungsbesuch",
        "erstgespräch", "aufnahme", "entlassung", "pflegegrad beantragt", "suchen einen pflegedienst",
    ),
    Category.APPOINTMENT: (
        "termin", "verschieb", "absage", "absagen", "uhrzeit", "einsatz", "hausbesuch", "urlaub",
        "nicht zu hause", "kommen sie", "heute abend", "morgen früh", "früher kommen", "etwas früher",
    ),
    Category.INSURANCE: (
        "pflegekasse", "krankenkasse", "medizinischer dienst", "begutachtung", "kostenzusage",
        "kostenübernahme", "leistungsbescheid", "bewilligung", "aktenzeichen",
    ),
    Category.PHYSICIAN: (
        "verordnung", "praxis", "hausarzt", "dr. med", "medikation", "medikamentenplan", "rezept",
        "entlassbericht", "wundversorgung", "klinik", "arztbrief",
    ),
    Category.BILLING: (
        "rechnung", "abrechnung", "zahlung", "überweisung", "mahnung", "betrag", "zahlungserinnerung",
        "gutschrift", "eigenanteil", "inkasso", "zahlungsziel", "absetzung",
    ),
    Category.APPLICATION: (
        "bewerb", "lebenslauf", "stellenanzeige", "stellenangebot", "initiativ", "pflegefachkraft",
        "pflegehelfer", "pflegehilfskraft", "ausbildung", "vorstellungsgespräch", "zeugnis",
    ),
    Category.COMPLAINT: (
        "beschwerde", "unzufrieden", "verärgert", "reklamation", "nicht zufrieden", "enttäuscht",
        "unzumutbar", "zu spät", "unpünktlich",
    ),
    Category.DOCUMENTS: (
        "unterlagen", "anbei", "im anhang", "vollmacht", "nachreichen", "formular", "kopie",
    ),
    Category.AUTHORITY: (
        "landratsamt", "sozialamt", "gesundheitsamt", "finanzamt", "bezirksamt", "ordnungsamt",
        "heimaufsicht", "berufsgenossenschaft", "behörde", "hilfe zur pflege", "begehung",
        "betriebsprüfung", "steuernummer",
    ),
    Category.ADVERTISING: (
        "newsletter", "abmelden", "unsubscribe", "angebot", "rabatt", "webinar", "jetzt buchen",
        "gewinnspiel", "paket", "sendungsverfolgung", "fortbildung", "seminar", "messe",
    ),
}  # fmt: skip
"""Lower-case substrings per category (German compounds); on equal points the earlier category wins."""
SUBJECT_WEIGHT = 3
"""A keyword in the subject weighs three times as much as one in the body: the subject names the topic."""

SENDER_TYPE_CATEGORY: dict[str, Category] = {
    "kostentraeger": Category.INSURANCE,
    "abrechnungsdienst": Category.BILLING,
    "lieferant": Category.BILLING,
    "behoerde": Category.AUTHORITY,
    "arzt": Category.PHYSICIAN,
}
"""The category a known sender of this type usually writes in."""
SENDER_HINT_POINTS = 3
"""A known sender counts like one keyword in the subject for the category of its type."""

# "Unterlagen anbei" fits almost every mail; a specific category with any hit wins over it.
GENERIC_CATEGORIES = frozenset({Category.DOCUMENTS})
"""Categories that only win when no specific category has a keyword."""

LEGAL_NOT_KNOWN = (
    "Schreiben einer Kanzlei, die nicht als bekannter Anwalt hinterlegt ist – deshalb nicht unter "
    "„Recht / Anwalt“. Ist es Ihre Kanzlei: „Absender als bekannt hinterlegen“ mit Typ Anwalt."
)
"""Finding when a letter looks legal but the law firm is not in the list of known senders."""

# The confidence of the rules is compared with ``rules_threshold`` (default 0.8): below it the AI is
# asked when it is available. The values below are chosen relative to that line.
SUSPICIOUS_CONFIDENCE = 0.85
"""A fixed security warning is a strong sign; above the threshold, so confidence alone does not ask the AI."""
NEWSLETTER_CONFIDENCE = 0.95
"""A newsletter sender from the list of known senders is as certain as the rules get."""
LAWYER_CONFIDENCE = 0.9
"""A law firm from the list of known senders; above the threshold."""
INTERNAL_CONFIDENCE = 0.75
"""A colleague may write about anything; just below the threshold, so the AI looks at it."""
NO_KEYWORD_CONFIDENCE = 0.2
"""No keyword at all: the rules are guessing."""
CONFIDENCE_FLOOR = 0.3
"""Lowest confidence of a keyword decision."""
CONFIDENCE_CEILING = 0.95
"""Highest confidence of a keyword decision; keywords alone are never fully certain."""
STRENGTH_SHARE = 0.4
"""Part of the confidence that comes from the points of the best category."""
LEAD_SHARE = 0.25
"""Part of the confidence that comes from the lead over the runner-up."""
FULL_STRENGTH_POINTS = 6
"""Keyword points at which a category counts as clearly present: two keywords in the subject."""

# --- responsibility, tasks and replies ----------------------------------------------------------------------

RESPONSIBLE: dict[Category, Responsible] = {
    Category.CARE_REQUEST: Responsible.CARE_MANAGEMENT,
    Category.APPOINTMENT: Responsible.CARE_MANAGEMENT,
    Category.INSURANCE: Responsible.CARE_MANAGEMENT,
    Category.PHYSICIAN: Responsible.CARE_MANAGEMENT,
    Category.BILLING: Responsible.BILLING,
    Category.APPLICATION: Responsible.HUMAN_RESOURCES,
    Category.COMPLAINT: Responsible.CARE_MANAGEMENT,
    Category.DOCUMENTS: Responsible.CARE_MANAGEMENT,
    Category.AUTHORITY: Responsible.MANAGEMENT,
    Category.LEGAL: Responsible.MANAGEMENT,
    Category.INTERNAL: Responsible.CARE_MANAGEMENT,
    Category.SUSPICIOUS: Responsible.MANAGEMENT,
    Category.ADVERTISING: Responsible.ADMINISTRATION,
    Category.OTHER: Responsible.ADMINISTRATION,
}
"""Who handles a message of each category; the AI analysis uses the same table."""

ALWAYS_REPLY = frozenset(
    {
        Category.CARE_REQUEST,
        Category.APPOINTMENT,
        Category.COMPLAINT,
        Category.APPLICATION,
        Category.DOCUMENTS,
    }
)
"""People writing in these categories expect an answer; formal senders only when they ask for one."""
NEVER_REPLY = frozenset({Category.ADVERTISING, Category.SUSPICIOUS})
"""Categories that never get a draft: nobody expects an answer to advertising, and fraud must not get one."""

ATTACHMENT_HINTS = ("anbei", "im anhang", "angehängt", "beigefügt", "in der anlage")
"""Phrases that announce an attachment."""
MISSING_ATTACHMENT_TASK = "Angekündigter Anhang fehlt – beim Absender nachfragen"
"""Task added when an announced attachment is missing."""
MISSING_ATTACHMENT_REASON = "Anhang wird erwähnt, ist aber nicht vorhanden"
"""Reason shown when an announced attachment is missing."""
MISSING_ATTACHMENT_FINDING = "Ein Anhang wird erwähnt, ist aber nicht vorhanden."
"""Finding shown when an announced attachment is missing."""
UNKNOWN_BILLER_FINDING = "Der Absender ist nicht als Lieferant hinterlegt – Forderung vor Zahlung prüfen."
"""Finding for an invoice from a sender that is not in the list of known senders."""

HIGH_URGENCY_KEYWORDS = (
    "dringend", "sofort", "eilig", "notfall", "gestürzt", "sturz", "krankenhaus", "umgehend",
    "heute noch", "bis morgen", "letzte mahnung", "schmerzen", "krankmeldung", "melde mich krank",
)  # fmt: skip
"""Lower-case substrings that make a message urgent."""
DEADLINE_HIGH_DAYS = 7
"""A deadline within this many days of receipt makes a message urgent."""

INJECTION_PATTERN = (
    r"ignorier\w* (alle )?(vorherigen|bisherigen|obigen) anweisungen|ignore (all )?previous instructions"
    r"|systemprompt|system prompt",
    "Die Nachricht enthält Anweisungen an ein KI-System. Inhalt wurde nur als Daten behandelt – "
    "nichts davon ausführen.",
)
"""Pattern and warning for text that tries to give instructions to an AI (prompt injection)."""
CREDENTIALS_PATTERN = (
    r"passwort|zugangsdaten|kontodaten aller|tan-nummer|login bestätigen|konto verifizieren",
    "Die Nachricht fragt nach Zugangs- oder Kontodaten. Möglicher Betrugsversuch – keine Daten "
    "herausgeben, ggf. telefonisch beim bekannten Absender nachfragen.",
)
"""Pattern and warning for a request for login or account data.

Only for unknown senders: a known supplier may well mention its portal password reset.
"""

DOCUMENT_TYPES: tuple[tuple[str, str], ...] = (
    ("verordnung", "Ärztliche Verordnung"),
    ("kostenzusage", "Kostenzusage"),
    ("kostenuebernahme", "Kostenzusage"),
    ("kostenübernahme", "Kostenzusage"),
    ("bescheid", "Bescheid"),
    ("gutachten", "Gutachten"),
    ("medikation", "Medikationsplan"),
    ("entlass", "Entlassbericht"),
    ("vollmacht", "Vollmacht"),
    ("rechnung", "Rechnung"),
    ("lebenslauf", "Lebenslauf"),
    ("zeugnis", "Zeugnis"),
    ("anschreiben", "Bewerbungsanschreiben"),
    ("einladung", "Terminschreiben"),
)
"""(keyword, document type) pairs; the first keyword found decides."""
DOCUMENT_TYPE_TEXT_CHARS = 500
"""How much of an attachment text is searched for the document type: the title is at the beginning."""
UNKNOWN_DOCUMENT_TYPE = "Dokument"
"""Document type when neither the file name nor the text says more."""

TASKS: dict[Category, tuple[str, ...]] = {
    Category.CARE_REQUEST: (
        "Rückruf bzw. Erstgespräch vereinbaren",
        "Versorgungsbedarf, Pflegegrad und gewünschten Beginn klären",
        "Kapazität in der Tourenplanung prüfen",
    ),
    Category.APPOINTMENT: ("Tourenplanung prüfen und anpassen", "Änderung bestätigen"),
    Category.INSURANCE: ("Vorgang der Klientenakte zuordnen", "Fristen und Termine eintragen"),
    Category.PHYSICIAN: (
        "Verordnung prüfen und der Klientenakte zuordnen",
        "Genehmigung bei der Kasse prüfen",
    ),
    Category.BILLING: ("Rechnung sachlich prüfen", "an die Buchhaltung weitergeben"),
    Category.APPLICATION: ("Bewerbung an die Pflegedienstleitung weitergeben", "Eingang bestätigen"),
    Category.COMPLAINT: (
        "Sachverhalt mit dem Team klären",
        "Pflegedienstleitung informieren",
        "zeitnah antworten",
    ),
    Category.DOCUMENTS: ("Unterlagen prüfen und der Klientenakte zuordnen",),
    Category.AUTHORITY: ("Schreiben prüfen und Fristen eintragen", "an die Geschäftsführung weitergeben"),
    Category.LEGAL: (
        "Schreiben und Anlagen an die Geschäftsführung geben",
        "Fristen eintragen und angeforderte Unterlagen oder Stellungnahme vorbereiten",
    ),
    Category.INTERNAL: ("Anliegen intern klären",),
    Category.SUSPICIOUS: (
        "Keine Links öffnen, nichts zahlen, keine Daten herausgeben",
        "Im Zweifel den Absender über eine bekannte Telefonnummer fragen",
    ),
    Category.ADVERTISING: (),
    Category.OTHER: ("Zuständigkeit klären",),
}
"""The next steps for the team per category."""

REPLY_TEMPLATES: dict[Category, str] = {
    Category.CARE_REQUEST: (
        "vielen Dank für Ihre Anfrage und Ihr Vertrauen in Musterpflege.\n\n"
        "Gern beraten wir Sie zu den Möglichkeiten der Versorgung. [Bitte Rückrufzeitpunkt oder Termin für "
        "ein Erstgespräch vorschlagen.]\n\n"
        "Damit wir uns gut vorbereiten können, wären folgende Angaben hilfreich: gewünschter Beginn, "
        "Pflegegrad (falls vorhanden) und Ihre telefonische Erreichbarkeit."
    ),
    Category.APPOINTMENT: (
        "vielen Dank für Ihre Nachricht.\n\n"
        "[Bitte Änderung bestätigen bzw. neuen Termin mit Datum und Uhrzeit nennen.]\n\n"
        "Falls der Termin nicht passt, melden Sie sich gern bei uns."
    ),
    Category.INSURANCE: (
        "vielen Dank für Ihr Schreiben.\n\n"
        "[Bitte Eingang bestätigen und ggf. offene Punkte, Unterlagen oder Termine ergänzen.]"
    ),
    Category.PHYSICIAN: (
        "vielen Dank für die Übersendung.\n\n[Bitte Eingang bestätigen und ggf. Rückfragen ergänzen.]"
    ),
    Category.BILLING: (
        "vielen Dank für Ihre Nachricht.\n\n"
        "[Bitte Rechnungsnummer, Betrag und Sachstand prüfen und ergänzen.]\n\n"
        "Wir melden uns, sobald die Prüfung abgeschlossen ist."
    ),
    Category.APPLICATION: (
        "vielen Dank für Ihre Bewerbung und Ihr Interesse an Musterpflege.\n\n"
        "Wir haben Ihre Unterlagen erhalten und melden uns nach Durchsicht zeitnah bei Ihnen. "
        "[Optional: nächsten Schritt oder Gesprächstermin ergänzen.]"
    ),
    Category.COMPLAINT: (
        "vielen Dank, dass Sie uns Ihr Anliegen mitgeteilt haben. "
        "Es tut uns leid, dass Sie unzufrieden sind.\n\n"
        "[Bitte Sachverhalt klären und konkrete nächste Schritte ergänzen.]\n\n"
        "Wir nehmen Ihre Rückmeldung ernst und kommen zeitnah auf Sie zu."
    ),
    Category.DOCUMENTS: (
        "vielen Dank für die Unterlagen. Wir haben sie erhalten und ordnen sie zu.\n\n"
        "[Bitte ergänzen, falls noch Unterlagen fehlen.]"
    ),
    Category.AUTHORITY: (
        "vielen Dank für Ihr Schreiben.\n\n"
        "[Bitte Eingang bestätigen bzw. Rückfrage oder Unterlagen ergänzen.]"
    ),
    Category.LEGAL: (
        "vielen Dank für Ihr Schreiben und die Unterlagen.\n\n"
        "[Bitte Eingang bestätigen und Termin für Stellungnahme oder Unterlagen nennen.]"
    ),
    Category.INTERNAL: "[Bitte Antwort ergänzen.]",
    Category.SUSPICIOUS: "",
    Category.ADVERTISING: "",
    Category.OTHER: "vielen Dank für Ihre Nachricht.\n\n[Bitte Antwort ergänzen.]",
}
"""Reply texts after the salutation; ``[…]`` marks what the team fills in before sending."""
TEMPLATE_ORGANIZATION = "Musterpflege"
"""The name in :data:`REPLY_TEMPLATES` that is replaced by the configured organization name."""

_ATTRIBUTION = re.compile(r"^(am|on)\b.*\b(schrieb|wrote)\b.*:$", re.IGNORECASE)  # "Am … schrieb X:"
"""The line above a quote ("Am 1.11. schrieb X:")."""
_NOISE = re.compile(r"^(\[(image|bild|grafik):[^\]]*\]|<?https?://\S+>?)$", re.IGNORECASE)
"""Lines that only hold an image placeholder or a bare link."""
_QUOTE_PREFIX = re.compile(r"^(\s*>)+ ?")
"""The ``>`` marks in front of a quoted line."""
_AUTOMATED_SENDER = re.compile(r"no-?reply|do-?not-?reply|mailer-daemon", re.IGNORECASE)
"""Local parts of addresses that nobody reads."""


# --- decisions ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleContext:
    """One message prepared once for all decisions: signals, fixed warnings and lower-case texts."""

    message: MailMessage
    signals: MailSignals
    warnings: tuple[str, ...]
    """The security warnings of :func:`fixed_warnings`."""
    subject: str
    """The subject in lower case, like the keywords."""
    body: str
    """The body in lower case, like the keywords."""

    @classmethod
    def of(cls, message: MailMessage, signals: MailSignals) -> Self:
        """Prepares ``message`` with its ``signals``."""
        return cls(
            message=message,
            signals=signals,
            warnings=fixed_warnings(message, signals),
            subject=message.subject.lower(),
            body=message.body.lower(),
        )


@dataclass(frozen=True)
class Decision[T]:
    """One decision of the rules and the reason shown to the team (``None``: nothing worth showing)."""

    value: T
    reason: str | None = None


@dataclass(frozen=True)
class Categorization:
    """The category with the confidence of the rules and the reason shown to the team."""

    category: Category
    confidence: float
    reason: str


@dataclass(frozen=True)
class KeywordScore:
    """Points and matched keywords of one keyword list."""

    points: int = 0
    hits: tuple[str, ...] = ()

    @classmethod
    def count(cls, keywords: tuple[str, ...], subject: str, body: str) -> Self:
        """Counts the ``keywords`` in ``subject`` and ``body`` (both lower case); the subject weighs more."""
        points, hits = 0, []
        for keyword in keywords:
            in_subject, in_body = keyword in subject, keyword in body
            if in_subject or in_body:
                hits.append(keyword)
                points += SUBJECT_WEIGHT * in_subject + in_body
        return cls(points, tuple(hits))

    def plus(self, points: int, hit: str) -> Self:
        """Returns the score with extra ``points`` for a reason that is no keyword (a known sender)."""
        return replace(self, points=self.points + points, hits=(*self.hits, hit))


@dataclass(frozen=True)
class RuleDecisions:
    """All decisions of the rules for one message."""

    categorization: Categorization
    attachment_missing: bool
    urgency: Decision[Urgency]
    reply_needed: Decision[bool]

    @classmethod
    def of(cls, context: RuleContext) -> Self:
        """Makes every decision for the message of ``context``."""
        categorization = decide_category(context)
        category = categorization.category
        return cls(
            categorization=categorization,
            attachment_missing=attachment_missing(context),
            urgency=decide_urgency(context, category),
            reply_needed=decide_reply_needed(context, category),
        )

    @property
    def category(self) -> Category:
        """The chosen category."""
        return self.categorization.category

    @property
    def reasons(self) -> tuple[str, ...]:
        """The reasons in the order of the decisions: category, attachment, urgency, reply."""
        missing = MISSING_ATTACHMENT_REASON if self.attachment_missing else None
        candidates = (self.categorization.reason, missing, self.urgency.reason, self.reply_needed.reason)
        return tuple(reason for reason in candidates if reason is not None)

    @property
    def tasks(self) -> tuple[str, ...]:
        """The tasks of the category; a missing attachment comes first."""
        tasks = TASKS[self.category]
        return (MISSING_ATTACHMENT_TASK, *tasks) if self.attachment_missing else tasks


def decide_category(context: RuleContext) -> Categorization:
    """Returns the category: fixed rules first (fraud, newsletter, law firm, colleague), then keywords."""
    fixed = _fixed_category(context)
    return fixed if fixed is not None else _keyword_category(context)


def _fixed_category(context: RuleContext) -> Categorization | None:
    """Returns the category that a warning or the kind of sender fixes, or ``None``."""
    signals, sender = context.signals, context.signals.sender
    if context.warnings:
        reason = "Merkmale eines Betrugs- oder Phishingversuchs"
        return Categorization(Category.SUSPICIOUS, SUSPICIOUS_CONFIDENCE, reason)
    if sender is not None and sender.type == "newsletter":
        reason = f"Bekannter Newsletter-Absender: {sender.name}"
        return Categorization(Category.ADVERTISING, NEWSLETTER_CONFIDENCE, reason)
    if is_known_lawyer(signals):
        reason = f"Bekannte Kanzlei: {sender.name if sender else ''}"
        return Categorization(Category.LEGAL, LAWYER_CONFIDENCE, reason)
    if signals.internal and not signals.forwarded:
        return Categorization(Category.INTERNAL, INTERNAL_CONFIDENCE, "Absender aus der eigenen Firma")
    return None


def _keyword_category(context: RuleContext) -> Categorization:
    """Returns the category with the most keyword points, or "Sonstiges" without any keyword."""
    scores = _keyword_scores(context)
    ranked = _ranked(scores)
    if not ranked:
        return Categorization(Category.OTHER, NO_KEYWORD_CONFIDENCE, "Keine eindeutigen Stichworte gefunden")
    category, best = ranked[0], scores[ranked[0]]
    second = max((scores[c].points for c in ranked[1:] if c not in GENERIC_CATEGORIES), default=0)
    reason = f"Stichworte für „{category.label}“: {', '.join(best.hits)}"
    return Categorization(category, _confidence(best.points, min(second, best.points)), reason)


def _keyword_scores(context: RuleContext) -> dict[Category, KeywordScore]:
    """Scores every category; attachment names count like the body, a known sender adds points."""
    attachment_names = " ".join(a.filename.lower() for a in context.message.attachments)
    text = f"{context.body} {attachment_names}"
    scores = {
        candidate: KeywordScore.count(keywords, context.subject, text)
        for candidate, keywords in CATEGORY_KEYWORDS.items()
    }
    sender = context.signals.sender
    hinted = SENDER_TYPE_CATEGORY.get(sender.type) if sender else None
    if sender is not None and hinted is not None:
        scores[hinted] = scores[hinted].plus(SENDER_HINT_POINTS, f"bekannter Absender {sender.name}")
    return scores


def _ranked(scores: dict[Category, KeywordScore]) -> list[Category]:
    """Returns the categories with points, best first; a generic category ranks behind every specific one."""
    # Stable sort: on equal rank the earlier category in CATEGORY_KEYWORDS wins.
    return sorted(
        (c for c, score in scores.items() if score.points > 0),
        key=lambda c: (c not in GENERIC_CATEGORIES, scores[c].points),
        reverse=True,
    )


def _confidence(top: int, second: int) -> float:
    """Returns 0.3–0.95 from the strength of the best category and its lead over the runner-up."""
    strength = min(top, FULL_STRENGTH_POINTS) / FULL_STRENGTH_POINTS
    lead = (top - second) / top
    return round(min(CONFIDENCE_CEILING, CONFIDENCE_FLOOR + STRENGTH_SHARE * strength + LEAD_SHARE * lead), 2)


def attachment_missing(context: RuleContext) -> bool:
    """Checks whether the text announces an attachment that the message does not have."""
    return not context.message.attachments and any(hint in context.body for hint in ATTACHMENT_HINTS)


def decide_urgency(context: RuleContext, category: Category) -> Decision[Urgency]:
    """Returns the urgency: fraud by the money at risk, advertising low, others by keywords and deadlines."""
    if category is Category.SUSPICIOUS:
        return Decision(Urgency.HIGH if _money_at_risk(context.signals) else Urgency.LOW)
    if category is Category.ADVERTISING:
        return Decision(Urgency.LOW)
    urgent = KeywordScore.count(HIGH_URGENCY_KEYWORDS, context.subject, context.body)
    if urgent.hits:
        return Decision(Urgency.HIGH, f"Dringlichkeitsmerkmale: {', '.join(urgent.hits)}")
    due = _deadline_soon(context)
    if due is not None:
        return Decision(Urgency.HIGH, f"Frist in den nächsten Tagen: {due:%d.%m.%Y}")
    return Decision(Urgency.NORMAL)


def _money_at_risk(signals: MailSignals) -> bool:
    """Checks for changed bank details or a payment demanded under pressure."""
    return (
        signals.bank_change
        or bool(signals.iban_mismatch)
        or (signals.payment_request and bool(signals.pressure))
    )


def _deadline_soon(context: RuleContext) -> date | None:
    """Returns the first deadline within :data:`DEADLINE_HIGH_DAYS` of receipt, or ``None``."""
    received_at = context.message.received_at
    if received_at is None:
        return None
    received = received_at.date()
    soon = (d.due for d in context.signals.deadlines if (d.due - received).days <= DEADLINE_HIGH_DAYS)
    return next(soon, None)


def decide_reply_needed(context: RuleContext, category: Category) -> Decision[bool]:
    """Decides whether a draft is written; the sender must expect an answer and be able to receive it."""
    if category in NEVER_REPLY or context.warnings:
        return Decision(False)
    blocker = _no_reply_reason(context)
    if blocker is not None:
        return Decision(False, blocker)
    if category in ALWAYS_REPLY or ASK_FOR_REPLY.search(context.signals.own_text):
        return Decision(True)
    return Decision(False, "Keine Frage oder Bitte um Rückmeldung erkannt – kein Entwurf")


def _no_reply_reason(context: RuleContext) -> str | None:
    """Returns why the sender gets no draft whatever the category, or ``None``."""
    if is_automated_sender(context.message.sender_address):
        return "Automatische Absenderadresse – eine Antwort kommt nicht an"
    if context.signals.pure_forward:
        return "Weiterleitung – keine Antwort an den Weiterleitenden nötig"
    sender = context.signals.sender
    if sender is not None and sender.type in {"lieferant", "newsletter"}:
        return "Rechnung oder Mitteilung eines Lieferanten – keine Antwort nötig"
    return None


# --- helpers shared with the AI analysis --------------------------------------------------------------------


def is_known_lawyer(signals: MailSignals) -> bool:
    """Checks whether the sender is a law firm from the list of known senders (and replies go back there)."""
    return signals.known and signals.sender is not None and signals.sender.type == "anwalt"


def enforce_legal(category: Category, signals: MailSignals) -> tuple[Category, str | None]:
    """Keeps „Recht / Anwalt“ for law firms from the list of known senders – and for them always.

    The exceptions are advertising and suspected fraud, which stay as they are.

    Args:
        category: The category chosen so far.
        signals: The signals of the message.

    Returns:
        The category and a finding for the team if the category was changed, otherwise ``None``.
    """
    if is_known_lawyer(signals):
        if category in {Category.SUSPICIOUS, Category.ADVERTISING}:
            return category, None
        return Category.LEGAL, None
    if category is Category.LEGAL:
        return Category.OTHER, LEGAL_NOT_KNOWN
    return category, None


def fixed_warnings(message: MailMessage, signals: MailSignals) -> tuple[str, ...]:
    """Returns the security warnings that do not depend on a model; the AI analysis uses them too."""
    text = f"{message.subject}\n{message.body}".lower()
    warnings = list(security_warnings(signals))
    if re.search(INJECTION_PATTERN[0], text):
        warnings.append(INJECTION_PATTERN[1])
    if not signals.known and re.search(CREDENTIALS_PATTERN[0], text):
        warnings.append(CREDENTIALS_PATTERN[1])
    return tuple(warnings)


def is_automated_sender(address: str) -> bool:
    """Checks whether ``address`` belongs to a machine (no-reply, mailer-daemon) that reads no answers."""
    return bool(_AUTOMATED_SENDER.search(address.partition("@")[0]))


def document_type(filename: str, text: str | None = None) -> str:
    """Returns the document type by file name; if that says nothing, by the beginning of the text."""
    for candidate in (filename.lower(), (text or "")[:DOCUMENT_TYPE_TEXT_CHARS].lower()):
        found = next((label for key, label in DOCUMENT_TYPES if key in candidate), None)
        if found:
            return found
    return UNKNOWN_DOCUMENT_TYPE


def read_aloud_text(message: MailMessage, category: Category) -> str:
    """Returns the text read aloud: category, sender and subject only (no summary with personal data)."""
    sender = message.sender_name or message.sender_address.rpartition("@")[2]
    return f"{category.label} von {sender}: {message.subject or 'ohne Betreff'}"


def salutation(sender_name: str) -> str:
    """Returns the salutation of a draft: personal with a name, formal without."""
    return f"Guten Tag {sender_name}," if sender_name else "Sehr geehrte Damen und Herren,"


def application_reply(message: MailMessage, template: str, organization: OrganizationSettings) -> str:
    """Returns the fixed confirmation of receipt with the documents still missing (no AI text)."""
    items = checklist(message.subject, message.body, [(a.filename, a.text) for a in message.attachments])
    return confirmation(
        template, salutation=salutation(message.sender_name), organization=organization.name, items=items
    )


# --- summary ------------------------------------------------------------------------------------------------


def summarize(body: str, subject: str) -> str:
    """Returns the summary: own text first; a reply that only quotes (a "Re:" passing a mail on) uses that."""
    lines = body.split("\n")
    own = [line for line in lines if not line.lstrip().startswith(">")]
    quoted = [_QUOTE_PREFIX.sub("", line) for line in lines if line.lstrip().startswith(">")]
    text = _summary_text(own) or _summary_text(quoted) or subject or "(kein Text)"
    return text if len(text) <= MAX_SUMMARY else text[: MAX_SUMMARY - len(_ELLIPSIS)].rstrip() + _ELLIPSIS


def _summary_text(lines: list[str]) -> str:
    """Joins the content lines up to the signature, skipping forward headers, greetings and noise."""
    parts: list[str] = []
    in_forward_header = False
    for raw_line in lines:
        line = raw_line.strip()
        if FORWARD_MARKER.match(line):
            in_forward_header = True
            continue
        if in_forward_header:  # "Von:/Date:/Subject:" block, possibly wrapped, ends at a blank line
            in_forward_header = bool(line)
            continue
        if _is_filler(line):
            continue
        if line == "--" or CLOSING.match(line):  # signature follows
            break
        parts.append(line)
        if len(" ".join(parts)) >= MAX_SUMMARY:
            break
    return " ".join(parts)


def _is_filler(line: str) -> bool:
    """Checks for a line without content: empty, a greeting, a quote attribution or a bare link or image."""
    return not line or bool(GREETING.match(line) or _ATTRIBUTION.match(line) or _NOISE.match(line))


# --- the analyzer -------------------------------------------------------------------------------------------


class ReplyWriter:
    """Writes the draft of the rules from fixed texts; nothing from the mail is copied into it.

    Applications get the fixed confirmation of receipt with the missing documents, care requests the own
    template if one is set; every other category uses its text from :data:`REPLY_TEMPLATES`.
    """

    def __init__(
        self, organization: OrganizationSettings, application_template: str, care_template: str
    ) -> None:
        """Keeps the organization and the two reply templates."""
        self._organization = organization
        self._application_template = application_template
        self._care_template = care_template

    def write(self, message: MailMessage, category: Category) -> str:
        """Returns the draft for ``message`` with salutation and signature."""
        # Urgency is shown in the interface only; internal notes never go into the reply text.
        return f"{self._body(message, category)}\n\n{self._organization.signature}"

    def _body(self, message: MailMessage, category: Category) -> str:
        """Returns the draft without signature; every text starts with the salutation."""
        if category is Category.APPLICATION:
            return application_reply(message, self._application_template, self._organization)
        if category is Category.CARE_REQUEST and self._care_template:
            return fill_template(
                self._care_template,
                salutation=salutation(message.sender_name),
                organization=self._organization.name,
            )
        template = REPLY_TEMPLATES[category].replace(TEMPLATE_ORGANIZATION, self._organization.name)
        return f"{salutation(message.sender_name)}\n\n{template}"


class RuleBasedAnalyzer:
    """Analyses a message with the keyword rules alone: offline, fast and explainable."""

    def __init__(
        self,
        organization: OrganizationSettings,
        senders: SenderDirectory | None = None,
        application_template: str = DEFAULT_TEMPLATE,
        care_template: str = "",
    ) -> None:
        """Keeps the settings of the rules.

        Args:
            organization: Name and signature used in the drafts.
            senders: The known senders and own domains; ``None`` knows nobody.
            application_template: The fixed confirmation of receipt for applications.
            care_template: The reply template for care requests; empty uses the built-in text.
        """
        self._senders = senders or SenderDirectory()
        self._writer = ReplyWriter(organization, application_template, care_template)

    def signals(self, message: MailMessage) -> MailSignals:
        """Returns the deterministic signals of ``message``; the routing uses them to decide about the AI."""
        return detect(message, self._senders)

    def analyze(self, message: MailMessage) -> Analysis:
        """Analyses ``message`` with the rules.

        Args:
            message: The parsed message; its content is data, never instructions.

        Returns:
            The analysis with the reasons of every decision.
        """
        context = RuleContext.of(message, self.signals(message))
        decided = RuleDecisions.of(context)
        category, reply_needed = decided.category, decided.reply_needed.value
        return Analysis(
            category=category,
            urgency=decided.urgency.value,
            summary=summarize(message.body, message.subject),
            reasons=decided.reasons,
            tasks=decided.tasks,
            reply_needed=reply_needed,
            reply_subject=reply_subject(message.subject),
            reply_body=self._writer.write(message, category) if reply_needed else "",
            documents=_documents(message),
            warnings=context.warnings,
            engine=ENGINE_NAME,
            findings=_findings(context, decided),
            deadlines=context.signals.deadlines,
            payment=_payment(context),
            responsible=RESPONSIBLE[category],
            confidence=decided.categorization.confidence,
            read_aloud=read_aloud_text(message, category),
        )


def _documents(message: MailMessage) -> tuple[DocumentClassification, ...]:
    """Returns the document type of every attachment."""
    return tuple(
        DocumentClassification(a.filename, document_type(a.filename, a.text)) for a in message.attachments
    )


def _findings(context: RuleContext, decided: RuleDecisions) -> tuple[str, ...]:
    """Returns the notes for the team: a missing attachment, an invoice from an unknown sender."""
    findings: list[str] = []
    if decided.attachment_missing:
        findings.append(MISSING_ATTACHMENT_FINDING)
    if decided.category is Category.BILLING and not context.signals.known:
        findings.append(UNKNOWN_BILLER_FINDING)
    return tuple(findings)


def _payment(context: RuleContext) -> PaymentDetails | None:
    """Returns the payment data, but only when the mail asks for payment or names an IBAN."""
    message = context.message
    text = f"{message.subject}\n{message.body}\n{attachment_text(message)}"
    payment = find_payment(text, message.received_at)
    return payment if payment and (context.signals.payment_request or context.signals.ibans) else None
