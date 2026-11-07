"""Small building blocks of the web interface: texts, list filters, change tracking and security headers."""

from fastapi import Response
from fastapi.testclient import TestClient
from starlette.requests import Request

from mail_agent.jobs import JobRunner
from mail_agent.models import Category, Status
from mail_agent.service import MailAgentService
from mail_agent.web.app import create_app
from mail_agent.web.change_tracking import is_data_change
from mail_agent.web.routes.lists import MAX_FOLLOW_UP_DAYS, follow_up_days
from mail_agent.web.security import FOREIGN_ORIGIN_TEXT, SECURITY_HEADERS
from mail_agent.web.texts import INTERNAL_ERROR, error_text, notice_text
from mail_agent.web.views import ListView
from tests.helpers import BASE_URL


def _request(method: str, path: str) -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": [], "query_string": b""})


def test_notice_text_fills_in_the_number() -> None:
    assert notice_text("werbung_erledigt", 3) == "3 Werbe-Mails als erledigt markiert."
    assert notice_text("gespeichert") == "Entwurf gespeichert."
    assert notice_text("unbekannt") is None
    assert notice_text(None) is None


def test_error_text_points_to_the_log_file_without_a_window() -> None:
    assert error_text(None) is None
    assert error_text("intern:KeyError") == INTERNAL_ERROR
    assert "agent.log" in INTERNAL_ERROR  # the agent runs invisibly, there is no black window any more
    assert error_text("imap_login_failed") == "Anmeldung am Postfach fehlgeschlagen."
    assert error_text("neu") == "Abruf fehlgeschlagen (neu)."


def test_list_view_from_values_with_missing_and_unknown_values() -> None:
    view = ListView.from_values({"kategorie": "werbung", "status": "alle", "q": "  zwei   Worte  "})
    assert view.category is Category.ADVERTISING
    assert view.status is None
    assert view.search == "zwei Worte"
    assert ListView.from_values({}) == ListView()
    assert ListView.from_values({"status": "quatsch"}).status is None
    assert ListView.from_query(ListView(status=Status.DONE, search="x").query()) == ListView(
        status=Status.DONE, search="x"
    )


def test_follow_up_days_falls_back_for_implausible_values() -> None:
    assert follow_up_days("7", 3) == 7
    assert follow_up_days(str(MAX_FOLLOW_UP_DAYS), 3) == MAX_FOLLOW_UP_DAYS
    assert follow_up_days(str(MAX_FOLLOW_UP_DAYS + 1), 3) == 3
    assert follow_up_days("-1", 3) == 3
    assert follow_up_days("", 3) == 3


def test_is_data_change_only_for_successful_posts_outside_the_exceptions() -> None:
    ok, refused = Response(status_code=303), Response(status_code=403)
    assert is_data_change(_request("POST", "/nachrichten/1/status"), ok)
    assert not is_data_change(_request("POST", "/nachrichten/1/status"), refused)
    assert not is_data_change(_request("GET", "/"), ok)
    assert not is_data_change(_request("POST", "/beenden"), ok)
    assert not is_data_change(_request("POST", "/uebergabe/jetzt"), ok)


def test_refused_cross_origin_post_also_carries_the_security_headers(service: MailAgentService) -> None:
    client = TestClient(create_app(service, JobRunner(service, background=False)), base_url=BASE_URL)
    response = client.post("/werbung-erledigt", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert response.text == FOREIGN_ORIGIN_TEXT
    for name in SECURITY_HEADERS:
        assert name in response.headers
