import threading
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import date

import pytest

from mail_agent import service as service_module
from mail_agent.analysis.claude_code import ClaudeCodeAnalyzer
from mail_agent.analysis.fallback import FallbackAnalyzer
from mail_agent.analysis.routing import RulesFirstAnalyzer
from mail_agent.analysis.rules import RuleBasedAnalyzer
from mail_agent.config import FetchSettings, OrganizationSettings, Settings, parse_settings
from mail_agent.models import Analysis, Category, MailMessage
from mail_agent.sample_data import write_samples
from mail_agent.service import MailAgentService, batching, build_analyzer, build_source
from mail_agent.sources import FetchCursor, FetchPlan, RawMail, SourceError
from mail_agent.sources.eml_folder import EmlFolderSource
from mail_agent.sources.imap import ImapSource
from mail_agent.storage import FetchState, InboxFilter
from tests.helpers import make_mail


@dataclass
class ListSource:
    mails: list[RawMail]
    identity: str = "liste"

    def plan(self, cursor: FetchCursor | None, start: date | None) -> FetchPlan:
        return FetchPlan(tuple(str(i) for i in range(len(self.mails))))

    def load(self, plan: FetchPlan, items: Sequence[str]) -> Iterator[RawMail]:
        for item in items:
            yield self.mails[int(item)]


@dataclass
class MailboxSource:
    """Like the IMAP source: UIDs, a validity, a start date and a stored position."""

    uids: list[int]
    validity: str = "7"
    identity: str = "imap:test"
    plans: list[tuple[FetchCursor | None, date | None]] = field(default_factory=list)
    loads: list[list[str]] = field(default_factory=list)
    fail_on_load: int | None = None

    def plan(self, cursor: FetchCursor | None, start: date | None) -> FetchPlan:
        self.plans.append((cursor, start))
        after = cursor.last_uid if cursor and cursor.validity == self.validity else 0
        return FetchPlan(tuple(str(u) for u in self.uids if u > after), self.validity, start)

    def load(self, plan: FetchPlan, items: Sequence[str]) -> Iterator[RawMail]:
        self.loads.append(list(items))
        if self.fail_on_load is not None and len(self.loads) == self.fail_on_load:
            raise SourceError("imap_connection_lost")
        for item in items:
            yield RawMail(
                make_mail(message_id=f"<{item}@x>", subject=f"Nr. {item}"), f"imap:INBOX/{item}", item
            )


def test_import_is_idempotent_and_files_documents(service: MailAgentService, settings: Settings) -> None:
    write_samples(settings.eml_dir)
    first = service.import_new()
    assert (first.imported, first.duplicates, first.failed) == (13, 0, [])
    second = service.import_new()
    assert (second.imported, second.duplicates) == (0, 13)

    stored = sorted(p.name for p in settings.filing_root.rglob("*.pdf"))
    assert "Verordnung_HKP_Sommer.pdf" in stored
    assert not list(settings.filing_root.rglob("*.exe"))
    assert service.repository.inbox.counts()["offen"] == 13


def test_unreadable_mail_is_reported_not_fatal(service: MailAgentService) -> None:
    service.source = ListSource([RawMail(b"", "eml:leer.eml")])
    report = service.import_new()
    assert report.failed == ["eml:leer.eml: message_empty"]
    assert "1 nicht lesbar" in report.text


def test_reanalyze_updates_stored_analysis(service: MailAgentService, settings: Settings) -> None:
    write_samples(settings.eml_dir)
    service.import_new()
    first = service.repository.inbox.messages(InboxFilter(category=Category.COMPLAINT))[0]
    service.repository.analyses.save_draft(first.id, "eigener Text")
    analysis = service.reanalyze(first.id)
    assert analysis is not None
    detail = service.repository.messages.get(first.id)
    assert detail is not None
    assert not detail.draft_edited
    assert service.reanalyze(999) is None


def test_factories(settings: Settings) -> None:
    assert isinstance(build_analyzer(settings), RuleBasedAnalyzer)
    claude_settings = parse_settings({}, settings.data_dir.parent)
    assert isinstance(build_analyzer(claude_settings), RulesFirstAnalyzer)
    assert isinstance(build_analyzer(claude_settings, rules_first=False), FallbackAnalyzer)
    code_settings = parse_settings(
        {"analysis": {"engine": "claude-code", "rules_first": False}}, settings.data_dir.parent
    )
    analyzer = build_analyzer(code_settings)
    assert isinstance(analyzer, FallbackAnalyzer)
    assert isinstance(analyzer._primary, ClaudeCodeAnalyzer)
    assert isinstance(build_source(settings), EmlFolderSource)
    imap_settings = parse_settings(
        {
            "source": {"type": "imap"},
            "imap": {"allowed_username": "t@example.org"},
            "analysis": {"engine": "rules"},
        },
        settings.data_dir.parent,
    )
    assert isinstance(build_source(imap_settings), ImapSource)


