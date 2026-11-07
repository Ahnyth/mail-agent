"""Command line interface.

mail-agent testdaten          writes synthetic test mails into the inbox folder
mail-agent einlesen           imports every message received since the last fetch
mail-agent ki-nachholen       analyses messages again that are still waiting for the AI
mail-agent web                starts the local web interface (http://127.0.0.1:8765)
mail-agent web --taskleiste   the same with the icon next to the clock (background start)
"""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__
from .config import ConfigError, Settings, load_settings
from .jobs import JobRunner
from .sample_data import write_samples
from .security.redaction import configure_logging, get_logger
from .service import ImportReport, build_service
from .service.execution import KIND_AI, KIND_FETCH
from .sources import SourceError

if TYPE_CHECKING:
    import uvicorn

    from .tray import TrayIcon

_log = get_logger("cli")

GRACEFUL_SHUTDOWN_SECONDS = 10
"""When the agent is stopped, open requests get at most this long (every wait has an upper bound)."""
LOCAL_HOST = "127.0.0.1"
"""The web interface listens only on this PC, never on the network."""
EXIT_CONFIG_ERROR = 2
"""Exit code when the configuration file is invalid."""
EXIT_SOURCE_ERROR = 3
"""Exit code when the mailbox or the inbox folder could not be read."""


def _parser() -> argparse.ArgumentParser:
    """Builds the command line parser; commands and help texts are German like the rest of the interface."""
    parser = argparse.ArgumentParser(prog="mail-agent", description="MailAgent (Prototyp)")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", type=Path, help="Konfigurationsdatei (Standard: ./mail_agent.toml)")
    parser.add_argument("-v", "--verbose", action="store_true", help="ausführliche Protokollausgabe")
    commands = parser.add_subparsers(dest="command", required=True)

    samples = commands.add_parser("testdaten", help="künstliche Testmails in den Eingangsordner schreiben")
    samples.add_argument("--ueberschreiben", action="store_true", help="vorhandene Testmails ersetzen")

    commands.add_parser("einlesen", help="alle Nachrichten seit dem letzten Abruf übernehmen")
    commands.add_parser("ki-nachholen", help="Nachrichten, die noch auf die KI warten, neu auswerten")

    web = commands.add_parser("web", help="Weboberfläche starten (nur lokal erreichbar)")
    web.add_argument("--port", type=int, help="Port (Standard aus der Konfiguration, sonst 8765)")
    web.add_argument(
        "--taskleiste", action="store_true", help="Symbol neben der Uhr mit „Öffnen“ und „Beenden“ zeigen"
    )
    return parser


def _cmd_samples(settings: Settings, overwrite: bool) -> int:
    """Writes the synthetic test mails into the inbox folder of the configuration."""
    written = write_samples(settings.eml_dir, overwrite=overwrite)
    print(f"{len(written)} Testmails geschrieben nach {settings.eml_dir}")
    return 0


def _print_progress(phase: str, report: ImportReport) -> None:
    """Prints how many messages are done while they are analysed."""
    if phase == "auswerten" and report.found:
        print(f"  {report.processed} von {report.found} verarbeitet ({report.imported} neu)", flush=True)


def _cmd_import(settings: Settings, kind: str) -> int:
    """Runs a fetch (``KIND_FETCH``) or the repeated AI analysis (``KIND_AI``) and prints the result."""
    service = build_service(settings)
    try:
        report = service.run(kind, "kommandozeile", _print_progress)
    except SourceError as exc:
        print(f"Einlesen fehlgeschlagen: {exc.code}", file=sys.stderr)
        return EXIT_SOURCE_ERROR
    print(f"Einlesen abgeschlossen: {report.text}")
    for failure in report.failed:
        print(f"  nicht lesbar: {failure}", file=sys.stderr)
    return 0


def _cmd_web(settings: Settings, port: int | None, tray: bool = False) -> int:
    """Starts the local web interface and returns once it has stopped.

    Args:
        settings: The configuration.
        port: The port; ``None`` takes the one from the configuration.
        tray: Show the icon next to the clock (background start without a window).

    Returns:
        Always 0.
    """
    # Imported here, so the other commands start fast and never load the web stack or the taskbar icon.
    import uvicorn  # noqa: PLC0415 - the web server is needed only by this command

    from .tray import TrayIcon  # noqa: PLC0415 - the icon (with Pillow) is needed only by this command
    from .web.app import create_app  # noqa: PLC0415 - FastAPI and the templates are needed only here

    service = build_service(settings)
    jobs = JobRunner(service)
    port = port or settings.web_port
    url = f"http://{LOCAL_HOST}:{port}"

    def request_quit() -> None:
        """From the "Beenden" button or the icon: uvicorn stops, then the lifespan stops the jobs."""
        server.should_exit = True

    app = create_app(service, jobs, on_quit=request_quit)
    config = uvicorn.Config(
        app,
        host=LOCAL_HOST,
        port=port,
        log_level="warning",
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )
    server = uvicorn.Server(config)
    icon = TrayIcon(url, request_quit, jobs.status) if tray else None
    _serve(server, icon, url)
    return 0


def _serve(server: "uvicorn.Server", icon: "TrayIcon | None", url: str) -> None:
    """Runs ``server`` until it stops, with the icon next to the clock while it runs."""
    if icon is not None:
        icon.start(ready=lambda: server.started)
    how = "Beenden: Symbol neben der Uhr oder Knopf „Beenden“" if icon is not None else "Beenden mit Strg+C"
    print(f"MailAgent läuft auf {url}  ({how})")
    try:
        server.run()
    finally:
        if icon is not None:
            icon.stop()
    _log.info("MailAgent beendet")


def main(argv: Sequence[str] | None = None) -> int:
    """Runs one command of the command line.

    Args:
        argv: The arguments without the program name; ``None`` uses the real command line.

    Returns:
        The exit code: 0 on success, :data:`EXIT_CONFIG_ERROR` or :data:`EXIT_SOURCE_ERROR` on errors.
    """
    args = _parser().parse_args(argv)
    configure_logging(logging.DEBUG if args.verbose else logging.INFO)
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    if args.command == "testdaten":
        return _cmd_samples(settings, args.ueberschreiben)
    if args.command == "einlesen":
        return _cmd_import(settings, KIND_FETCH)
    if args.command == "ki-nachholen":
        return _cmd_import(settings, KIND_AI)
    return _cmd_web(settings, args.port, args.taskleiste)


if __name__ == "__main__":
    sys.exit(main())
