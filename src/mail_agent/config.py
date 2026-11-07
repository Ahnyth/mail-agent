"""Configuration from a TOML file without secrets.

Unknown keys and wrong types are rejected so that typos never silently change behaviour.
Relative paths are resolved against the directory of the configuration file.
"""

import re
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal, cast, get_args

from .security.credentials import TARGET_RE

DEFAULT_CONFIG_NAME = "mail_agent.toml"
"""Configuration file looked for in the working directory when no path is given."""
DEFAULT_MODEL_CLASSIFY = "claude-sonnet-5-5"
"""Fast model that sorts every message."""
DEFAULT_MODEL_DRAFT = "claude-opus-5-5"
"""Stronger model that writes only the drafts that are needed."""
DEFAULT_DATA_DIR = "daten"
"""Folder of the database and the filed attachments."""
DEFAULT_EML_DIR = "daten/eingang"
"""Folder with ``.eml`` files for ``source.type = "eml"`` (test data)."""
DEFAULT_NEW_CASES_DIR = "tests/eval/neu"
"""Where corrections of the office become anonymised test cases."""
DEFAULT_KNOWN_SENDERS = "config/bekannte_absender.yaml"
"""The shared list of known senders."""
DEFAULT_STYLE_GUIDE = "config/stil_leitfaden.md"
"""The office's writing style for drafts."""
DEFAULT_APPLICATION_TEMPLATE = "config/vorlagen/bewerbung_eingang.md"
"""Fixed confirmation of receipt for applications."""
DEFAULT_CARE_REQUEST_TEMPLATE = "config/vorlagen/pflegeanfrage_antwort.md"
"""The office's pattern for replies to care requests."""
DEFAULT_PIPER_MODEL = "daten/stimmen/de_DE-thorsten-high.onnx"
"""German Piper voice for reading the briefing aloud."""
DEFAULT_WHISPER_MODEL = "daten/stimmen/whisper-small"
"""Folder of the faster-whisper model for voice commands."""
DEFAULT_WEB_PORT = 8765
"""Port of the web interface on ``127.0.0.1``."""
WHISPER_MODEL_FILE = "model.bin"
"""A faster-whisper model folder is recognised by this file."""

MAX_PARALLEL = 4
"""Upper limit for ``analysis.parallel``; more AI calls at once mainly use up the subscription faster."""
MAX_FETCH_TIMES = 12
"""Upper limit for ``fetch.times``; more automatic fetches a day bring nothing a manual fetch could not."""
MAX_PORT = 65535
"""The largest TCP port number."""
MAX_IMAP_SINCE_DAYS = 365
"""Upper limit for ``imap.since_days``; a first fetch reaches back at most one year."""
MAX_IMAP_TIMEOUT_SECONDS = 300
"""Upper limit for ``imap.timeout_seconds``; a mailbox that needs longer counts as unreachable."""
MAX_FOLLOW_UP_DAYS = 60
"""Upper limit for ``workflow.follow_up_days``; an unanswered mail must not stay off the list for longer."""
MAX_BRIEFING_DAYS = 90
"""Upper limit for ``workflow.briefing_days``; the morning briefing looks ahead at most three months."""

SourceType = Literal["eml", "imap"]
EngineType = Literal["claude", "claude-code", "rules"]
EffortType = Literal["low", "medium", "high", "xhigh", "max"]

SOURCE_TYPES: tuple[SourceType, ...] = get_args(SourceType)
"""Allowed values of ``source.type``."""
ENGINES: tuple[EngineType, ...] = get_args(EngineType)
"""Allowed values of ``analysis.engine``."""
EFFORTS: tuple[EffortType, ...] = get_args(EffortType)
"""Allowed values of ``analysis.effort_classify`` and ``analysis.effort_draft``."""
TIME_PATTERN = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")
"""A time of day in ``fetch.times``, e.g. ``9:05`` or ``09:05``."""


class ConfigError(ValueError):
    """Invalid configuration; the message names the offending key, never a secret."""


