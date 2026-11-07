"""Analysing stored messages again: one by hand, or all that still wait for the AI."""

import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor

from ..models import Analysis, MailMessage
from ..parsing import MailParseError, parse_message
from ..storage import MailRepository
from .batching import batches, stop_requested
from .preparation import MessagePreparer
from .report import ImportReport, Progress

Loaded = list[tuple[int, MailMessage]]
"""Stored messages of one portion with their ids, parsed again from the raw message."""


class Reanalyzer:
    """Runs stored messages through the current rules, senders and AI again."""

    def __init__(self, repository: MailRepository, preparer: MessagePreparer) -> None:
        """Keeps the parts of one re-analysis."""
        self._repository = repository
        self._preparer = preparer

    def one(self, message_id: int) -> Analysis | None:
        """Analyses one message again, e.g. after setting up the AI; a draft edited by hand is replaced.

        Returns:
            The new analysis, or ``None`` if the id is unknown.

        Raises:
            MailParseError: The stored raw message cannot be read any more.
        """
        raw = self._repository.messages.raw(message_id)
        if raw is None:
            return None
        message, analysis = self._preparer.prepare(parse_message(raw), exclude_id=message_id)
        self._repository.analyses.replace(message_id, analysis)
        self._refresh(message_id, message)
        return analysis

    def untouched(
        self, ids: list[int] | None, progress: Progress, stop: threading.Event | None, *, parallel: int
    ) -> ImportReport:
        """Analyses open messages again that nobody changed by hand.

        Args:
            ids: The messages to look at; ``None`` takes every message that still waits for the AI.
            progress: Called after every message.
            stop: Ends the run after the current portion when set.
            parallel: How many messages are analysed at the same time.

        Returns:
            The report; ``imported`` counts the re-analysed messages, ``duplicates`` those changed by hand
            in the meantime.
        """
        report = ImportReport()
        analyses = self._repository.analyses
        todo = analyses.pending_ai_ids() if ids is None else analyses.untouched_open_ids(ids)
        report.found = len(todo)
        progress("auswerten", report)
        with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
            for batch in batches(todo):
                if stop_requested(stop):
                    report.stopped = True
                    break
                loaded = self._load(batch, report)
                prepared = pool.map(lambda entry: self._preparer.prepare(entry[1], entry[0]), loaded)
                for (message_id, _), (message, analysis) in zip(loaded, prepared, strict=True):
                    self._store_if_untouched(message_id, message, analysis, report)
                    progress("auswerten", report)
        return report

    def _load(self, batch: Sequence[int], report: ImportReport) -> Loaded:
        """Parses the raw messages of one portion again; unreadable ones are counted as failed."""
        loaded: Loaded = []
        for message_id in batch:
            raw = self._repository.messages.raw(message_id)
            if raw is None:
                continue
            try:
                loaded.append((message_id, parse_message(raw)))
            except MailParseError as exc:
                report.failed.append(f"Nachricht {message_id}: {exc.code}")
        return loaded

    def _store_if_untouched(
        self, message_id: int, message: MailMessage, analysis: Analysis, report: ImportReport
    ) -> None:
        """Stores the new analysis unless the message was changed by hand while it was being analysed."""
        if self._repository.analyses.replace(message_id, analysis, only_if_untouched=True):
            self._refresh(message_id, message)
            report.imported += 1
            report.ai_pending += int(analysis.ai_pending)
        else:
            report.duplicates += 1  # changed by hand in the meantime: left alone

    def _refresh(self, message_id: int, message: MailMessage) -> None:
        """Keeps stored sender, text and attachment texts in line with the current parser."""
        self._repository.messages.refresh(message_id, message)
        texts = {a.filename: (a.text, a.text_note) for a in message.attachments}
        self._repository.documents.update_texts(message_id, texts)
