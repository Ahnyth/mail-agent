"""Building the whole agent from the configuration file."""

import os
from collections.abc import Callable

from ..config import Settings
from ..filing import DocumentStore
from ..handover import Handover
from ..security.credentials import WindowsCredentialProvider
from ..sources import MailSource
from ..sources.eml_folder import EmlFolderSource
from ..sources.imap import ImapSource
from ..speech_input import WhisperTranscriber
from ..storage import MailRepository
from ..voice import PiperSpeaker, WindowsSpeaker
from .agent import MailAgentService, ServiceOptions
from .setup import build_analyzer, build_drafter, build_ocr, build_senders


def build_source(settings: Settings) -> MailSource:
    """Returns the real mailbox (read-only IMAP) or the folder with test mails, as configured."""
    if settings.source_type == "imap":
        return ImapSource(settings.imap, WindowsCredentialProvider())
    return EmlFolderSource(settings.eml_dir)


def build_handover(settings: Settings) -> Handover | None:
    """Returns the hand-over; only with the real mailbox, whose password is the key (Windows store)."""
    if settings.source_type != "imap":
        return None
    return Handover(settings.data_dir, password_reader(settings.imap.credential_target))


def password_reader(target: str) -> Callable[[], str]:
    """Returns a function that reads the mailbox password from the Windows store.

    The password is read only at the moment a package is written or taken over and is never kept, so it
    does not stay in memory longer than needed.
    """

    def read() -> str:
        """Reads the password of ``target`` from the Windows credential store."""
        return WindowsCredentialProvider().get_credential(target).password.reveal()

    return read


def build_options(settings: Settings) -> ServiceOptions:
    """Returns the optional parts of the agent as the configuration describes them."""
    return ServiceOptions(
        parallel=settings.analysis.parallel,
        new_cases_dir=settings.new_cases_dir,
        senders=build_senders(settings),
        ocr=build_ocr(settings) if settings.attachments.read_text else None,
        read_attachment_text=settings.attachments.read_text,
        workflow=settings.workflow,
        speaker=PiperSpeaker(settings.piper_model) if settings.piper_model else None,
        transcriber=WhisperTranscriber(settings.whisper_model) if settings.whisper_model else None,
        drafter=build_drafter(settings),
        organization=settings.organization,
        fetch=settings.fetch,
        settings=settings,
        windows_voice=WindowsSpeaker() if os.name == "nt" else None,
        handover=build_handover(settings),
    )


def build_service(settings: Settings) -> MailAgentService:
    """Returns the agent with all parts the configuration asks for."""
    return MailAgentService(
        repository=MailRepository(settings.database_path),
        analyzer=build_analyzer(settings),
        documents=DocumentStore(settings.filing_root),
        source=build_source(settings),
        options=build_options(settings),
    )