@dataclass(frozen=True)
class OrganizationSettings:
    """The care service as it appears in prompts, drafts and templates."""

    name: str = "Musterpflege GmbH"
    signature: str = "Mit freundlichen Grüßen\n\n[Name]\nMusterpflege GmbH"
    """Added below every draft."""


@dataclass(frozen=True)
class ImapSettings:
    """Access to the mailbox; it is only ever read (see ``sources/imap.py``)."""

    host: str = "imap.example.org"
    port: int = 993
    mailbox: str = "INBOX"
    credential_target: str = "MAIL_AGENT/postfach"
    """Name of the entry in the Windows credential manager (``cmdkey``) that holds the password."""
    allowed_username: str = ""
    """The only mailbox this PC may open; protects against a wrong stored login."""
    since_days: int = 14
    """Only without ``fetch.start_date``: the first fetch starts this many days ago (then fixed)."""
    timeout_seconds: int = 30
    sent_mailbox: str = ""
    """Sent folder for recognising replies; empty: the agent tries the usual names itself."""


@dataclass(frozen=True)
class FetchSettings:
    """Who fetches, from when and at what times.

    Only ONE PC may have ``enabled`` – every PC keeps its own database, so two fetching PCs would
    read and analyse the same mails twice. Mails received before ``start_date`` are never fetched;
    after that every fetch reads everything that arrived since the previous one, however much it is.
    ``times`` are local times for the automatic fetch while the agent runs; a time missed because
    the PC was off is caught up on the next start.
    """

    enabled: bool = True
    start_date: date | None = None
    times: tuple[time, ...] = ()


@dataclass(frozen=True)
class AnalysisSettings:
    """``model_classify`` sorts every message; ``model_draft`` only writes the drafts that are needed.

    With ``rules_first`` the rules decide clear cases alone; the AI is asked when their confidence is
    below ``rules_threshold`` or the message involves payments, deadlines, attachments or an unknown
    sender. ``parallel`` messages are analysed at the same time.
    """

    engine: EngineType = "claude-code"
    """Default: the Claude subscription. ``claude`` (paid API) only when configured explicitly."""
    model_classify: str = DEFAULT_MODEL_CLASSIFY
    model_draft: str = DEFAULT_MODEL_DRAFT
    effort_classify: EffortType = "low"
    effort_draft: EffortType = "high"
    """Only about 5 % of the mails need a draft, so better wording costs little of the subscription."""
    allow_mailbox_data: bool = False
    """Release for sending mails from the real mailbox to the AI; required for IMAP with AI."""
    claude_command: str = "claude"
    rules_first: bool = True
    rules_threshold: float = 0.8
    parallel: int = 2
    known_senders: Path | None = None
    style_guide: Path | None = None
    application_template: Path | None = None
    care_request_template: Path | None = None
    """Pattern the AI adapts for replies to care requests; used as it is without the AI."""
    send_attachment_text: bool = True
    """Send the (shortened) text of attachments to the AI together with the mail."""


@dataclass(frozen=True)
class AttachmentSettings:
    """Reading the text of attachments, with text recognition (Tesseract) for scans."""

    read_text: bool = True
    tesseract_command: str = ""
    """Empty: ``tesseract`` from PATH or the usual Windows install location."""
    ocr_language: str = "deu"
    """Tesseract language codes, e.g. ``deu`` or ``deu+eng``."""


@dataclass(frozen=True)
class WorkflowSettings:
    """The office's lists: follow-ups and the morning briefing."""

    follow_up_days: int = 3
    """Wiedervorlage: messages that need a reply and are older than this many days."""
    briefing_days: int = 14
    """Morning briefing: deadlines within this many days."""