@dataclass
class SlowAnalyzer:
    """Records how many analyses run at the same time."""

    active: int = 0
    peak: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def analyze(self, message: MailMessage) -> Analysis:
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.05)
        with self.lock:
            self.active -= 1
        return RuleBasedAnalyzer(OrganizationSettings()).analyze(message)


def test_parallel_import_keeps_every_message_and_measures_time(
    service: MailAgentService, settings: Settings
) -> None:
    write_samples(settings.eml_dir)
    analyzer = SlowAnalyzer()
    service.analyzer = analyzer
    service.parallel = 3
    assert service.import_new().imported == 13
    assert analyzer.peak > 1
    summaries = service.repository.inbox.messages()
    assert len(summaries) == 13
    detail = service.repository.messages.get(summaries[0].id)
    assert detail is not None
    assert detail.analysis.duration_ms is not None


def test_duplicates_within_one_batch_are_stored_once(service: MailAgentService) -> None:
    mail = RawMail(make_mail(message_id="<same@x>"), "eml:a.eml")
    service.source = ListSource([mail, RawMail(mail.data, "eml:b.eml")])
    report = service.import_new()
    assert (report.imported, report.duplicates) == (1, 1)


def test_fetch_reads_everything_in_portions_and_remembers_the_position(
    service: MailAgentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batching, "BATCH_SIZE", 2)
    source = MailboxSource([3, 4, 8, 9, 12])
    service.source = source
    service.fetch = FetchSettings(start_date=date(2026, 11, 3))
    phases: list[tuple[str, int]] = []
    report = service.import_new(lambda phase, r: phases.append((phase, r.processed)))
    assert (report.found, report.imported) == (5, 5)  # no upper limit
    assert source.loads == [["3", "4"], ["8", "9"], ["12"]]
    assert phases[0] == ("suchen", 0)
    assert phases[-1] == ("auswerten", 5)
    assert service.repository.positions.get("imap:test") == FetchState(
        "imap:test", "7", 12, date(2026, 11, 3)
    )

    source.uids += [13, 15]  # two new mails arrive
    second = service.import_new()
    assert (second.found, second.imported) == (2, 2)
    assert source.plans[-1] == (FetchCursor("7", 12), date(2026, 11, 3))


def test_interrupted_fetch_continues_where_it_stopped(
    service: MailAgentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batching, "BATCH_SIZE", 2)
    source = MailboxSource([1, 2, 3, 4, 5], fail_on_load=2)
    service.source = source
    with pytest.raises(SourceError):
        service.import_new()
    assert service.repository.positions.get("imap:test") is not None
    assert service.repository.positions.get("imap:test").last_uid == 2  # type: ignore[union-attr]
    source.fail_on_load = None
    report = service.import_new()
    assert (report.found, report.imported) == (3, 3)
    assert len(service.repository.inbox.messages()) == 5


def test_stop_request_ends_after_the_current_portion(
    service: MailAgentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batching, "BATCH_SIZE", 2)
    service.source = MailboxSource([1, 2, 3, 4])
    stop = threading.Event()

    def progress(phase: str, report: service_module.ImportReport) -> None:
        if report.imported == 2:
            stop.set()

    report = service.import_new(progress, stop)
    assert report.stopped
    assert report.imported == 2
    assert "Rest folgt beim nächsten Abruf" in report.text


def test_first_fetch_fixes_the_start_date(service: MailAgentService) -> None:
    source = MailboxSource([1])
    service.source = source
    service.fetch = FetchSettings(start_date=None)
    service.import_new()
    assert source.plans[0] == (None, None)  # the source chose since_days and reported its date …
    service.repository.positions.save(FetchState("imap:test", "7", 1, date(2026, 10, 20)))
    service.import_new()
    assert source.plans[-1] == (FetchCursor("7", 1), date(2026, 10, 20))  # … which then stays fixed


def test_earlier_start_date_reads_older_mails_again(service: MailAgentService) -> None:
    source = MailboxSource([1, 2, 3])
    service.source = source
    service.repository.positions.save(FetchState("imap:test", "7", 3, date(2026, 11, 3)))
    service.fetch = FetchSettings(start_date=date(2026, 11, 2))
    report = service.import_new()
    assert source.plans[-1] == (None, date(2026, 11, 2))
    assert report.found == 3


def test_broken_analysis_still_imports_the_message(service: MailAgentService) -> None:
    class Broken:
        def analyze(self, message: MailMessage) -> Analysis:
            raise RuntimeError("bug")

    service.analyzer = Broken()
    service.source = MailboxSource([1])
    assert service.import_new().imported == 1
    detail = service.repository.messages.get(service.repository.inbox.messages()[0].id)
    assert detail is not None
    assert detail.analysis.ai_pending
    assert detail.analysis.category is Category.OTHER
    assert "Automatische Auswertung fehlgeschlagen" in detail.analysis.findings[0]


