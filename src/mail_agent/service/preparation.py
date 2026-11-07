"""Preparing one message for storage: sender history, attachment texts and the analysis."""

from collections.abc import Callable
from dataclasses import replace

from ..analysis import Analyzer, timed_analyze
from ..analysis.rules import document_type
from ..attachments import TesseractOcr, read_attachments
from ..models import Analysis, Category, DocumentClassification, MailMessage, Urgency
from ..parsing import reply_subject
from ..security.redaction import get_logger

_log = get_logger("service")

HistoryLookup = Callable[[MailMessage, int | None], tuple[str, ...]]
"""Returns the history lines of the sender of a message, leaving out the message id given."""


class MessagePreparer:
    """Reads the attachments of a message and analyses it; never raises.

    One broken message must not stop an import of hundreds, so a crashing analysis gives a stand-in that
    still shows up in the inbox.
    """

    def __init__(
        self, analyzer: Analyzer, history: HistoryLookup, ocr: TesseractOcr | None, *, read_text: bool
    ) -> None:
        """Keeps the parts for :meth:`prepare`.

        Args:
            analyzer: Rules, Claude or both.
            history: Where the earlier classifications of a sender come from.
            ocr: Text recognition for scans, or ``None``.
            read_text: Whether attachment texts are read at all.
        """
        self._analyzer = analyzer
        self._history = history
        self._ocr = ocr
        self._read_text = read_text

    def prepare(self, message: MailMessage, exclude_id: int | None = None) -> tuple[MailMessage, Analysis]:
        """Returns the message with history and attachment texts, together with its analysis.

        Args:
            message: The parsed message.
            exclude_id: Its own id when it is analysed again, so it does not count as its own history.

        Returns:
            The completed message and the analysis (a stand-in if the analysis crashed).
        """
        message = replace(message, sender_history=self._history(message, exclude_id))
        message = read_attachments(message, self._ocr, enabled=self._read_text)
        try:
            return message, timed_analyze(self._analyzer, message)
        except Exception as exc:  # an analysis bug must never stop the whole run
            _log.warning(
                "Auswertung fehlgeschlagen (%s) – Nachricht ohne Auswertung übernommen", type(exc).__name__
            )
            return message, unanalysed(message)


def unanalysed(message: MailMessage) -> Analysis:
    """Returns the stand-in analysis for a message whose automatic analysis crashed.

    The message still shows up in the inbox, marked for the AI catch-up.
    """
    return Analysis(
        category=Category.OTHER,
        urgency=Urgency.NORMAL,
        summary=message.subject or "(ohne Betreff)",
        reasons=("Automatische Auswertung fehlgeschlagen",),
        tasks=("Nachricht selbst prüfen",),
        reply_needed=False,
        reply_subject=reply_subject(message.subject),
        reply_body="",
        documents=tuple(
            DocumentClassification(a.filename, document_type(a.filename, a.text)) for a in message.attachments
        ),
        warnings=(),
        engine="keine",
        findings=(
            "Automatische Auswertung fehlgeschlagen – bitte selbst prüfen und später neu analysieren.",
        ),
        ai_pending=True,
    )
