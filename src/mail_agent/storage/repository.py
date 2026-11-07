"""The repository: all stores of the agent's database, built on one SQLite file."""

from pathlib import Path

from .analyses import AnalysisStore
from .app_state import AppState
from .corrections import CorrectionStore
from .database import Database
from .documents import DocumentRecords
from .history import SenderHistory
from .inbox import InboxQueries
from .messages import MessageStore
from .positions import FetchPositions
from .runs import RunLog
from .workflow import WorkflowStore


class MailRepository:
    """The agent's database, divided into one small store per area.

    Example: ``repository.messages.get(7)`` loads a message, ``repository.workflow.set_status(7, …)``
    marks it as done. Every store opens its own short connections, so the import thread and the web page
    can work at the same time.
    """

    def __init__(self, path: Path) -> None:
        """Opens the database at ``path`` and brings its layout up to date."""
        self.database = Database(path)
        self.database.migrate()
        self.messages = MessageStore(self.database)
        """Adding, finding and loading messages."""
        self.inbox = InboxQueries(self.database)
        """The inbox list and the counters of its filters."""
        self.workflow = WorkflowStore(self.database)
        """Open or done, answered or not."""
        self.analyses = AnalysisStore(self.database)
        """New analyses, edited drafts, messages that may be analysed again."""
        self.corrections = CorrectionStore(self.database)
        """Corrections by hand with their original values."""
        self.documents = DocumentRecords(self.database)
        """Single attachments and the texts read from them."""
        self.history = SenderHistory(self.database)
        """Earlier mails of a sender, without content."""
        self.positions = FetchPositions(self.database)
        """How far the inbox and the sent folder have been read."""
        self.runs = RunLog(self.database)
        """Every fetch and AI catch-up with its result."""
        self.state = AppState(self.database)
        """Small values the agent remembers between starts."""

    @property
    def path(self) -> Path:
        """The database file."""
        return self.database.path

    def migrate(self) -> None:
        """Brings the layout up to date, e.g. after taking over a package of an older version."""
        self.database.migrate()
