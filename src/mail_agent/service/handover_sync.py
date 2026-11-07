"""When the hand-over package is written: after changes, once things are quiet, never during a fetch."""

import threading
import time

from ..handover import Handover, HandoverError, PackageHeader
from ..security.redaction import get_logger

_log = get_logger("service")

EXPORT_QUIET_SECONDS = 60
"""The package is written a minute after the last change, not after every click."""


class HandoverSync:
    """Writes and takes over hand-over packages; only one of them runs at a time."""

    def __init__(self, handover: Handover | None) -> None:
        """Works on ``handover``; ``None`` means there is no hand-over (test data without a mailbox)."""
        self._handover = handover
        self._lock = threading.Lock()
        self._last_change = 0.0

    @property
    def configured(self) -> bool:
        """Whether a shared folder is set up."""
        return self._handover is not None and self._handover.configured

    def may_fetch(self, fetch_enabled: bool) -> bool:
        """Checks whether this PC may fetch: with a shared folder only the PC that holds the data does.

        Args:
            fetch_enabled: The fetch setting of the configuration, used without a shared folder.

        Returns:
            Whether a fetch may start.
        """
        if self._handover is not None and self._handover.configured:
            return self._handover.is_active()
        return fetch_enabled

    def note_change(self) -> None:
        """Remembers that the data changed, so a new package is written once things are quiet."""
        self._last_change = time.monotonic()
        if self._handover is not None:
            self._handover.note_change()

    def export(self) -> PackageHeader:
        """Writes a package now.

        Raises:
            HandoverError: No hand-over is set up, or the package could not be written.
        """
        handover = self._require()
        with self._lock:
            return handover.export()

    def take_over(self) -> PackageHeader:
        """Installs the package of the other PC; afterwards this PC fetches.

        Raises:
            HandoverError: No hand-over is set up, or the package could not be installed.
        """
        handover = self._require()
        with self._lock:
            return handover.take_over()

    def tick(self, *, busy: bool = False, wait_for_quiet: bool = True) -> bool:
        """Writes a package if the data changed and the last change is a minute old.

        Args:
            busy: A fetch or analysis is running; then nothing is written.
            wait_for_quiet: ``False`` when the agent stops, so the changes of the last minute go into the
                package right away and the other PC does not take over an old state.

        Returns:
            Whether a package was written.
        """
        handover = self._handover
        if handover is None or busy or not handover.configured:
            return False
        state = handover.state()
        if not (state.active and state.changed):
            return False
        if wait_for_quiet and time.monotonic() - self._last_change < EXPORT_QUIET_SECONDS:
            return False
        try:
            self.export()
        except HandoverError as exc:
            _log.warning("Übergabe nicht geschrieben (%s) – neuer Versuch später", exc.code)
            return False
        return True

    def _require(self) -> Handover:
        """Returns the hand-over or raises ``handover_not_configured``."""
        if self._handover is None:
            raise HandoverError("handover_not_configured")
        return self._handover