def test_run_log_and_fetch_lock(service: MailAgentService) -> None:
    service.source = MailboxSource([1, 2])
    report = service.run("abruf", "manuell")
    last = service.repository.runs.last("abruf")
    assert last is not None
    assert (last.trigger, last.found, last.imported, last.error) == ("manuell", 2, report.imported, None)

    service.fetch = FetchSettings(enabled=False)
    with pytest.raises(SourceError) as exc:
        service.run("abruf", "zeitplan")
    assert exc.value.code == "fetch_disabled"

    service.fetch = FetchSettings()
    service.source = MailboxSource([3], fail_on_load=1)
    with pytest.raises(SourceError):
        service.run("abruf", "zeitplan")
    failed = service.repository.runs.last("abruf")
    assert failed is not None
    assert failed.error == "imap_connection_lost"


def test_reanalyze_pending_skips_manual_changes(service: MailAgentService) -> None:
    pending_analyzer = RuleBasedAnalyzer(OrganizationSettings())

    class Pending:
        def analyze(self, message: MailMessage) -> Analysis:
            return replace(pending_analyzer.analyze(message), ai_pending=True)

    service.analyzer = Pending()
    service.source = MailboxSource([1, 2, 3])
    service.import_new()
    ids = sorted(s.id for s in service.repository.inbox.messages())
    service.repository.analyses.save_draft(ids[1], "eigener Text")
    assert service.repository.inbox.counts()["ki_ausstehend"] == 3
    assert service.repository.analyses.pending_ai_ids() == [ids[0], ids[2]]

    service.analyzer = pending_analyzer  # the AI is back
    report = service.reanalyze_pending()
    assert (report.found, report.imported, report.ai_pending) == (2, 2, 0)
    assert service.repository.analyses.pending_ai_ids() == []
    assert service.repository.inbox.counts()["ki_ausstehend"] == 1  # the edited one stays marked


def test_reanalyze_reads_attachments_again(service: MailAgentService, settings: Settings) -> None:
    write_samples(settings.eml_dir)
    service.import_new()
    message_id = service.repository.inbox.messages(InboxFilter(category=Category.PHYSICIAN))[0].id
    detail = service.repository.messages.get(message_id)
    assert detail is not None
    assert detail.documents[0].extracted_text == "Verordnung HKP (Testdokument)"
    service.read_attachment_text = False
    service.reanalyze(message_id)
    detail = service.repository.messages.get(message_id)
    assert detail is not None
    assert detail.documents[0].extracted_text is None


def test_mails_of_one_sender_see_how_the_first_was_classified(
    service: MailAgentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, tuple[str, ...]]] = []

    class Recording:
        def analyze(self, message: MailMessage) -> Analysis:
            seen.append((message.subject, message.sender_history))
            return RuleBasedAnalyzer(OrganizationSettings()).analyze(message)

    def mail(n: int, sender: str) -> RawMail:
        return RawMail(
            make_mail(sender=sender, subject=f"Rechnung {n}", message_id=f"<{n}@x>"), f"eml:{n}", str(n)
        )

    service.analyzer = Recording()
    service.parallel = 3
    service.source = ListSource(
        [mail(1, "a@werkstatt.example"), mail(2, "a@werkstatt.example"), mail(3, "b@andere.example")]
    )
    service.import_new()
    history = dict(seen)
    assert history["Rechnung 1"] == ()  # first of its sender
    assert history["Rechnung 3"] == ()
    assert len(history["Rechnung 2"]) == 1  # analysed after the first one was stored
    assert "Priorität" in history["Rechnung 2"][0]
    # re-analysing a message does not count the message itself
    first = next(s.id for s in service.repository.inbox.messages() if s.subject == "Rechnung 1")
    seen.clear()
    service.reanalyze(first)
    assert len(seen[0][1]) == 1


def test_saving_a_known_lawyer_moves_his_mails_to_legal(settings: Settings) -> None:
    from mail_agent.senders import KnownSender
    from mail_agent.service import build_service

    service = build_service(settings)
    service.source = ListSource(
        [RawMail(make_mail(sender="ra@kanzlei.example", subject="Klage", message_id="<k@x>"), "eml:k", "k")]
    )
    service.import_new()
    [summary] = service.repository.inbox.messages()
    assert summary.category is not Category.LEGAL
    ids = service.save_known_sender(KnownSender("Kanzlei", "anwalt", domains=("kanzlei.example",)))
    assert ids == [summary.id]
    assert (settings.data_dir / "bekannte_absender.yaml").is_file()
    service.reanalyze_pending(ids=ids)
    assert service.repository.inbox.messages()[0].category is Category.LEGAL
    assert service.forget_known_sender("kanzlei.example")
    assert service.senders.lookup("ra@kanzlei.example") is None
