"""Stopping the invisible agent: icon next to the clock, "Beenden" button, clean shutdown."""

import socket
import threading
from datetime import datetime
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient

from mail_agent import cli, tray
from mail_agent.background import WEB_ARGS
from mail_agent.config import Settings
from mail_agent.jobs import JobRunner, JobStatus, running_text
from mail_agent.service import MailAgentService
from mail_agent.service.execution import KIND_FETCH
from mail_agent.tray import BRAND, TrayIcon, icon_image, quit_question
from mail_agent.web.app import create_app
from mail_agent.web.routes import shutdown as app_module
from tests.helpers import BASE_URL

URL = "http://127.0.0.1:8765"


def _status(running: bool = True, total: int = 40, done: int = 12) -> JobStatus:
    return JobStatus(
        KIND_FETCH,
        "manuell",
        running=running,
        phase="auswerten",
        started_at=datetime.now(),
        total=total,
        done=done,
    )


class _BusyRunner(JobRunner):
    """A fetch that is running right now."""

    def status(self) -> JobStatus | None:
        return _status()


# --- what is running ---------------------------------------------------------------------------------------


def test_running_text() -> None:
    assert running_text(None) is None
    assert running_text(_status(running=False)) is None
    assert running_text(_status()) == "Abruf (12 von 40)"
    assert running_text(_status(total=0)) == "Abruf"


def test_quit_question_only_while_a_job_runs() -> None:
    assert quit_question(None) is None
    question = quit_question(_status())
    assert question is not None
    assert "Gerade läuft: Abruf (12 von 40)" in question
    assert "Trotzdem beenden?" in question


# --- the icon ----------------------------------------------------------------------------------------------


def test_icon_image_is_the_violet_m_logo() -> None:
    image = icon_image(64)
    assert image.size == (64, 64)
    assert image.mode == "RGBA"

    def at(x: int, y: int) -> tuple[int, ...]:
        return cast(tuple[int, ...], image.getpixel((x, y)))

    assert at(0, 0)[3] == 0  # rounded corner stays transparent
    assert at(32, 2)[:3] == BRAND
    assert any(at(x, y)[:3] == (255, 255, 255) for x in range(16, 48) for y in range(16, 48))


def _icon(job: JobStatus | None, answer: bool = True) -> tuple[TrayIcon, list[str], list[str], list[str]]:
    quits: list[str] = []
    questions: list[str] = []
    opened: list[str] = []

    def ask(question: str) -> bool:
        questions.append(question)
        return answer

    icon = TrayIcon(URL, lambda: quits.append("quit"), lambda: job, ask=ask, open_url=opened.append)
    return icon, quits, questions, opened


def test_quit_without_a_job_stops_at_once() -> None:
    icon, quits, questions, _ = _icon(None)
    icon.quit()
    assert quits == ["quit"]
    assert questions == []


def test_quit_during_a_fetch_asks_first() -> None:
    icon, quits, questions, _ = _icon(_status(), answer=False)
    icon.quit()
    assert quits == []  # "Nein": keeps running
    assert "Abruf (12 von 40)" in questions[0]
    icon, quits, _, _ = _icon(_status(), answer=True)
    icon.quit()
    assert quits == ["quit"]


def test_open_shows_the_web_interface() -> None:
    icon, _, _, opened = _icon(None)
    icon.open()
    assert opened == [URL]


def test_stop_before_the_server_answers_never_shows_an_icon() -> None:
    icon, _, _, _ = _icon(None)
    icon.start(ready=lambda: False)
    icon.stop()  # ends the waiting thread right away; pystray is never touched
    assert icon._thread is not None
    assert not icon._thread.is_alive()
    assert icon._icon is None


def test_message_box_only_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mail_agent.tray.sys.platform", "linux")
    assert tray.ask_yes_no("Beenden?")


def test_background_start_shows_the_icon() -> None:
    assert WEB_ARGS == ["web", "--taskleiste"]
    assert cli._parser().parse_args(WEB_ARGS).taskleiste
    assert not cli._parser().parse_args(["web"]).taskleiste


# --- "Beenden" in the web interface ------------------------------------------------------------------------


def _client(service: MailAgentService, on_quit: object = None, runner: JobRunner | None = None) -> TestClient:
    jobs = runner or JobRunner(service, background=False)
    return TestClient(create_app(service, jobs, on_quit=on_quit), base_url=BASE_URL)  # type: ignore[arg-type]


def test_no_button_without_a_way_to_stop(service: MailAgentService) -> None:
    client = _client(service)
    assert 'action="/beenden"' not in client.get("/").text
    assert client.post("/beenden").status_code == 404


def test_button_stops_the_agent(service: MailAgentService, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "QUIT_DELAY_SECONDS", 0)
    stopped = threading.Event()
    client = _client(service, on_quit=stopped.set)
    page = client.get("/").text
    assert 'action="/beenden"' in page
    assert "Solange er aus ist, holt er keine Mails ab" in page  # the confirmation question
    response = client.post("/beenden")
    assert response.status_code == 200
    assert "Der MailAgent ist beendet" in response.text
    assert "Doppelklick auf „MailAgent“" in response.text
    assert stopped.wait(2)


def test_button_during_a_fetch_asks_first(service: MailAgentService, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "QUIT_DELAY_SECONDS", 0)
    stopped = threading.Event()
    client = _client(service, on_quit=stopped.set, runner=_BusyRunner(service, background=False))
    page = client.post("/beenden").text
    assert "Gerade läuft: Abruf (12 von 40)" in page
    assert "Trotzdem beenden" in page
    assert not stopped.wait(0.2)
    assert "Der MailAgent ist beendet" in client.post("/beenden", data={"trotzdem": "1"}).text
    assert stopped.wait(2)


def test_web_server_really_stops(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end: the real server on loopback, "Beenden" posted like einrichten.ps1 does, the server ends."""
    monkeypatch.setattr(app_module, "QUIT_DELAY_SECONDS", 0.05)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    result: list[int] = []
    server = threading.Thread(target=lambda: result.append(cli._cmd_web(settings, port)), daemon=True)
    server.start()
    for _ in range(200):  # at most 10 seconds until it answers
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.05).close()
            break
        except OSError:
            server.join(0.05)
    response = httpx.post(f"http://127.0.0.1:{port}/beenden", data={"trotzdem": "1"}, timeout=10)
    assert "Der MailAgent ist beendet" in response.text
    server.join(20)
    assert not server.is_alive()
    assert result == [0]
