"""The agent behind the web interface and the jobs: fetching, analysing, filing and everything around it."""

import threading
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from ..analysis import Analyzer
from ..analysis.claude import ClaudeAnalyzer
from ..analysis.rules import RuleBasedAnalyzer
from ..attachments import TesseractOcr
from ..config import FetchSettings, OrganizationSettings, Settings, WorkflowSettings
from ..filing import DocumentStore
from ..handover import Handover, PackageHeader
from ..models import Analysis
from ..parsing import parse_message
from ..senders import KnownSender, SenderDirectory
from ..sources import MailSource
from ..sources.imap import ImapSource
from ..speech_input import WhisperTranscriber
from ..storage import MailRepository
from ..voice import WindowsSpeaker
from .cases import CaseExporter
from .execution import JobExecution
from .handover_sync import HandoverSync
from .importer import MailImporter
from .preparation import MessagePreparer
from .reanalysis import Reanalyzer
from .report import ImportReport, Progress, no_progress
from .sender_book import SenderBook, local_senders_path
from .sent_sync import SENT_CHECKED_STATE, SentFolderSync
from .setup import build_analyzer, build_drafter, build_senders


@dataclass
class ServiceOptions:
    """The optional parts of the agent; tests leave out what they do not need."""

    parallel: int = 1
    """How many messages are analysed at the same time."""
    new_cases_dir: Path | None = None
    """Where corrections become anonymised test cases; ``None`` switches the export off."""
    senders: SenderDirectory = field(default_factory=SenderDirectory)
    ocr: TesseractOcr | None = None
    read_attachment_text: bool = True
    workflow: WorkflowSettings = field(default_factory=WorkflowSettings)
    transcriber: WhisperTranscriber | None = None
    drafter: ClaudeAnalyzer | None = None
    organization: OrganizationSettings = field(default_factory=OrganizationSettings)
    fetch: FetchSettings = field(default_factory=FetchSettings)
    settings: Settings | None = None
    windows_voice: WindowsSpeaker | None = None
    handover: Handover | None = None


