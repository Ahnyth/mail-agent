"""The agent's work flow: fetch → analyse → file attachments → store, plus everything around it.

``MailAgentService`` is the single entry point; the helpers in this package each do one part of the work.
``build_service`` assembles everything from the configuration.
"""

from .agent import MailAgentService, ServiceOptions
from .factory import build_handover, build_options, build_service, build_source
from .preparation import unanalysed
from .report import ImportReport, Progress, no_progress
from .sender_book import LOCAL_SENDERS_FILE, local_senders_path
from .sent_sync import SENT_CHECKED_STATE, SENT_MAILBOX_STATE
from .setup import build_analyzer, build_drafter, build_ocr, build_senders, care_template

__all__ = [
    "LOCAL_SENDERS_FILE",
    "SENT_CHECKED_STATE",
    "SENT_MAILBOX_STATE",
    "ImportReport",
    "MailAgentService",
    "Progress",
    "ServiceOptions",
    "build_analyzer",
    "build_drafter",
    "build_handover",
    "build_ocr",
    "build_options",
    "build_senders",
    "build_service",
    "build_source",
    "care_template",
    "local_senders_path",
    "no_progress",
    "unanalysed",
]