@dataclass(frozen=True)
class Settings:
    """The whole configuration of one PC, with every path already resolved."""

    data_dir: Path
    source_type: SourceType
    eml_dir: Path
    organization: OrganizationSettings = field(default_factory=OrganizationSettings)
    imap: ImapSettings = field(default_factory=ImapSettings)
    fetch: FetchSettings = field(default_factory=FetchSettings)
    analysis: AnalysisSettings = field(default_factory=AnalysisSettings)
    web_port: int = DEFAULT_WEB_PORT
    new_cases_dir: Path | None = None
    attachments: AttachmentSettings = field(default_factory=AttachmentSettings)
    workflow: WorkflowSettings = field(default_factory=WorkflowSettings)
    piper_model: Path | None = None
    whisper_model: Path | None = None
    """Folder of a faster-whisper model for local voice commands; ``None`` disables push-to-talk."""

    @property
    def database_path(self) -> Path:
        """The SQLite database inside ``data_dir``."""
        return self.data_dir / "mail_agent.sqlite3"

    @property
    def filing_root(self) -> Path:
        """The folder inside ``data_dir`` where attachments are filed."""
        return self.data_dir / "ablage"


_SECTIONS: dict[str, set[str]] = {
    "organization": {"name", "signature"},
    "storage": {"data_dir"},
    "source": {"type", "eml_dir"},
    "imap": {
        "host",
        "port",
        "mailbox",
        "credential_target",
        "allowed_username",
        "max_messages",  # no longer used (every fetch reads everything since the last one)
        "since_days",
        "timeout_seconds",
        "sent_mailbox",
    },
    "fetch": {"enabled", "start_date", "times"},
    "analysis": {
        "engine",
        "model_classify",
        "model_draft",
        "effort_classify",
        "effort_draft",
        "allow_mailbox_data",
        "claude_command",
        "rules_first",
        "rules_threshold",
        "parallel",
        "known_senders",
        "style_guide",
        "application_template",
        "care_request_template",
        "send_attachment_text",
    },
    "web": {"port"},
    "eval": {"new_cases_dir"},
    "attachments": {"read_text", "tesseract_command", "ocr_language"},
    "workflow": {"follow_up_days", "briefing_days"},
    "voice": {"piper_model", "whisper_model"},
}
"""Every allowed section of the file with its allowed keys."""
_RENAMED = {
    "analysis.model": "analysis.model_classify und analysis.model_draft",
    "analysis.effort": "analysis.effort_classify und analysis.effort_draft",
}
"""Keys of older files that were replaced; the error message names the new keys."""


@dataclass(frozen=True)
class _Section:
    """One section of the file with checked, typed access to its keys.

    Every error message names the key as it is written in the file, e.g. ``imap.port``.
    """

    name: str
    values: dict[str, Any]
    base_dir: Path
    """Relative paths start here: the directory of the configuration file."""

    def value[T](self, key: str, expected: type[T], default: T) -> T:
        """Returns the value of ``key``, or ``default`` if it is missing.

        Raises:
            ConfigError: The value has a different type.
        """
        value = self.values.get(key, default)
        # bool is a subclass of int; never accept it where a number is expected
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise ConfigError(f"{self.name}.{key}: erwartet {expected.__name__}")
        return value

    def text(self, key: str, default: str) -> str:
        """Returns a text value."""
        return self.value(key, str, default)

    def flag(self, key: str, default: bool) -> bool:
        """Returns a true/false value."""
        return self.value(key, bool, default)

    def number(self, key: str, default: int) -> int:
        """Returns a whole number."""
        return self.value(key, int, default)

    def choice[L: str](self, key: str, allowed: tuple[L, ...], default: L) -> L:
        """Returns a text value that must be one of ``allowed``.

        Raises:
            ConfigError: The value is not allowed.
        """
        value = self.text(key, default)
        if value not in allowed:
            raise ConfigError(f"{self.name}.{key}: erlaubt sind {', '.join(allowed)}")
        return value

    def bounded(self, key: str, default: int, maximum: int) -> int:
        """Returns a whole number from 1 to ``maximum``.

        Raises:
            ConfigError: The number is outside that range.
        """
        value = self.number(key, default)
        if not 1 <= value <= maximum:
            raise ConfigError(f"{self.name}.{key}: muss zwischen 1 und {maximum} liegen")
        return value

    def port(self, key: str, default: int) -> int:
        """Returns a TCP port number.

        Raises:
            ConfigError: The number is no valid port.
        """
        value = self.number(key, default)
        if not 1 <= value <= MAX_PORT:
            raise ConfigError(f"{self.name}.{key}: ungültiger Port")
        return value

    def fraction(self, key: str, default: float) -> float:
        """Returns a number from 0 to 1; whole numbers are accepted, ``true`` is not.

        Raises:
            ConfigError: The value is no number in that range.
        """
        value = self.values.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
            raise ConfigError(f"{self.name}.{key}: erwartet eine Zahl zwischen 0 und 1")
        return float(value)

    def path(self, key: str, default: str) -> Path:
        """Returns a path relative to the configuration file."""
        return self.base_dir / self.text(key, default)

    def optional_file(self, key: str, default: str) -> Path | None:
        """Returns a file; an explicitly configured file must exist, a missing default file is not used.

        Raises:
            ConfigError: The configured file does not exist.
        """
        path = self.path(key, default)
        if path.is_file():
            return path
        if key in self.values:
            raise ConfigError(f"{self.name}.{key}: Datei nicht gefunden ({path.name})")
        return None

    def optional_model_dir(self, key: str, default: str) -> Path | None:
        """Returns a faster-whisper model folder, with the same rules as :meth:`optional_file`.

        Raises:
            ConfigError: The configured folder holds no model.
        """
        path = self.path(key, default)
        if (path / WHISPER_MODEL_FILE).is_file():
            return path
        if key in self.values:
            raise ConfigError(f"{self.name}.{key}: Modellordner nicht gefunden ({path.name})")
        return None


