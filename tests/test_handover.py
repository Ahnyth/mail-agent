"""Hand-over between the two PCs: encrypted package, claim, take-over with backup – all offline."""

import base64
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mail_agent import handover as handover_module
from mail_agent.analysis.rules import RuleBasedAnalyzer
from mail_agent.config import FetchSettings, OrganizationSettings, Settings, parse_settings
from mail_agent.filing import DocumentStore
from mail_agent.handover import (
    CLAIM_NAME,
    MAGIC,
    PACKAGE_NAME,
    Handover,
    HandoverError,
    decode_field,
    decrypt_stream,
    derive_key,
    encrypt_stream,
    read_claim,
    read_header,
)
from mail_agent.jobs import JobRunner, Scheduler
from mail_agent.models import Status
from mail_agent.sample_data import write_samples
from mail_agent.service import MailAgentService, ServiceOptions, handover_sync
from mail_agent.sources import SourceError
from mail_agent.sources.eml_folder import EmlFolderSource
from mail_agent.storage import MailRepository
from mail_agent.web.app import create_app
from tests.helpers import BASE_URL

SECRET = "postfach-passwort"


def _pc(root: Path, name: str, *, secret: str = SECRET, fetch: bool = True) -> MailAgentService:
    settings: Settings = parse_settings({"analysis": {"engine": "rules"}}, root)
    return MailAgentService(
        repository=MailRepository(settings.database_path),
        analyzer=RuleBasedAnalyzer(OrganizationSettings()),
        documents=DocumentStore(settings.filing_root),
        source=EmlFolderSource(settings.eml_dir),
        options=ServiceOptions(
            fetch=FetchSettings(enabled=fetch),
            handover=Handover(settings.data_dir, lambda: secret, pc=name),
        ),
    )


def _with_mails(service: MailAgentService, root: Path) -> MailAgentService:
    write_samples(root / "daten" / "eingang")
    service.import_new()
    (root / "daten" / "bekannte_absender.yaml").write_text("absender: []\n", encoding="utf-8")
    return service


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    return tmp_path / "Cloud" / "MailAgent-Uebergabe"


@pytest.fixture
def office(tmp_path: Path, folder: Path) -> MailAgentService:
    service = _with_mails(_pc(tmp_path / "buero", "BUERO"), tmp_path / "buero")
    assert service.handover is not None
    service.handover.configure(folder, may_fetch_now=True)
    return service


def _handover(service: MailAgentService) -> Handover:
    assert service.handover is not None
    return service.handover


# --- encryption -------------------------------------------------------------------------------------------


def _roundtrip(plain: bytes, monkeypatch: pytest.MonkeyPatch) -> tuple[bytes, bytes, bytes, bytes]:
    monkeypatch.setattr(handover_module, "CHUNK_BYTES", 10)  # several chunks
    key, header, prefix = derive_key(SECRET, b"s" * 16), b'{"x":1}', b"n" * 8
    sealed = io.BytesIO()
    encrypt_stream(io.BytesIO(plain), sealed, key, header, prefix)
    return sealed.getvalue(), key, header, prefix


