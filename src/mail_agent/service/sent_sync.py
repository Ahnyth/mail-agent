"""The sent folder: replies found there mark the answered mails as "beantwortet" and "erledigt".

The folder is only read (``EXAMINE``), from the fetch start date on, and only what arrived since the last
check: it has its own read position, just like the inbox.
"""

import threading
from collections.abc import Sequence
from datetime import UTC, date, datetime

from ..security.redaction import get_logger
from ..sent import SENT_FOLDER_CANDIDATES, StoredMail, answered_ids, read_sent
from ..sources import FetchPlan
from ..sources.imap import ImapSource
from ..storage import FetchState, MailRepository
from .batching import batches, resume_cursor, stop_requested

_log = get_logger("service")

SENT_MAILBOX_STATE = "sent_mailbox"
"""``app_state`` key: the sent folder found by trying the usual names (only stored once found)."""
SENT_CHECKED_STATE = "sent_checked"
"""``app_state`` key: when the sent folder was last looked for or read (shown in the inbox)."""


class SentFolderSync:
    """Finds the sent folder and matches the replies in it with the stored mails."""

    def __init__(self, repository: MailRepository, source: ImapSource, configured_mailbox: str) -> None:
        """Keeps the parts; ``configured_mailbox`` is empty unless the configuration names the folder."""
        self._repository = repository
        self._source = source
        self._configured = configured_mailbox

    def mailbox(self) -> str | None:
        """Returns the sent folder: configured, found earlier, or found now by trying the usual names."""
        if self._configured:
            return self._configured
        known = self._repository.state.get(SENT_MAILBOX_STATE)
        if known:
            return known
        found = self._source.find_mailbox(SENT_FOLDER_CANDIDATES)
        if found:
            self._repository.state.set(SENT_MAILBOX_STATE, found)
        return found

    def sync(self, start: date | None, stop: threading.Event | None) -> int:
        """Reads the new replies in the sent folder and marks the answered mails.

        Args:
            start: The fetch start date; older replies are not read.
            stop: Ends the run after the current portion when set.

        Returns:
            How many mails were marked as answered.
        """
        mailbox = self.mailbox()
        self._repository.state.set(SENT_CHECKED_STATE, datetime.now(UTC).isoformat())
        if mailbox is None:
            return 0
        identity = self._source.mailbox_identity(mailbox)
        cursor = resume_cursor(self._repository.positions.get(identity), start)
        plan = self._source.plan_mailbox(mailbox, cursor, start)
        last_uid = cursor.last_uid if cursor else 0
        candidates = self._repository.workflow.reply_candidates()
        answered = 0
        for batch in batches(plan.items):
            if stop_requested(stop):
                break
            answered += self._match_batch(mailbox, plan, batch, candidates)
            candidates = [c for c in candidates if not self._is_answered(c.id)]
            last_uid = self._advance(identity, plan, batch, last_uid)
        if answered:
            _log.info("Gesendet-Ordner: %d Mail(s) als beantwortet erkannt", answered)
        return answered

    def _match_batch(
        self, mailbox: str, plan: FetchPlan, batch: Sequence[str], candidates: list[StoredMail]
    ) -> int:
        """Reads one portion of sent mails and marks the mails they answer; returns how many were marked."""
        answered = 0
        for raw in self._source.load_mailbox(mailbox, plan, batch):
            sent = read_sent(raw.data)
            if sent is None:
                continue
            for message_id in answered_ids(sent, candidates):
                if self._repository.workflow.mark_replied(message_id, sent.sent_at):
                    answered += 1
        return answered

    def _is_answered(self, message_id: int) -> bool:
        """Checks whether a mail has an answer by now (or no longer exists)."""
        detail = self._repository.messages.get(message_id)
        return detail is None or detail.answered_at is not None

    def _advance(self, identity: str, plan: FetchPlan, batch: Sequence[str], last_uid: int) -> int:
        """Saves the read position of the sent folder after a portion and returns the new last number."""
        if plan.validity is None:
            return last_uid
        last_uid = max(last_uid, *(int(item) for item in batch))
        self._repository.positions.save(FetchState(identity, plan.validity, last_uid, plan.start))
        return last_uid