def parse_settings(data: dict[str, Any], base_dir: Path) -> Settings:
    """Builds the settings from the parsed TOML file and checks every value.

    Args:
        data: The content of the file as returned by :mod:`tomllib`.
        base_dir: The directory of the file; relative paths start here.

    Returns:
        The checked settings.

    Raises:
        ConfigError: A section or key is unknown, a value is wrong or the settings contradict each other.
    """
    _check_sections(data)
    sections = {
        name: _Section(name, cast(dict[str, Any], data.get(name, {})), base_dir) for name in _SECTIONS
    }
    storage, source = sections["storage"], sections["source"]
    data_dir = storage.path("data_dir", DEFAULT_DATA_DIR)
    source_type = source.choice("type", SOURCE_TYPES, "eml")
    eml_dir = source.path("eml_dir", DEFAULT_EML_DIR)
    imap = _imap_settings(sections["imap"])
    fetch = _fetch_settings(sections["fetch"], source_type)
    analysis = _analysis_settings(sections["analysis"])
    # The language is checked before the remaining sections, so a file with several mistakes keeps
    # reporting the same one first.
    language = _ocr_language(sections["attachments"])
    settings = Settings(
        data_dir=data_dir,
        source_type=source_type,
        eml_dir=eml_dir,
        organization=_organization_settings(sections["organization"]),
        imap=imap,
        fetch=fetch,
        analysis=analysis,
        web_port=sections["web"].port("port", DEFAULT_WEB_PORT),
        new_cases_dir=sections["eval"].path("new_cases_dir", DEFAULT_NEW_CASES_DIR),
        attachments=_attachment_settings(sections["attachments"], language),
        workflow=_workflow_settings(sections["workflow"]),
        piper_model=sections["voice"].optional_file("piper_model", DEFAULT_PIPER_MODEL),
        whisper_model=sections["voice"].optional_model_dir("whisper_model", DEFAULT_WHISPER_MODEL),
    )
    _check_consistency(settings)
    return settings


