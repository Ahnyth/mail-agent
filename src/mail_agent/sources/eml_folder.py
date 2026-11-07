"""Read ``.eml`` files from a local folder (test data, or messages saved from a mail client).

There is no stored position: every fetch looks at all files, duplicates are recognised by their
Message-ID. The start date does not apply (test files have arbitrary dates).
"""

from collections.abc import Iterator, Sequence
from datetime import date
from pathlib import Path

from ..parsing import MAX_MESSAGE_BYTES
from ..security.redaction import get_logger
from . import FetchCursor, FetchPlan, RawMail, SourceError

_log = get_logger("sources.eml")


class EmlFolderSource:
    """A folder of ``.eml`` files as a mail source; files are only read, never changed."""

    def __init__(self, folder: Path, max_bytes: int = MAX_MESSAGE_BYTES) -> None:
        """Reads ``folder``; files larger than ``max_bytes`` are skipped."""
        self._folder = folder
        self._max_bytes = max_bytes

    @property
    def identity(self) -> str:
        """Returns the name of the source, made from the folder name."""
        return f"eml:{self._folder.name}"

    def plan(self, cursor: FetchCursor | None = None, start: date | None = None) -> FetchPlan:
        """Returns all ``.eml`` files of the folder, sorted by name; ``cursor`` and ``start`` do not apply.

        Raises:
            SourceError: ``eml_folder_missing`` if the folder does not exist.
        """
        if not self._folder.is_dir():
            raise SourceError("eml_folder_missing")
        return FetchPlan(tuple(path.name for path in sorted(self._folder.glob("*.eml"))))

    def load(self, plan: FetchPlan, items: Sequence[str]) -> Iterator[RawMail]:
        """Yields the files ``items``; links, missing and oversized files are skipped with a log entry."""
        for name in items:
            path = self._folder / name
            if path.is_symlink() or not path.is_file():
                _log.warning("Übersprungen (keine reguläre Datei): %s", path.name)
                continue
            if path.stat().st_size > self._max_bytes:
                _log.warning("Übersprungen (zu groß): %s", path.name)
                continue
            yield RawMail(data=path.read_bytes(), origin=f"eml:{path.name}", item=name)

    def fetch(self) -> Iterator[RawMail]:
        """Yields all messages at once (tests and tools)."""
        plan = self.plan()
        yield from self.load(plan, plan.items)
