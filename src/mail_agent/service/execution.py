"""One job as the scheduler or a button starts it: run it, keep the report, record it in the run log."""

import threading
from typing import Protocol

from ..security.redaction import get_logger
from ..sources import SourceError
from ..storage import MailRepository, RunResult
from .report import ImportReport, Progress

_log = get_logger("service")

KIND_FETCH = "abruf"
"""Fetch new mails, then read the sent folder."""
KIND_AI = "ki-nachholen"
"""Analyse all open messages again that still wait for the AI."""
KIND_REANALYZE = "neu-auswerten"
"""Analyse given messages again, e.g. after their sender was saved as known."""


class Agent(Protocol):
    """What a job needs from the agent (implemented by ``MailAgentService``)."""

    repository: MailRepository

    @property
    def handover_configured(self) -> bool:
        """Whether a shared hand-over folder is set up."""

    def may_fetch(self) -> bool:
        """Whether this PC may fetch now."""

    def import_new(self, progress: Progress, stop: threading.Event | None = None) -> ImportReport:
        """Fetches and stores the new messages."""

    def sync_sent(self, stop: threading.Event | None = None) -> int:
        """Marks mails answered in the sent folder and returns how many."""

    def reanalyze_pending(
        self, progress: Progress, stop: threading.Event | None = None, ids: list[int] | None = None
    ) -> ImportReport:
        """Analyses untouched open messages again."""

    def note_change(self) -> None:
        """Remembers that the data changed (for the hand-over)."""


class JobExecution:
    """Runs one job and records it in the run log, also when it fails."""

    def __init__(self, agent: Agent, progress: Progress, stop: threading.Event | None) -> None:
        """Prepares a job; ``progress`` hears about every step, ``stop`` ends it after the current portion."""
        self._agent = agent
        self._progress = progress
        self._stop = stop
        self._report = ImportReport()

    def run(self, kind: str, trigger: str, ids: list[int] | None = None) -> ImportReport:
        """Runs a fetch (``abruf``), the AI catch-up (``ki-nachholen``) or a re-analysis of ``ids``.

        Args:
            kind: ``abruf``, ``ki-nachholen`` or ``neu-auswerten``.
            trigger: Who started it, e.g. ``zeitplan`` or ``manuell``.
            ids: The messages for ``neu-auswerten``, e.g. after a sender was saved as known.

        Returns:
            The report of the job.

        Raises:
            SourceError: This PC may not fetch, or the mailbox could not be read.
        """
        if kind == KIND_FETCH and not self._agent.may_fetch():
            raise SourceError("handover_passive" if self._agent.handover_configured else "fetch_disabled")
        runs = self._agent.repository.runs
        run_id = runs.start(kind, trigger)
        error: str | None = None
        try:
            if kind == KIND_FETCH:
                self._report = self._fetch()
            else:
                self._report = self._reanalyze(ids if kind == KIND_REANALYZE else None)
            if self._report.imported or self._report.answered:
                self._agent.note_change()
            return self._report
        except SourceError as exc:
            error = exc.code
            raise
        except Exception as exc:
            error = f"intern:{type(exc).__name__}"  # the type only: messages may contain mail content
            raise
        finally:
            runs.finish(run_id, run_result(self._report, error))

    def _track(self, phase: str, report: ImportReport) -> None:
        """Keeps the newest report (also for the run log of a failed job) and passes the progress on."""
        self._report = report
        self._progress(phase, report)

    def _fetch(self) -> ImportReport:
        """Fetches new mails, then reads the sent folder; a reply is never read before the mail it answers."""
        report = self._agent.import_new(self._track, self._stop)
        if not report.stopped:
            self._track("gesendet", report)
            report.answered = self._sync_sent()
            self._track("gesendet", report)
        return report

    def _sync_sent(self) -> int:
        """Reads the sent folder; a failure only costs the answered marks until the next fetch."""
        try:
            return self._agent.sync_sent(self._stop)
        except SourceError as exc:
            _log.warning("Gesendet-Ordner nicht lesbar (%s) – nächster Versuch beim nächsten Abruf", exc.code)
            return 0

    def _reanalyze(self, ids: list[int] | None) -> ImportReport:
        """Analyses the given messages again, or all that wait for the AI."""
        return self._agent.reanalyze_pending(self._track, self._stop, ids)


def run_result(report: ImportReport, error: str | None) -> RunResult:
    """Returns the part of a report that the run log keeps."""
    return RunResult(
        found=report.found,
        imported=report.imported,
        duplicates=report.duplicates,
        failed=tuple(report.failed),
        ai_pending=report.ai_pending,
        error=error,
    )
