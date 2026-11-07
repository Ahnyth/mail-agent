"""Combine a primary analyzer (AI) with a rule-based fallback."""

import threading
import time
from collections.abc import Callable
from dataclasses import replace

from ..models import Analysis, MailMessage
from ..security.redaction import get_logger
from . import AnalysisUnavailableError, Analyzer

_log = get_logger("analysis.fallback")

PAUSE_SECONDS = 30 * 60
"""After a lasting failure (subscription limit, missing login) the AI is paused this long."""
FAILURES_BEFORE_PAUSE = 3
"""Temporary failures in a row (timeouts, service busy) that also pause the AI, for a shorter time."""
SHORT_PAUSE_SECONDS = 10 * 60
"""The shorter pause after ``FAILURES_BEFORE_PAUSE`` temporary failures in a row."""

_REASONS = {
    "ai_credentials_missing": "nicht bei Claude angemeldet (claude auth login --claudeai)",
    "ai_not_authorized": "Zugang zu Claude ungültig oder ohne Berechtigung",
    "ai_no_credit": "kein Guthaben im Anthropic-Konto",
    "ai_cli_missing": "Claude Code (claude) nicht gefunden",
    "ai_cli_blocked": "Claude Code konnte nicht gestartet werden (von Windows blockiert?)",
    "ai_usage_limit": "Nutzungslimit des Claude-Abos erreicht",
    "ai_timeout": "KI hat nicht rechtzeitig geantwortet",
    "ai_unreachable": "KI-Dienst nicht erreichbar",
    "ai_rate_limited": "KI-Dienst ausgelastet",
    "ai_refused": "KI hat die Bearbeitung abgelehnt",
    "ai_output_invalid": "KI-Antwort auch beim zweiten Versuch ungültig",
    "ai_output_truncated": "KI-Antwort unvollständig",
    "ai_model_not_found": "KI-Modell nicht gefunden",
    "ai_cli_error": "Fehler beim Aufruf von Claude Code",
}
"""German reasons shown to the office per error code; an unknown code is shown as it is."""


class FallbackAnalyzer:
    """Uses the primary analyzer (AI) and falls back to the rules when it fails.

    A fallback result says so in its findings and is marked ``ai_pending``, so that it can be analysed
    again later.

    A lasting failure (limit reached, not logged in) pauses the primary analyzer for ``pause_seconds``, so a
    long import does not wait for the same error on every message; so do ``FAILURES_BEFORE_PAUSE`` temporary
    failures in a row (a hanging CLI would otherwise cost five minutes per message). After the pause the AI
    is tried again – the agent runs for days, the limit resets in between. Safe to use from several worker
    threads.
    """

    def __init__(
        self,
        primary: Analyzer,
        fallback: Analyzer,
        *,
        pause_seconds: float = PAUSE_SECONDS,
        short_pause_seconds: float = SHORT_PAUSE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Combines both analyzers.

        Args:
            primary: The AI.
            fallback: The rules; they never fail.
            pause_seconds: Pause after a lasting failure.
            short_pause_seconds: Pause after ``FAILURES_BEFORE_PAUSE`` temporary failures in a row.
            clock: Monotonic seconds; tests pass a fake clock.
        """
        self._primary = primary
        self._fallback = fallback
        self._pause_seconds = pause_seconds
        self._short_pause_seconds = short_pause_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._paused_until: float | None = None
        self._pause_reason: str | None = None
        self._failures_in_a_row = 0

    @property
    def pause_reason(self) -> str | None:
        """Why the AI is paused right now, or ``None``; an expired pause ends here."""
        with self._lock:
            if self._paused_until is not None and self._clock() >= self._paused_until:
                self._paused_until = self._pause_reason = None
            return self._pause_reason

    def analyze(self, message: MailMessage) -> Analysis:
        """Analyses ``message`` with the AI, or with the rules while the AI is paused or failing.

        Args:
            message: The mail to analyse.

        Returns:
            The AI's analysis, or the rules' analysis with a notice and marked ``ai_pending``.
        """
        paused = self.pause_reason
        if paused is not None:
            return self._with_notice(self._fallback.analyze(message), paused)
        try:
            analysis = self._primary.analyze(message)
        except AnalysisUnavailableError as exc:
            _log.warning("KI-Analyse nicht verfügbar (%s) – Regeln verwendet", exc.code)
            reason = self._record_failure(exc)
            return self._with_notice(self._fallback.analyze(message), reason)
        with self._lock:
            self._failures_in_a_row = 0
        return analysis

    def _record_failure(self, exc: AnalysisUnavailableError) -> str:
        """Counts a failure, pauses the AI when needed and returns the German reason."""
        reason = _REASONS.get(exc.code, exc.code)
        with self._lock:
            self._failures_in_a_row += 1
            if exc.permanent:
                self._pause(self._pause_seconds, reason)
            elif self._failures_in_a_row >= FAILURES_BEFORE_PAUSE:
                self._pause(self._short_pause_seconds, f"{reason}, mehrmals hintereinander")
        return reason

    def _pause(self, seconds: float, reason: str) -> None:
        """Pauses the AI for ``seconds``; call with the lock held."""
        self._paused_until = self._clock() + seconds
        self._pause_reason = reason
        self._failures_in_a_row = 0

    @staticmethod
    def _with_notice(analysis: Analysis, reason: str) -> Analysis:
        """Returns the rules' ``analysis`` with a notice why the AI was not used, marked for a later retry."""
        notice = f"KI-Analyse nicht verfügbar ({reason}) – Einordnung über Regeln, später nachholen."
        return replace(analysis, findings=(*analysis.findings, notice), ai_pending=True)
