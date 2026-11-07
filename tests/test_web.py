import threading
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from mail_agent.analysis.rules import RuleBasedAnalyzer
from mail_agent.config import FetchSettings, OrganizationSettings, Settings
from mail_agent.eval.cases import parse_case
from mail_agent.jobs import JobRunner
from mail_agent.models import Category, Status
from mail_agent.sample_data import write_samples
from mail_agent.service import ImportReport, MailAgentService
from mail_agent.storage import InboxFilter
from mail_agent.web.app import create_app
from tests.helpers import BASE_URL, make_mail


@pytest.fixture
def client(service: MailAgentService) -> TestClient:
    # jobs run inline, so a test sees the result right after the request
    return TestClient(create_app(service, JobRunner(service, background=False)), base_url=BASE_URL)


@pytest.fixture
def imported(service: MailAgentService, settings: Settings) -> MailAgentService:
    write_samples(settings.eml_dir)
    service.import_new()
    return service


def _id(service: MailAgentService, category: Category) -> int:
    return service.repository.inbox.messages(InboxFilter(category=category))[0].id


def test_empty_overview(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Keine Nachrichten" in response.text
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_import_button_and_overview(client: TestClient, settings: Settings) -> None:
    write_samples(settings.eml_dir)
    response = client.post("/einlesen", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/?hinweis=abruf_gestartet"
    page = client.get(response.headers["location"]).text
    assert "Fertig: 13 neu eingelesen, 0 bereits vorhanden" in page
    assert "Mein Vater ist gestürzt" in page
    assert "Letzter Abruf:" in page
    assert "(per Knopf), 13 neu" in page
    status = client.get("/abruf/status").json()
    assert (status["running"], status["phase"], status["total"], status["imported"]) == (
        False,
        "fertig",
        13,
        13,
    )


def test_import_error_is_shown(client: TestClient) -> None:
    response = client.post("/einlesen")  # inbox folder does not exist
    assert "Eingangsordner" in response.text
    assert client.get("/abruf/status").json()["error"] == "eml_folder_missing"


def test_running_job_shows_progress_and_blocks_a_second_start(service: MailAgentService) -> None:
    runner = JobRunner(service)
    client = TestClient(create_app(service, runner), base_url=BASE_URL)
    started = threading.Event()
    release = threading.Event()

    def slow_run(kind: str, trigger: str, progress, stop, ids=None):  # type: ignore[no-untyped-def]
        report = ImportReport(found=40, imported=7)
        progress("auswerten", report)
        started.set()
        release.wait(5)
        return report

    service.run = slow_run  # type: ignore[method-assign,assignment]
    client.post("/einlesen")
    assert started.wait(5)
    page = client.get("/").text
    assert 'data-running="1"' in page
    assert 'value="7"' in page
    assert "7 von 40" in page
    assert "/?hinweis=abruf_laeuft" in client.post("/einlesen", follow_redirects=False).headers["location"]
    assert client.get("/abruf/status").json()["running"]
    release.set()
    runner.wait()
    assert not client.get("/abruf/status").json()["running"]


def test_fetch_switched_off_on_this_pc(client: TestClient, service: MailAgentService) -> None:
    service.fetch = FetchSettings(enabled=False)
    response = client.post("/einlesen", follow_redirects=False)
    assert response.headers["location"] == "/?hinweis=abruf_aus"
    page = client.get("/").text
    assert "Abruf auf diesem PC ausgeschaltet" in page
    assert "disabled" in page


def test_ai_pending_banner_filter_and_catch_up(client: TestClient, imported: MailAgentService) -> None:
    first = imported.repository.inbox.messages()[0]
    detail = imported.repository.messages.get(first.id)
    assert detail is not None
    imported.repository.analyses.replace(first.id, replace(detail.analysis, ai_pending=True))
    imported.analyzer = PendingAware()  # pretend the AI is configured
    page = client.get("/").text
    assert "ohne KI eingeordnet" in page
    assert "KI-Auswertung nachholen" in page
    filtered = client.get("/?status=offen&ki=ausstehend").text
    assert filtered.count('<li class="item') == 1
    assert "KI ausstehend" in filtered
    response = client.post("/ki-nachholen", follow_redirects=False)
    assert response.headers["location"] == "/?hinweis=ki_gestartet"
    assert imported.repository.inbox.counts()["ki_ausstehend"] == 0
    assert "ohne KI eingeordnet" not in client.get("/").text


class PendingAware:
    """Analyses with the rules and counts as an AI analyzer for the page."""

    def analyze(self, message):
        return RuleBasedAnalyzer(OrganizationSettings()).analyze(message)


def test_filters(client: TestClient, imported: MailAgentService) -> None:
    page = client.get("/?status=offen&kategorie=bewerbung").text
    assert "Bewerbung als Pflegefachkraft" in page
    assert "Rechnung 2026-1187" not in page
    assert client.get("/?kategorie=unsinn&status=unsinn").status_code == 200


def test_detail_escapes_mail_content(client: TestClient, imported: MailAgentService) -> None:
    newsletter = _id(imported, Category.ADVERTISING)
    page = client.get(f"/nachrichten/{newsletter}").text
    assert "<script>trackOpen" not in page


def test_detail_shows_draft_documents_and_warnings(client: TestClient, imported: MailAgentService) -> None:
    page = client.get(f"/nachrichten/{_id(imported, Category.APPLICATION)}").text
    assert "Antwortentwurf" in page
    assert "Lebenslauf_Hoffmann.pdf" in page
    phishing = next(m for m in imported.repository.inbox.messages() if "Paket" in m.subject)
    page = client.get(f"/nachrichten/{phishing.id}").text
    assert "nicht abgelegt" in page
    assert "Betrugsversuch" in page


def test_save_draft_status_and_reanalyze(client: TestClient, imported: MailAgentService) -> None:
    message_id = _id(imported, Category.COMPLAINT)
    response = client.post(f"/nachrichten/{message_id}/entwurf", data={"entwurf": "Neuer\r\nText"})
    assert "Entwurf gespeichert" in response.text
    detail = imported.repository.messages.get(message_id)
    assert detail is not None
    assert detail.analysis.reply_body == "Neuer\nText"

    client.post(f"/nachrichten/{message_id}/status", data={"status": "erledigt"})
    detail = imported.repository.messages.get(message_id)
    assert detail is not None
    assert detail.status is Status.DONE
    assert client.post(f"/nachrichten/{message_id}/status", data={"status": "weg"}).status_code == 400

    assert "neu analysiert" in client.post(f"/nachrichten/{message_id}/neu-analysieren").text


def test_document_download(client: TestClient, imported: MailAgentService) -> None:
    detail = imported.repository.messages.get(_id(imported, Category.PHYSICIAN))
    assert detail is not None
    response = client.get(f"/dokumente/{detail.documents[0].id}")
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
    assert response.headers["content-type"] == "application/pdf"
    # the PDF viewer (a plugin) is allowed, scripts of this origin are not
    policy = response.headers["content-security-policy"]
    assert "object-src 'self'" in policy
    assert "default-src 'none'" in policy


def test_rejected_document_is_not_downloadable(client: TestClient, imported: MailAgentService) -> None:
    phishing = next(m for m in imported.repository.inbox.messages() if "Paket" in m.subject)
    detail = imported.repository.messages.get(phishing.id)
    assert detail is not None
    assert client.get(f"/dokumente/{detail.documents[0].id}").status_code == 404


def test_unknown_ids_return_404(client: TestClient) -> None:
    assert client.get("/nachrichten/999").status_code == 404
    assert client.get("/dokumente/999").status_code == 404
    assert client.post("/nachrichten/999/entwurf", data={"entwurf": "x"}).status_code == 404
    assert client.post("/nachrichten/999/neu-analysieren").status_code == 404


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"},
        {"Sec-Fetch-Site": "same-site"},
    ],
)
def test_cross_origin_post_is_rejected(client: TestClient, headers: dict[str, str]) -> None:
    assert client.post("/einlesen", headers=headers).status_code == 403


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "same-origin", "Origin": "null"},  # what a real browser sends
        {"Origin": BASE_URL},
        {},
    ],
)
def test_same_origin_post_is_accepted(client: TestClient, headers: dict[str, str]) -> None:
    assert client.post("/einlesen", headers=headers, follow_redirects=False).status_code == 303