def _check_sections(data: dict[str, Any]) -> None:
    """Rejects unknown sections, unknown keys and keys that were replaced.

    Raises:
        ConfigError: The file has a section or key that is not allowed.
    """
    for name, value in data.items():
        if name not in _SECTIONS:
            raise ConfigError(f"Unbekannter Abschnitt: [{name}]")
        if not isinstance(value, dict):
            raise ConfigError(f"[{name}] muss ein Abschnitt sein")
        for key in value:
            if f"{name}.{key}" in _RENAMED:
                raise ConfigError(f"{name}.{key} wurde ersetzt durch {_RENAMED[f'{name}.{key}']}")
        unknown = set(value) - _SECTIONS[name]
        if unknown:
            raise ConfigError(f"Unbekannte Schlüssel in [{name}]: {', '.join(sorted(unknown))}")


def _organization_settings(section: _Section) -> OrganizationSettings:
    """Builds the ``[organization]`` settings."""
    defaults = OrganizationSettings()
    return OrganizationSettings(
        name=section.text("name", defaults.name),
        signature=section.text("signature", defaults.signature),
    )


def _imap_settings(section: _Section) -> ImapSettings:
    """Builds the ``[imap]`` settings.

    Raises:
        ConfigError: A value is wrong, e.g. a credential name the credential manager would not accept.
    """
    defaults = ImapSettings()
    settings = ImapSettings(
        host=section.text("host", defaults.host),
        port=section.port("port", defaults.port),
        mailbox=section.text("mailbox", defaults.mailbox),
        credential_target=section.text("credential_target", defaults.credential_target),
        allowed_username=section.text("allowed_username", defaults.allowed_username),
        since_days=section.bounded("since_days", defaults.since_days, MAX_IMAP_SINCE_DAYS),
        timeout_seconds=section.bounded(
            "timeout_seconds", defaults.timeout_seconds, MAX_IMAP_TIMEOUT_SECONDS
        ),
        sent_mailbox=section.text("sent_mailbox", defaults.sent_mailbox),
    )
    if not TARGET_RE.fullmatch(settings.credential_target):
        raise ConfigError("imap.credential_target: ungültiger Name")
    section.number("max_messages", 0)  # still accepted in older files, no longer used
    return settings


def _fetch_settings(section: _Section, source_type: SourceType) -> FetchSettings:
    """Builds the ``[fetch]`` settings."""
    return FetchSettings(
        # The real mailbox is fetched by one PC only, so it has to be switched on explicitly there.
        enabled=section.flag("enabled", source_type == "eml"),
        start_date=_start_date(section),
        times=_fetch_times(section),
    )


def _start_date(section: _Section) -> date | None:
    """Returns ``fetch.start_date``, a TOML date without quotes, or ``None``.

    Raises:
        ConfigError: The value is no date (e.g. a quoted text or a date with time).
    """
    value = section.values.get("start_date")
    if value is None:
        return None
    # tomllib returns datetime.date for `2026-11-03`; datetime is a subclass and not wanted here
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ConfigError("fetch.start_date: Datum ohne Anführungszeichen, z. B. start_date = 2026-11-03")
    return value


def _fetch_times(section: _Section) -> tuple[time, ...]:
    """Returns ``fetch.times`` sorted and without duplicates.

    Raises:
        ConfigError: The value is no list, too long or holds an invalid time.
    """
    value = section.values.get("times", [])
    if not isinstance(value, list) or len(value) > MAX_FETCH_TIMES:
        raise ConfigError(f'fetch.times: Liste von Uhrzeiten, z. B. ["09:00"] (höchstens {MAX_FETCH_TIMES})')
    times = set()
    for entry in value:
        match = TIME_PATTERN.fullmatch(entry) if isinstance(entry, str) else None
        if match is None:
            raise ConfigError(f'fetch.times: ungültige Uhrzeit {entry!r}, erwartet z. B. "09:00"')
        times.add(time(int(match[1]), int(match[2])))
    return tuple(sorted(times))