class MailAgentService:
    """The single entry point for the web interface, the jobs and the console.

    The work itself is done by small helpers: ``MailImporter`` (fetch), ``Reanalyzer``, ``SentFolderSync``,
    ``HandoverSync``, ``SenderBook`` and ``CaseExporter``. This class keeps the configured parts and creates
    the helpers per call from them, so a changed part (e.g. a newly saved sender) takes effect at once.

    Attributes:
        transcriber: Speech recognition for push-to-talk; ``None`` without the optional package.
        drafter: Claude for "Von KI formulieren lassen"; ``None`` without AI.
        settings: Set by ``build_service``; needed to rebuild the analysis after a sender was saved.
        windows_voice: Reads aloud with the German Windows voice (offline); ``None`` outside Windows.
        handover: Hand-over between the two PCs; ``None`` without a mailbox (test data).
    """

    def __init__(
        self,
        repository: MailRepository,
        analyzer: Analyzer,
        documents: DocumentStore,
        source: MailSource,
        options: ServiceOptions | None = None,
    ) -> None:
        """Assembles the agent from its parts.

        Args:
            repository: The database.
            analyzer: Rules, Claude or both.
            documents: The filing folder for attachments.
            source: The mailbox (IMAP) or a folder with ``.eml`` files.
            options: Everything optional; the defaults suit tests with test data.
        """
        options = options or ServiceOptions()
        self.repository = repository
        self.analyzer = analyzer
        self.documents = documents
        self.source = source
        self.parallel = options.parallel
        self.new_cases_dir = options.new_cases_dir
        self.senders = options.senders
        self.ocr = options.ocr
        self.read_attachment_text = options.read_attachment_text
        self.workflow = options.workflow
        self.transcriber = options.transcriber
        self.drafter = options.drafter
        self.organization = options.organization
        self.fetch = options.fetch
        self.settings = options.settings
        self.windows_voice = options.windows_voice
        self.handover = options.handover
        self._handover_sync = HandoverSync(options.handover)

    # --- hand-over between the two PCs -----------------------------------------------------------

    @property
    def handover_configured(self) -> bool:
        """Whether a shared hand-over folder is set up."""
        return self._handover_sync.configured

    def may_fetch(self) -> bool:
        """Checks whether this PC may fetch: with a shared folder only the PC that holds the data does."""
        return self._handover_sync.may_fetch(self.fetch.enabled)

    def note_change(self) -> None:
        """Remembers a change (fetch, status, draft, …), so a new package is written once things are quiet."""
        self._handover_sync.note_change()

    def export_handover(self) -> PackageHeader:
        """Writes a hand-over package now (raises ``HandoverError`` if that is impossible)."""
        return self._handover_sync.export()

    def take_over(self) -> PackageHeader:
        """Installs the package of the other PC; afterwards this PC fetches."""
        header = self._handover_sync.take_over()
        self.repository.migrate()  # the package may come from an older version of the agent
        self.reload_senders()
        return header

    def handover_tick(self, busy: bool = False, wait_for_quiet: bool = True) -> bool:
        """Writes a package if the data changed and things are quiet (called by the schedule)."""
        return self._handover_sync.tick(busy=busy, wait_for_quiet=wait_for_quiet)

    # --- analysis and known senders -------------------------------------------------------------------------

    @property
    def uses_ai(self) -> bool:
        """Whether Claude takes part in the analysis (otherwise only the rules)."""
        return not isinstance(self.analyzer, RuleBasedAnalyzer)

    @property
    def local_senders_path(self) -> Path | None:
        """The file of the senders saved in the web interface; ``None`` without settings."""
        return local_senders_path(self.settings) if self.settings else None

    def reload_senders(self) -> None:
        """Uses a changed list of known senders right away for analysis, warnings and drafts."""
        if self.settings is None:
            return
        self.senders = build_senders(self.settings)
        self.analyzer = build_analyzer(self.settings)
        self.drafter = build_drafter(self.settings)

    def same_sender_ids(self, sender_address: str, reply_to: str, *, open_only: bool = True) -> list[int]:
        """Returns the messages from the same company, newest first (by Reply-To for Lexware & Co.)."""
        return self._sender_book().same_sender_ids(sender_address, reply_to, open_only=open_only)

    def save_known_sender(self, sender: KnownSender) -> list[int]:
        """Saves ``sender`` as known and uses it at once; returns its open messages for a new analysis.

        Raises:
            ValueError: There is no list of known senders without settings.
        """
        self._sender_book().save(sender)
        self.reload_senders()
        return self._sender_book().ids_of(sender)

    def forget_known_sender(self, identity: str) -> bool:
        """Removes a sender saved in the web interface (``identity``: address or domain)."""
        if not self._sender_book().forget(identity):
            return False
        self.reload_senders()
        return True

    # --- fetch and re-analysis --------------------------------------------------------------------------

    def start_date(self) -> date | None:
        """Returns the day before which no mail is fetched.

        That is the configured date, otherwise the date fixed by the first fetch (``imap.since_days`` back
        from then).
        """
        if self.fetch.start_date is not None:
            return self.fetch.start_date
        state = self.repository.positions.get(self.source.identity)
        return state.start_date if state else None

    def import_new(
        self, progress: Progress = no_progress, stop: threading.Event | None = None
    ) -> ImportReport:
        """Fetches, analyses and stores every message received since the last fetch.

        The mailbox is never changed.

        Raises:
            SourceError: The source could not be read; everything stored up to then is kept.
        """
        importer = MailImporter(self.repository, self.source, self.documents, self._preparer())
        return importer.run(self.start_date(), progress, stop, parallel=self.parallel)

    def sent_mailbox(self) -> str | None:
        """Returns the sent folder: configured, found earlier, or found now by trying the usual names."""
        sync = self._sent_sync()
        return sync.mailbox() if sync else None

    def sync_sent(self, stop: threading.Event | None = None) -> int:
        """Marks mails answered in the sent folder as "beantwortet" and "erledigt"; returns how many."""
        sync = self._sent_sync()
        if sync is None:  # test data has no sent folder; the inbox still shows when it was looked at
            self.repository.state.set(SENT_CHECKED_STATE, datetime.now(UTC).isoformat())
            return 0
        return sync.sync(self.start_date(), stop)

    def reanalyze(self, message_id: int) -> Analysis | None:
        """Analyses a stored message again (e.g. after setting up the AI); an edited draft is replaced."""
        return Reanalyzer(self.repository, self._preparer()).one(message_id)

    def reanalyze_pending(
        self,
        progress: Progress = no_progress,
        stop: threading.Event | None = None,
        ids: list[int] | None = None,
    ) -> ImportReport:
        """Analyses untouched open messages again: those waiting for the AI, or the given ``ids``."""
        return Reanalyzer(self.repository, self._preparer()).untouched(
            ids, progress, stop, parallel=self.parallel
        )

    def run(
        self,
        kind: str,
        trigger: str,
        progress: Progress = no_progress,
        stop: threading.Event | None = None,
        ids: list[int] | None = None,
    ) -> ImportReport:
        """Runs a fetch, the AI catch-up or a re-analysis of ``ids`` and records it in the run log."""
        return JobExecution(self, progress, stop).run(kind, trigger, ids)

    # --- drafts and test cases --------------------------------------------------------------------------

    def draft_from_dictation(self, message_id: int, notes: str) -> str | None:
        """Lets Claude turn dictated notes into a polite reply and stores it (still only a draft)."""
        raw = self.repository.messages.raw(message_id)
        if raw is None or self.drafter is None:
            return None
        draft = self.drafter.draft_from_notes(parse_message(raw), notes)
        self.repository.analyses.save_draft(message_id, draft)
        return draft

    def export_correction(self, message_id: int) -> Path | None:
        """Writes the latest correction of a message as an anonymised test case for review."""
        return CaseExporter(self.repository, self.new_cases_dir).export(message_id)

    # --- helpers, created per call from the current parts --------------------------------------------------

    def _sender_book(self) -> SenderBook:
        """Returns the helper for known senders and mails of one sender."""
        return SenderBook(self.repository, self.local_senders_path)

    def _preparer(self) -> MessagePreparer:
        """Returns the helper that completes and analyses one message."""
        book = self._sender_book()
        return MessagePreparer(self.analyzer, book.history, self.ocr, read_text=self.read_attachment_text)

    def _sent_sync(self) -> SentFolderSync | None:
        """Returns the helper for the sent folder; only the real mailbox has one."""
        if not isinstance(self.source, ImapSource):
            return None
        configured = self.settings.imap.sent_mailbox if self.settings else ""
        return SentFolderSync(self.repository, self.source, configured)
