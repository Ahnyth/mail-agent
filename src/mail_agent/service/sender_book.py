"""Known senders maintained in the web interface, and the mails that belong to one sender."""

from pathlib import Path

from ..config import Settings
from ..models import MailMessage, Status
from ..senders import KnownSender, SenderDirectory, identity_address, remove_local_sender, save_local_sender
from ..storage import MailRepository

LOCAL_SENDERS_FILE = "bekannte_absender.yaml"
"""Senders saved in the web interface; next to the database, only on this PC (part of the hand-over)."""
HISTORY_LIMIT = 6
"""Earlier classifications of the same sender passed to the AI (newest first)."""


def local_senders_path(settings: Settings) -> Path:
    """Returns the file of the senders saved in the web interface."""
    return settings.data_dir / LOCAL_SENDERS_FILE


class SenderBook:
    """Groups mails by sender and edits the list of senders saved in the web interface."""

    def __init__(self, repository: MailRepository, local_path: Path | None) -> None:
        """Works on ``repository``; ``local_path`` is ``None`` without settings (tests with test data)."""
        self._repository = repository
        self._local_path = local_path

    def same_sender_ids(self, sender_address: str, reply_to: str, *, open_only: bool = True) -> list[int]:
        """Returns the messages from the same company, newest first.

        Mails sent through a service (Lexware & Co.) are grouped by their Reply-To address.
        """
        identity = identity_address(sender_address, reply_to)
        rows = self._repository.history.sender_rows(Status.OPEN if open_only else None)
        return [i for i, address, reply in rows if identity_address(address, reply) == identity]

    def ids_of(self, sender: KnownSender) -> list[int]:
        """Returns the open messages that ``sender`` covers (exact address, or domain with subdomains)."""
        directory = SenderDirectory(senders=(sender,))
        rows = self._repository.history.sender_rows(Status.OPEN)
        return [i for i, address, reply in rows if directory.lookup_message(address, reply) is not None]

    def history(self, message: MailMessage, exclude_id: int | None = None) -> tuple[str, ...]:
        """Returns how the latest mails of the sender were classified, for the AI (no mail content)."""
        ids = [
            i
            for i in self.same_sender_ids(message.sender_address, message.reply_to, open_only=False)
            if i != exclude_id
        ]
        return self._repository.history.lines(ids[:HISTORY_LIMIT])

    def save(self, sender: KnownSender) -> None:
        """Adds ``sender`` to the list of this PC.

        Raises:
            ValueError: There is no list without settings.
        """
        if self._local_path is None:
            raise ValueError("no_settings")
        save_local_sender(self._local_path, sender)

    def forget(self, identity: str) -> bool:
        """Removes a sender saved in the web interface (``identity``: address or domain).

        Returns:
            ``False`` if the entry is not in the list of this PC.
        """
        return self._local_path is not None and remove_local_sender(self._local_path, identity)
