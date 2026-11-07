"""Invisible start in the background (autostart, desktop shortcut) and console-free helper programs."""

import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from mail_agent import background
from mail_agent.background import LOG_FILE, MAX_LOG_BYTES, agent_running
from mail_agent.processes import NO_WINDOW


@pytest.fixture
def listener() -> Iterator[int]:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        yield server.getsockname()[1]


def _project(tmp_path: Path, port: int) -> Path:
    config = f'[web]\nport = {port}\n[analysis]\nengine = "rules"\n'
    (tmp_path / "mail_agent.toml").write_text(config, encoding="utf-8")
    return tmp_path


def test_agent_running_detects_a_listener(listener: int) -> None:
    assert agent_running(listener)


def test_agent_running_false_without_listener() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    assert not agent_running(free_port)


def test_second_start_does_nothing(tmp_path: Path, listener: int) -> None:
    stdout = sys.stdout
    assert background.main(project=_project(tmp_path, listener)) == 0
    assert sys.stdout is stdout  # restored after writing to the log
    log = (tmp_path / LOG_FILE).read_text(encoding="utf-8")
    assert "Start im Hintergrund" in log
    assert f"Läuft schon auf Port {listener}" in log


def test_config_error_goes_to_the_log(tmp_path: Path) -> None:
    (tmp_path / "mail_agent.toml").write_text("[unbekannt]\n", encoding="utf-8")
    assert background.main(project=tmp_path) == 2
    assert "Konfigurationsfehler" in (tmp_path / LOG_FILE).read_text(encoding="utf-8")


def test_large_log_is_rotated(tmp_path: Path, listener: int) -> None:
    log = tmp_path / LOG_FILE
    log.parent.mkdir(parents=True)
    log.write_bytes(b"x" * (MAX_LOG_BYTES + 1))
    background.main(project=_project(tmp_path, listener))
    assert log.with_name("agent.log.1").stat().st_size == MAX_LOG_BYTES + 1
    assert log.stat().st_size < 1000


def test_helper_programs_open_no_console_window() -> None:
    if sys.platform == "win32":
        assert NO_WINDOW == {"creationflags": subprocess.CREATE_NO_WINDOW}
    else:
        assert NO_WINDOW == {}


def test_start_script_waits_with_a_cap() -> None:
    script = (Path(__file__).resolve().parents[1] / "scripts" / "starten.cmd").read_text(encoding="ascii")
    assert 'pythonw.exe" -m mail_agent.background' in script
    assert "for /l %%i in (1,1,30)" in script
