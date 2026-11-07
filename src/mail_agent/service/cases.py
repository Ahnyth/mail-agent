"""Turning a correction by hand into an anonymised test case for the evaluation."""

from datetime import date
from pathlib import Path

from ..eval.anonymize import case_from_correction, write_case
from ..security.redaction import get_logger
from ..storage import MailRepository

_log = get_logger("service")


class CaseExporter:
    """Writes the latest correction of a message as a test case into the folder for new cases."""

    def __init__(self, repository: MailRepository, folder: Path | None) -> None:
        """Works on ``repository``; without ``folder`` nothing can be exported."""
        self._repository = repository
        self._folder = folder

    def export(self, message_id: int) -> Path | None:
        """Writes the test case of a message for review before it joins the collection.

        Returns:
            The new file, or ``None`` without a correction or without a folder.
        """
        detail = self._repository.messages.get(message_id)
        if detail is None or detail.correction is None or self._folder is None:
            return None
        case_id, text = case_from_correction(detail, detail.correction, date.today())
        path = write_case(self._folder, case_id, text)
        self._repository.corrections.mark_exported(detail.correction.id, path.name)
        _log.info("Korrektur als Testfall gespeichert: %s", path.name)
        return path