def test_foreign_host_header_is_rejected(client: TestClient) -> None:
    assert client.get("/", headers={"Host": "attacker.example"}).status_code == 400


def test_correction_and_export_as_case(
    client: TestClient, imported: MailAgentService, tmp_path: Path
) -> None:
    imported.new_cases_dir = tmp_path / "neu"
    message_id = _id(imported, Category.COMPLAINT)
    assert client.post(f"/nachrichten/{message_id}/testfall").status_code == 404  # no correction yet
    response = client.post(
        f"/nachrichten/{message_id}/korrektur",
        data={
            "kategorie": "termin",
            "prioritaet": "normal",
            "entwurf": "nein",
            "zustaendig": "pdl",
            "notiz": "x",
        },
    )
    assert "Korrektur gespeichert" in response.text
    assert "korrigiert" in response.text
    detail = imported.repository.messages.get(message_id)
    assert detail is not None
    assert detail.analysis.category is Category.APPOINTMENT

    response = client.post(f"/nachrichten/{message_id}/testfall")
    assert "Anonymisierter Testfall" in response.text
    files = list((tmp_path / "neu").glob("*.yaml"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "Wagner" not in text  # sender name removed
    case = parse_case(yaml.safe_load(text), files[0].name)
    assert case.expected.category is Category.APPOINTMENT
    assert not case.expected.reply_needed


def test_invalid_correction_is_rejected(client: TestClient, imported: MailAgentService) -> None:
    message_id = _id(imported, Category.COMPLAINT)
    data = {"kategorie": "gibtsnicht", "prioritaet": "normal", "entwurf": "nein"}
    assert client.post(f"/nachrichten/{message_id}/korrektur", data=data).status_code == 400
    data = {"kategorie": "termin", "prioritaet": "normal", "entwurf": "nein"}
    assert client.post("/nachrichten/999/korrektur", data=data).status_code == 404


def test_detail_shows_responsible_deadlines_and_correction_form(
    client: TestClient, imported: MailAgentService
) -> None:
    page = client.get(f"/nachrichten/{_id(imported, Category.INSURANCE)}").text
    assert "Zuständig: Pflegedienstleitung" in page
    assert "Falsch eingeordnet?" in page
    assert "15.11.2026" in page  # deadline from the sample mail


@pytest.mark.parametrize(
    "path", ["/briefing", "/fristen", "/rechnungen", "/bewerbungen", "/wiedervorlage?tage=0"]
)
def test_new_pages_render(client: TestClient, imported: MailAgentService, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert 'aria-current="page"' in response.text


def test_briefing_page_reads_aloud_without_body(client: TestClient, imported: MailAgentService) -> None:
    page = client.get("/briefing").text
    assert "Morgen-Briefing" in page
    assert 'data-speak="Guten Morgen' in page  # spoken by the Windows voice via /vorlesen
    assert "Piper" not in page
    assert client.get("/briefing/audio").status_code == 404  # the template has no Piper voice


def test_deadline_exports(client: TestClient, imported: MailAgentService) -> None:
    page = client.get("/fristen").text
    assert "15.11.2026" in page
    message_id = _id(imported, Category.INSURANCE)
    response = client.get(f"/nachrichten/{message_id}/fristen.ics")
    assert response.headers["content-type"].startswith("text/calendar")
    assert "attachment" in response.headers["content-disposition"]
    assert "DTSTART;VALUE=DATE:20261115" in response.text
    assert client.get(f"/nachrichten/{_id(imported, Category.COMPLAINT)}/fristen.ics").status_code == 404


def test_invoice_csv(client: TestClient, imported: MailAgentService) -> None:
    assert "2026-1187" in client.get("/rechnungen").text
    response = client.get("/rechnungen.csv")
    assert response.headers["content-type"].startswith("text/csv")
    assert response.content.startswith("\ufeff".encode())
    assert "2026-1187" in response.content.decode("utf-8")


def test_applications_page_shows_checklist(client: TestClient, imported: MailAgentService) -> None:
    page = client.get("/bewerbungen").text
    assert "Lebenslauf" in page
    assert "Miriam Hoffmann" in page


def test_answered_button_and_follow_up(client: TestClient, imported: MailAgentService) -> None:
    message_id = _id(imported, Category.COMPLAINT)
    assert "Thomas Wagner" in client.get("/wiedervorlage?tage=0").text
    response = client.post(f"/nachrichten/{message_id}/beantwortet", data={"beantwortet": "ja"})
    assert "Als beantwortet markiert" in response.text
    assert "Thomas Wagner" not in client.get("/wiedervorlage?tage=0").text
    client.post(f"/nachrichten/{message_id}/beantwortet", data={"beantwortet": "nein"})
    assert "Thomas Wagner" in client.get("/wiedervorlage?tage=0").text
    assert client.post(f"/nachrichten/{message_id}/beantwortet", data={"beantwortet": "x"}).status_code == 400


def test_responsible_filter(client: TestClient, imported: MailAgentService) -> None:
    page = client.get("/?zustaendig=personal").text
    assert "Bewerbung als Pflegefachkraft" in page
    assert "Beschwerde" not in page.split('class="inbox"')[1]


def test_detail_shows_attachment_text_and_read_aloud(client: TestClient, imported: MailAgentService) -> None:
    page = client.get(f"/nachrichten/{_id(imported, Category.APPLICATION)}").text
    assert "Text gelesen" in page
    assert "Vorlesen" in page


# --- search, "erledigt -> next", bulk advertising, known senders, mail program, read aloud ------


def test_search_finds_words_in_any_case_and_keeps_the_list(
    client: TestClient, imported: MailAgentService
) -> None:
    page = client.get("/?status=alle&q=GESTÜRZT").text  # umlaut, other case
    assert "Mein Vater ist gestürzt" in page
    assert "Treffer für „GESTÜRZT“" in page
    assert page.count('<li class="item') == 1
    assert "?von=status%3Dalle%26q%3DGEST%25C3%259CRZT" in page  # the detail link keeps the list
    assert client.get("/?q=gibtsnicht").text.count('<li class="item') == 0


def test_done_jumps_to_the_next_open_mail_of_the_same_list(
    client: TestClient, imported: MailAgentService
) -> None:
    listing = [s.id for s in imported.repository.inbox.messages(InboxFilter(status=Status.OPEN))]
    response = client.post(
        f"/nachrichten/{listing[0]}/status",
        data={"status": "erledigt", "von": "status=offen"},
        follow_redirects=False,
    )
    assert response.headers["location"].startswith(f"/nachrichten/{listing[1]}?hinweis=erledigt_weiter")
    page = client.get(response.headers["location"]).text
    assert "nächste offene Nachricht" in page
    assert 'href="/?status=offen"' in page  # "Zurück zur Liste" keeps the filter
    # the last open one leads back to the (now empty) list
    for message_id in listing[1:-1]:
        imported.repository.workflow.set_status(message_id, Status.DONE)
    last = client.post(
        f"/nachrichten/{listing[-1]}/status", data={"status": "erledigt"}, follow_redirects=False
    )
    assert last.headers["location"] == "/?status=offen&hinweis=alle_erledigt"


def test_all_advertising_done(client: TestClient, imported: MailAgentService) -> None:
    advertising = len(imported.repository.workflow.open_ids(Category.ADVERTISING))
    assert advertising
    assert f"Alle Werbung erledigen ({advertising})" in client.get("/").text
    response = client.post("/werbung-erledigt", follow_redirects=False)
    assert response.headers["location"] == f"/?hinweis=werbung_erledigt&anzahl={advertising}"
    assert f"{advertising} Werbe-Mails als erledigt markiert" in client.get(response.headers["location"]).text
    assert imported.repository.workflow.open_ids(Category.ADVERTISING) == []


@pytest.fixture
def full_client(settings: Settings) -> tuple[TestClient, MailAgentService]:
    """With settings, so senders can be saved (like the real app)."""
    from mail_agent.service import build_service

    service = build_service(settings)
    return TestClient(create_app(service, JobRunner(service, background=False)), base_url=BASE_URL), service


def test_save_sender_as_known_lawyer_from_the_mail(
    full_client: tuple[TestClient, MailAgentService], settings: Settings
) -> None:
    client, service = full_client
    settings.eml_dir.mkdir(parents=True)
    for n in (1, 2):
        (settings.eml_dir / f"k{n}.eml").write_bytes(
            make_mail(
                sender="RA Muster <ra@kanzlei-muster.example>",
                subject=f"Verfahren {n}",
                message_id=f"<k{n}@x>",
            )
        )
    service.import_new()
    first = service.repository.inbox.messages()[0].id
    page = client.get(f"/nachrichten/{first}").text
    assert "Absender als bekannt hinterlegen" in page
    assert "alle Adressen von <strong>@kanzlei-muster.example</strong>" in page
    response = client.post(
        f"/nachrichten/{first}/absender",
        data={"name": "Kanzlei Muster", "typ": "anwalt", "umfang": "domain"},
        follow_redirects=False,
    )
    assert "hinweis=absender_gespeichert" in response.headers["location"]
    assert {s.category for s in service.repository.inbox.messages()} == {Category.LEGAL}  # both re-analysed
    page = client.get(f"/nachrichten/{first}").text
    assert "Bekannter Absender: Kanzlei Muster · Anwalt / Kanzlei" in page
    assert "Recht / Anwalt" in page
    response = client.post(
        f"/nachrichten/{first}/absender-entfernen",
        data={"kennung": "kanzlei-muster.example"},
        follow_redirects=False,
    )
    assert "hinweis=absender_entfernt" in response.headers["location"]
    assert Category.LEGAL not in {s.category for s in service.repository.inbox.messages()}


def test_sender_form_refuses_bad_iban_and_freemail_domains(
    full_client: tuple[TestClient, MailAgentService], settings: Settings
) -> None:
    client, service = full_client
    settings.eml_dir.mkdir(parents=True)
    (settings.eml_dir / "g.eml").write_bytes(make_mail(sender="Max <max@gmail.com>", message_id="<g@x>"))
    service.import_new()
    message_id = service.repository.inbox.messages()[0].id
    assert (
        "nur für die Adresse <strong>max@gmail.com</strong>" in client.get(f"/nachrichten/{message_id}").text
    )
    bad = client.post(
        f"/nachrichten/{message_id}/absender",
        data={"name": "Max", "typ": "sonstige", "iban": "DE00 1234"},
        follow_redirects=False,
    )
    assert "hinweis=iban_ungueltig" in bad.headers["location"]
    client.post(
        f"/nachrichten/{message_id}/absender", data={"name": "Max", "typ": "sonstige", "umfang": "domain"}
    )
    saved = service.senders.lookup("max@gmail.com")
    assert saved is not None
    assert saved.domains == ()  # never all of gmail.com
    assert service.senders.lookup("jemand@gmail.com") is None


def test_correction_for_all_mails_of_the_sender(
    client: TestClient, service: MailAgentService, settings: Settings
) -> None:
    settings.eml_dir.mkdir(parents=True)
    for n in (1, 2, 3):
        (settings.eml_dir / f"r{n}.eml").write_bytes(
            make_mail(sender="werkstatt@autohaus.example", subject=f"Rechnung RE{n}", message_id=f"<r{n}@x>")
        )
    service.import_new()
    first = service.repository.inbox.messages()[0].id
    assert (
        "auch für die 2 weitere(n) offene(n) Mail(s) dieses Absenders"
        in client.get(f"/nachrichten/{first}").text
    )
    response = client.post(
        f"/nachrichten/{first}/korrektur",
        data={"kategorie": "abrechnung", "prioritaet": "normal", "entwurf": "nein", "alle_absender": "ja"},
        follow_redirects=False,
    )
    assert "hinweis=korrigiert_absender&von=status%3Doffen&anzahl=2" in response.headers["location"]
    assert {s.category for s in service.repository.inbox.messages()} == {Category.BILLING}
    assert len(service.repository.corrections.all()) == 3


def test_mail_program_button_and_read_aloud_bar(client: TestClient, imported: MailAgentService) -> None:
    details = imported.repository.messages.details()
    with_draft = next(d.id for d in details if d.analysis.reply_needed)
    page = client.get(f"/nachrichten/{with_draft}").text
    assert "data-mail-open" in page
    assert "In Outlook öffnen" in page
    assert 'id="speech-bar"' in page
    assert 'id="speech-rate"' in page
    without = next(d.id for d in details if not d.analysis.reply_needed)
    assert "Trotzdem antworten" in client.get(f"/nachrichten/{without}").text


def test_advertising_is_hidden_by_default(client: TestClient, imported: MailAgentService) -> None:
    newsletter = "Newsletter November"
    page = client.get("/").text
    assert newsletter not in page
    assert "1 Werbung ausgeblendet" in page
    shown = client.get("/?status=offen&werbung=zeigen").text
    assert newsletter in shown
    assert "wieder ausblenden" in shown
    # a category filter or a search always shows what matches – misfiled mails stay findable
    assert newsletter in client.get("/?status=offen&kategorie=werbung").text
    assert newsletter in client.get("/?status=offen&q=Webinare").text


def test_next_mail_skips_hidden_advertising(imported: MailAgentService) -> None:
    from mail_agent.web.views import ListView

    view = ListView.from_query("status=offen")
    categories = {m.category for m in view.messages(imported.repository)}
    assert Category.ADVERTISING not in categories
    shown = ListView.from_query("status=offen&werbung=zeigen")
    assert Category.ADVERTISING in {m.category for m in shown.messages(imported.repository)}
    assert shown.query().endswith("werbung=zeigen")