def test_encryption_roundtrip_in_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    plain = b"Mail-Daten " * 9
    sealed, key, header, prefix = _roundtrip(plain, monkeypatch)
    assert plain not in sealed
    restored = io.BytesIO()
    decrypt_stream(io.BytesIO(sealed), restored, key, header, prefix)
    assert restored.getvalue() == plain


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ("key", "handover_wrong_key"),
        ("header", "handover_wrong_key"),
        ("truncate", "handover_damaged"),
        ("append", "handover_damaged"),
        ("flip", "handover_damaged"),
    ],
)
def test_tampering_is_rejected(change: str, code: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sealed, key, header, prefix = _roundtrip(b"Mail-Daten " * 9, monkeypatch)
    if change == "key":
        key = derive_key("anderes-passwort", b"s" * 16)
    elif change == "header":
        header = b'{"x":2}'
    elif change == "truncate":
        sealed = sealed[: len(sealed) // 2]
    elif change == "append":
        sealed += b"x"
    else:
        sealed = sealed[:-5] + bytes([sealed[-5] ^ 1]) + sealed[-4:]
    with pytest.raises(HandoverError) as exc:
        decrypt_stream(io.BytesIO(sealed), io.BytesIO(), key, header, prefix)
    assert exc.value.code == code


class _ReadSpy(io.BytesIO):
    """Remembers the largest single read request."""

    largest = 0

    def read(self, size: int | None = -1, /) -> bytes:
        self.largest = max(self.largest, size or 0)
        return super().read(size)


def test_impossible_chunk_length_is_rejected_before_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    sealed, key, header, prefix = _roundtrip(b"Mail-Daten " * 9, monkeypatch)
    damaged = _ReadSpy(bytes([0xFF] * 4) + sealed[4:])  # the first chunk claims to be 4 GiB long
    with pytest.raises(HandoverError) as exc:
        decrypt_stream(damaged, io.BytesIO(), key, header, prefix)
    assert exc.value.code == "handover_damaged"
    assert damaged.largest < 1024 * 1024  # never tried to read the claimed gigabytes


def test_header_field_that_is_not_base64_counts_as_damaged() -> None:
    assert decode_field(base64.b64encode(b"salz").decode("ascii")) == b"salz"
    with pytest.raises(HandoverError) as exc:
        decode_field("kein base64!")
    assert exc.value.code == "handover_damaged"


# --- the two PCs -------------------------------------------------------------------------------------------


def test_office_writes_package_and_claim(office: MailAgentService, folder: Path) -> None:
    header = office.export_handover()
    assert (header.generation, header.pc, header.messages) == (1, "BUERO", 13)
    raw = (folder / PACKAGE_NAME).read_bytes()
    assert raw.startswith(MAGIC)
    assert b"Mein Vater ist gest" not in raw  # encrypted, also the mail text
    assert read_claim(folder) is not None
    assert json.loads((folder / CLAIM_NAME).read_text(encoding="utf-8"))["pc"] == "BUERO"
    assert not list(folder.glob("*.neu"))  # written under a temporary name, then renamed


def test_other_pc_takes_over_and_office_stops_fetching(
    office: MailAgentService, folder: Path, tmp_path: Path
) -> None:
    office.export_handover()
    home = _pc(tmp_path / "zuhause", "ZUHAUSE", fetch=False)
    _handover(home).configure(folder, may_fetch_now=False)
    assert not home.may_fetch()
    assert _handover(home).remote_newer()

    header = home.take_over()
    assert header.generation == 1
    assert home.may_fetch()
    assert len(home.repository.inbox.messages()) == 13
    assert (tmp_path / "zuhause" / "daten" / "bekannte_absender.yaml").is_file()
    stored = [d.stored_path for m in home.repository.messages.details() for d in m.documents if d.stored_path]
    assert stored
    assert all(home.documents.resolve(path).is_file() for path in stored)  # attachments came along

    assert not office.may_fetch()  # the claim names the other PC
    with pytest.raises(SourceError) as exc:
        office.run("abruf", "manuell")
    assert exc.value.code == "handover_passive"
    with pytest.raises(HandoverError) as refused:
        office.export_handover()
    assert refused.value.code == "handover_not_active"


def test_back_to_the_office_keeps_a_backup(office: MailAgentService, folder: Path, tmp_path: Path) -> None:
    office.export_handover()
    home = _pc(tmp_path / "zuhause", "ZUHAUSE", fetch=False)
    _handover(home).configure(folder, may_fetch_now=False)
    home.take_over()
    first = home.repository.inbox.messages()[0]
    home.repository.workflow.set_status_many([first.id], Status.DONE)
    assert home.export_handover().generation == 2

    assert _handover(office).remote_newer()
    office.take_over()
    assert office.may_fetch()
    assert office.repository.messages.get(first.id) is not None
    assert office.repository.messages.get(first.id).status.value == "erledigt"  # type: ignore[union-attr]
    data_dir = _handover(office).data_dir
    assert list(data_dir.parent.glob(f"{data_dir.name}-sicherung-*"))


def test_taking_back_own_unchanged_package_needs_no_install(office: MailAgentService, folder: Path) -> None:
    office.export_handover()
    handover = _handover(office)
    state = handover.state()
    handover._save(state.__class__(**{**state.__dict__, "active": False}))
    assert not office.may_fetch()
    office.take_over()
    assert office.may_fetch()


def test_wrong_password_leaves_the_data_alone(office: MailAgentService, folder: Path, tmp_path: Path) -> None:
    office.export_handover()
    home = _with_mails(
        _pc(tmp_path / "zuhause", "ZUHAUSE", secret="falsch", fetch=False), tmp_path / "zuhause"
    )
    _handover(home).configure(folder, may_fetch_now=False)
    with pytest.raises(HandoverError) as exc:
        home.take_over()
    assert exc.value.code == "handover_wrong_key"
    assert len(home.repository.inbox.messages()) == 13
    assert not home.may_fetch()


def test_package_of_a_newer_version_is_refused(
    office: MailAgentService, folder: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handover_module, "SCHEMA_VERSION", 999)
    office.export_handover()
    monkeypatch.undo()
    home = _pc(tmp_path / "zuhause", "ZUHAUSE", fetch=False)
    _handover(home).configure(folder, may_fetch_now=False)
    with pytest.raises(HandoverError) as exc:
        home.take_over()
    assert exc.value.code == "handover_update_first"


def test_only_two_backups_are_kept(office: MailAgentService, folder: Path, tmp_path: Path) -> None:
    home = _pc(tmp_path / "zuhause", "ZUHAUSE", fetch=False)
    _handover(home).configure(folder, may_fetch_now=False)
    data_dir = _handover(home).data_dir  # backups lie next to it: <name>-sicherung-<time>
    for stamp in ("20260101-000000", "20260102-000000", "20260103-000000"):
        (data_dir.parent / f"{data_dir.name}-sicherung-{stamp}").mkdir(parents=True)
    office.export_handover()
    home.take_over()
    backups = sorted(p.name for p in data_dir.parent.glob(f"{data_dir.name}-sicherung-*"))
    assert len(backups) == 2
    assert f"{data_dir.name}-sicherung-20260101-000000" not in backups


def test_missing_package_and_unreadable_header(folder: Path) -> None:
    folder.mkdir(parents=True)
    with pytest.raises(HandoverError) as missing:
        read_header(folder / PACKAGE_NAME)
    assert missing.value.code == "handover_no_package"
    (folder / PACKAGE_NAME).write_bytes(b"kein Paket")
    with pytest.raises(HandoverError) as damaged:
        read_header(folder / PACKAGE_NAME)
    assert damaged.value.code == "handover_damaged"


def test_automatic_package_after_changes(
    office: MailAgentService, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    office.export_handover()
    monkeypatch.setattr(handover_sync, "EXPORT_QUIET_SECONDS", 0)
    assert not office.handover_tick()  # nothing changed
    office.note_change()
    assert not office.handover_tick(busy=True)  # never while a fetch runs
    assert office.handover_tick()
    assert read_header(folder / PACKAGE_NAME).generation == 2
    assert not _handover(office).state().changed


def test_stopping_the_agent_writes_pending_changes_at_once(
    office: MailAgentService, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    office.export_handover()
    office.note_change()
    assert not office.handover_tick()  # within the quiet minute nothing happens ...
    runner = JobRunner(office, background=False)
    scheduler = Scheduler(office, runner)
    monkeypatch.setattr(scheduler, "start", lambda: None)
    with TestClient(create_app(office, runner, scheduler), base_url=BASE_URL):
        pass  # ... but stopping the agent (end of the web server) writes the package right away
    assert read_header(folder / PACKAGE_NAME).generation == 2
    assert not _handover(office).state().changed


def test_without_folder_the_toml_decides(tmp_path: Path) -> None:
    service = _pc(tmp_path, "BUERO", fetch=False)
    assert not service.handover_configured
    assert not service.may_fetch()


# --- web page ----------------------------------------------------------------------------------------------


def _client(service: MailAgentService) -> TestClient:
    return TestClient(create_app(service, JobRunner(service, background=False)), base_url=BASE_URL)


def test_page_configures_folder_and_writes_first_package(tmp_path: Path) -> None:
    office = _with_mails(_pc(tmp_path / "buero", "BUERO"), tmp_path / "buero")
    client = _client(office)
    page = client.get("/uebergabe").text
    assert "Noch kein Übergabe-Ordner eingestellt" in page
    folder = tmp_path / "Cloud" / "MailAgent-Uebergabe"
    response = client.post("/uebergabe/ordner", data={"ordner": str(folder)}, follow_redirects=False)
    assert response.headers["location"] == "/uebergabe?hinweis=uebergabe_geschrieben&anzahl=1"
    page = client.get(response.headers["location"]).text
    assert "Dieser PC hat die Daten" in page
    assert (folder / PACKAGE_NAME).is_file()


def test_inbox_offers_take_over_on_the_other_pc(
    office: MailAgentService, folder: Path, tmp_path: Path
) -> None:
    office.export_handover()
    home = _pc(tmp_path / "zuhause", "ZUHAUSE", fetch=False)
    _handover(home).configure(folder, may_fetch_now=False)
    client = _client(home)
    inbox = client.get("/").text
    assert "Am PC „BUERO“ wurde zuletzt gearbeitet" in inbox
    assert "Die Daten hat gerade der andere PC" in inbox
    response = client.post("/uebergabe/uebernehmen", follow_redirects=False)
    assert response.headers["location"] == "/?hinweis=uebergabe_uebernommen&anzahl=1"
    assert len(home.repository.inbox.messages()) == 13


def test_changes_in_the_page_mark_the_data_changed(office: MailAgentService) -> None:
    office.export_handover()
    client = _client(office)
    message = office.repository.inbox.messages()[0]
    client.post(f"/nachrichten/{message.id}/status", data={"status": "erledigt"})
    assert _handover(office).state().changed
