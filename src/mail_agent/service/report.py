"""The report of a fetch or re-analysis, and the progress callback that the jobs and the console use."""

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class ImportReport:
    """What a fetch or re-analysis did so far; shown as progress and stored in the run log."""

    imported: int = 0
    duplicates: int = 0
    failed: list[str] = field(default_factory=list)
    found: int = 0
    """Messages the source reported as new for this run."""
    ai_pending: int = 0
    """Imported (or re-analysed) messages that still wait for the AI."""
    stopped: bool = False
    answered: int = 0
    """Mails marked as answered because a reply was found in the sent folder."""

    @property
    def processed(self) -> int:
        """Messages handled so far: new, already known or unreadable."""
        return self.imported + self.duplicates + len(self.failed)

    @property
    def text(self) -> str:
        """The result as one German sentence, e.g. "3 neu eingelesen, 1 bereits vorhanden"."""
        parts = [f"{self.imported} neu eingelesen", f"{self.duplicates} bereits vorhanden"]
        if self.failed:
            parts.append(f"{len(self.failed)} nicht lesbar")
        if self.ai_pending:
            parts.append(f"{self.ai_pending} warten auf die KI")
        if self.answered:
            parts.append(f"{self.answered} als beantwortet erkannt")
        if self.stopped:
            parts.append("unterbrochen – der Rest folgt beim nächsten Abruf")
        return ", ".join(parts)


Progress = Callable[[str, ImportReport], None]
"""Called with a phase ("suchen", "lesen", "auswerten", "gesendet") and the report so far."""


def no_progress(_phase: str, _report: ImportReport) -> None:
    """Ignores progress; the default when nobody watches."""
