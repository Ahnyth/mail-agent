r"""Starts the agent invisibly in the background (Windows autostart and desktop shortcut).

Started with ``.venv\Scripts\pythonw.exe -m mail_agent.background`` in the project folder there is
no console window. ``pythonw`` has no standard output either, and uvicorn fails without one, so output and
errors go to ``daten/agent.log``. A second start while the agent already runs only notes that in the log, so
the autostart and the desktop shortcut can both use this entry point. The icon next to the clock (``tray.py``)
shows that the agent runs and stops it.
"""

import socket
import sys
from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path

from .cli import EXIT_CONFIG_ERROR, LOCAL_HOST
from .cli import main as cli_main
from .config import ConfigError, load_settings

LOG_FILE = Path("daten") / "agent.log"
"""Relative to the project folder (the shortcut's working directory); per PC, never part of a hand-over."""
MAX_LOG_BYTES = 1_000_000
"""Above about 1 MB the log is moved to ``agent.log.1`` (one old generation), so it never fills the disk."""
CONNECT_TIMEOUT_SECONDS = 0.5
"""A running agent answers on loopback within milliseconds; half a second is plenty (upper bound)."""
CONFIG_FILE = "mail_agent.toml"
"""The configuration file in the project folder."""
WEB_ARGS = ["web", "--taskleiste"]
"""Without a window, the icon next to the clock is the only sign that the agent runs
and the way to stop it."""


def agent_running(port: int) -> bool:
    """Checks whether something already listens on ``127.0.0.1:port`` (the agent binds only there)."""
    try:
        with socket.create_connection((LOCAL_HOST, port), timeout=CONNECT_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def _rotate(log: Path) -> None:
    """Moves a log larger than :data:`MAX_LOG_BYTES` to ``agent.log.1``, replacing the older one."""
    if log.is_file() and log.stat().st_size > MAX_LOG_BYTES:
        log.replace(log.with_name(log.name + ".1"))


def main(argv: Sequence[str] | None = None, project: Path | None = None) -> int:
    """Starts the web interface with the icon next to the clock, writing every output into the log.

    Args:
        argv: The command line for :func:`mail_agent.cli.main`; ``None`` uses :data:`WEB_ARGS`.
        project: The project folder; ``None`` uses the working directory.

    Returns:
        The exit code; 0 also when the agent already runs.
    """
    root = project or Path.cwd()
    log_path = root / LOG_FILE
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _rotate(log_path)
    with (
        log_path.open("a", encoding="utf-8", buffering=1) as log,
        redirect_stdout(log),
        redirect_stderr(log),
    ):
        print(f"--- {datetime.now():%d.%m.%Y %H:%M:%S} Start im Hintergrund ---")
        return _start(root, argv)


def _start(root: Path, argv: Sequence[str] | None) -> int:
    """Starts the agent unless it already runs; the output already goes into the log."""
    config = root / CONFIG_FILE
    try:
        settings = load_settings(config)
    except ConfigError as exc:
        print(f"Konfigurationsfehler: {exc}")
        return EXIT_CONFIG_ERROR
    if agent_running(settings.web_port):
        print(f"Läuft schon auf Port {settings.web_port} – nichts zu tun.")
        return 0
    return cli_main(["--config", str(config), *(argv or WEB_ARGS)])


if __name__ == "__main__":
    sys.exit(main())