def _analysis_settings(section: _Section) -> AnalysisSettings:
    """Builds the ``[analysis]`` settings; configured files must exist, missing default files are not used."""
    defaults = AnalysisSettings()
    return AnalysisSettings(
        engine=section.choice("engine", ENGINES, defaults.engine),
        model_classify=section.text("model_classify", defaults.model_classify),
        model_draft=section.text("model_draft", defaults.model_draft),
        effort_classify=section.choice("effort_classify", EFFORTS, defaults.effort_classify),
        effort_draft=section.choice("effort_draft", EFFORTS, defaults.effort_draft),
        allow_mailbox_data=section.flag("allow_mailbox_data", defaults.allow_mailbox_data),
        claude_command=section.text("claude_command", defaults.claude_command),
        rules_first=section.flag("rules_first", defaults.rules_first),
        rules_threshold=section.fraction("rules_threshold", defaults.rules_threshold),
        parallel=section.bounded("parallel", defaults.parallel, MAX_PARALLEL),
        known_senders=section.optional_file("known_senders", DEFAULT_KNOWN_SENDERS),
        style_guide=section.optional_file("style_guide", DEFAULT_STYLE_GUIDE),
        application_template=section.optional_file("application_template", DEFAULT_APPLICATION_TEMPLATE),
        care_request_template=section.optional_file("care_request_template", DEFAULT_CARE_REQUEST_TEMPLATE),
        send_attachment_text=section.flag("send_attachment_text", defaults.send_attachment_text),
    )


def _ocr_language(section: _Section) -> str:
    """Returns ``attachments.ocr_language``.

    Raises:
        ConfigError: The value is no Tesseract language code like ``deu`` or ``deu+eng``.
    """
    language = section.text("ocr_language", AttachmentSettings().ocr_language)
    if not language.replace("+", "").isalpha():
        raise ConfigError('attachments.ocr_language: z. B. "deu" oder "deu+eng"')
    return language


def _attachment_settings(section: _Section, language: str) -> AttachmentSettings:
    """Builds the ``[attachments]`` settings with the already checked ``language``."""
    defaults = AttachmentSettings()
    return AttachmentSettings(
        read_text=section.flag("read_text", defaults.read_text),
        tesseract_command=section.text("tesseract_command", defaults.tesseract_command),
        ocr_language=language,
    )


def _workflow_settings(section: _Section) -> WorkflowSettings:
    """Builds the ``[workflow]`` settings."""
    defaults = WorkflowSettings()
    return WorkflowSettings(
        follow_up_days=section.bounded("follow_up_days", defaults.follow_up_days, MAX_FOLLOW_UP_DAYS),
        briefing_days=section.bounded("briefing_days", defaults.briefing_days, MAX_BRIEFING_DAYS),
    )


def _check_consistency(settings: Settings) -> None:
    """Rejects combinations that would read the wrong mailbox or send mailbox data without release.

    Raises:
        ConfigError: The mailbox is used without ``imap.allowed_username`` or without release for the AI.
    """
    if settings.source_type == "imap":
        if not settings.imap.allowed_username:
            raise ConfigError("imap.allowed_username muss gesetzt sein (Schutz vor falschem Postfach)")
        if settings.analysis.engine != "rules" and not settings.analysis.allow_mailbox_data:
            raise ConfigError(
                "Postfachdaten an eine Cloud-KI zu senden ist nicht freigegeben: "
                'analysis.engine = "rules" setzen oder analysis.allow_mailbox_data = true '
                "(erst nach Datenschutzprüfung)"
            )


def load_settings(path: Path | None = None) -> Settings:
    """Loads the configuration from ``path``.

    Args:
        path: The configuration file; without a path ``mail_agent.toml`` in the working directory is
            used if present, otherwise the defaults.

    Returns:
        The checked settings.

    Raises:
        ConfigError: The file is missing, no valid TOML or holds a wrong value.
    """
    if path is None:
        candidate = Path.cwd() / DEFAULT_CONFIG_NAME
        if not candidate.is_file():
            return parse_settings({}, Path.cwd())
        path = candidate
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"Konfigurationsdatei nicht gefunden: {path.name}") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Konfigurationsdatei ungültig: {exc}") from None
    return parse_settings(data, path.resolve().parent)
