"""The fetch: reading new mails in portions, analysing them and storing them; the mailbox is never changed."""

import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from ..filing import DocumentStore
from ..models import Analysis, MailMessage
from ..parsing import MailParseError, parse_message
from ..security.redaction import get_logger
from ..sources import FetchPlan, MailSource, RawMail
from ..storage import FetchState, MailRepository
from .batching import batches, first_per_sender, resume_cursor, stop_requested
from .preparation import MessagePreparer
from .report import ImportReport, Progress

_log = get_logger("service")

Pending = list[tuple[RawMail, MailMessage]]
"""Messages of one portion that still have to be analysed and stored."""


class MailImporter:
    """Reads every message the source received since the last fetch.

    Works in portions: read, analyse (``parallel`` at a time), store, then save the position. If the source
    fails, everything stored up to then is kept and the next fetch continues from there.
    """

    def __init__(
        self,
        repository: MailRepository,
        source: MailSource,
        documents: DocumentStore,
        preparer: MessagePreparer,
    ) -> None:
        """Keeps the parts of one fetch."""
        self._repository = repository
        self._source = source
        self._documents = documents
        self._preparer = preparer

    def run(
        self, start: date | None, progress: Progress, stop: threading.Event | None, *, parallel: int
    ) -> ImportReport:
        """Fetches, analyses and stores the new messages.

        Args:
            start: Mails before this day are never fetched.
            progress: Called after every step.
            stop: Ends the run after the current portion when set.
            parallel: How many messages are analysed at the same time.

        Returns:
            The report of the run.

        Raises:
            SourceError: The source could not be read.
        """
        report = ImportReport()
        cursor = resume_cursor(self._repository.positions.get(self._source.identity), start)
        progress("suchen", report)
        plan = self._source.plan(cursor, start)
        report.found = len(plan.items)
        last_uid = cursor.last_uid if cursor else 0
        self._save_position(plan, last_uid)  # also fixes the start date on the very first fetch
        progress("lesen", report)
        seen: set[str] = set()
        with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
            for batch in batches(plan.items):
                if stop_requested(stop):
                    report.stopped = True
                    break
                pending = self._read(plan, batch, seen, report)
                progress("auswerten", report)
                self._analyse_and_store(pool, pending, report, progress)
                last_uid = self._advance(plan, batch, last_uid)
        _log.info("Einlesen abgeschlossen: %s", report.text)
        return report

    def _read(self, plan: FetchPlan, batch: Sequence[str], seen: set[str], report: ImportReport) -> Pending:
        """Loads and parses one portion; unreadable and already known messages are only counted."""
        pending: Pending = []
        for raw in self._source.load(plan, batch):
            try:
                message = parse_message(raw.data)
            except MailParseError as exc:
                _log.warning("Nachricht %s nicht lesbar: %s", raw.origin, exc.code)
                report.failed.append(f"{raw.origin}: {exc.code}")
                continue
            if message.key in seen or self._repository.messages.contains(message.key):
                report.duplicates += 1
                continue
            seen.add(message.key)
            pending.append((raw, message))
        return pending

    def _analyse_and_store(
        self, pool: ThreadPoolExecutor, pending: Pending, report: ImportReport, progress: Progress
    ) -> None:
        """Analyses one portion round by round (one mail per sender at a time) and stores every result."""
        for group in first_per_sender(pending):
            prepared = pool.map(lambda item: self._preparer.prepare(item[1]), group)
            for (raw, _), (message, analysis) in zip(group, prepared, strict=True):
                self._store(raw, message, analysis)
                report.imported += 1
                report.ai_pending += int(analysis.ai_pending)
                progress("auswerten", report)

    def _store(self, raw: RawMail, message: MailMessage, analysis: Analysis) -> None:
        """Files the attachments and stores the message with its analysis."""
        filed = self._documents.file(message, analysis)
        self._repository.messages.add(
            message, origin=raw.origin, raw=raw.data, analysis=analysis, documents=filed
        )

    def _advance(self, plan: FetchPlan, batch: Sequence[str], last_uid: int) -> int:
        """Saves the position after a finished portion and returns the new last message number."""
        if plan.validity is None:
            return last_uid
        last_uid = max(last_uid, *(int(item) for item in batch))
        self._save_position(plan, last_uid)
        return last_uid

    def _save_position(self, plan: FetchPlan, last_uid: int) -> None:
        """Stores how far the source has been read (only sources with message numbers have a position)."""
        if plan.validity is not None:
            self._repository.positions.save(
                FetchState(self._source.identity, plan.validity, last_uid, plan.start)
            )
