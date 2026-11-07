"""Evaluates the analysis on the test collection.

uv run python -m mail_agent.eval [--modus regeln|ki|kombiniert] [--parallel 3] [--nur ID-TEIL]

The cases are fictitious; with ``ki`` or ``kombiniert`` they are sent to the configured AI.
"""

import argparse
import io
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from ..config import MAX_PARALLEL, ConfigError, Settings, load_settings
from ..security.redaction import configure_logging
from ..service import build_analyzer
from .cases import EvalCase, EvalCaseError, load_cases
from .runner import MS_PER_SECOND, CaseResult, EvalReport, format_report, run

MODES = ("regeln", "ki", "kombiniert")
"""Rules only, every mail to the AI, or rules first and the AI for the rest (as in production)."""
DEFAULT_CASES = Path("tests/eval/faelle")
"""The checked-in collection of fictitious cases."""
DEFAULT_SENDERS = Path("tests/eval/bekannte_absender.yaml")
"""The fictitious list of known senders the cases are written for."""
DEFAULT_REPORTS = Path("daten/eval-berichte")
"""JSON reports go into the data folder, which never goes into Git."""
EXIT_BELOW_MINIMUM = 1
"""Exit code when the category hit rate is below ``--mindestens``."""
EXIT_ERROR = 2
"""Exit code when the configuration or the cases are unusable."""


@dataclass(frozen=True)
class _RunSetup:
    """The settings of one evaluation run.

    Attributes:
        mode: One of :data:`MODES`.
        settings: The configuration with the analysis adjusted to the mode.
        parallel: How many cases are analysed at the same time.
        engine: The engine as shown in the console and the report.
    """

    mode: str
    settings: Settings
    parallel: int
    engine: str


def _parser() -> argparse.ArgumentParser:
    """Builds the command line parser; options and help texts are German like the rest of the interface."""
    parser = argparse.ArgumentParser(
        prog="python -m mail_agent.eval", description="Testsammlung auswerten (nur erfundene Mails)"
    )
    parser.add_argument("--config", type=Path, help="Konfigurationsdatei (Standard: ./mail_agent.toml)")
    parser.add_argument(
        "--modus",
        choices=MODES,
        default="kombiniert",
        help="regeln = nur Regeln; ki = jede Mail an die KI; kombiniert = erst Regeln, dann KI (Standard)",
    )
    parser.add_argument("--faelle", type=Path, default=DEFAULT_CASES, help="Ordner mit den Fällen")
    parser.add_argument(
        "--absender", type=Path, default=DEFAULT_SENDERS, help="Absenderliste für die Auswertung"
    )
    parser.add_argument("--parallel", type=int, help=f"gleichzeitige Analysen (1–{MAX_PARALLEL})")
    parser.add_argument("--nur", help="nur Fälle, deren ID diesen Text enthält")
    parser.add_argument(
        "--mindestens", type=float, help="Fehlercode 1, wenn die Kategorie-Quote darunter liegt"
    )
    parser.add_argument("--bericht", type=Path, default=DEFAULT_REPORTS, help="Ordner für JSON-Berichte")
    return parser


def _run_setup(settings: Settings, args: argparse.Namespace) -> _RunSetup | None:
    """Adjusts the analysis settings to the chosen mode; ``None`` if an AI mode has no AI configured."""
    analysis = replace(settings.analysis, known_senders=args.absender if args.absender.is_file() else None)
    if args.modus == "regeln":
        analysis = replace(analysis, engine="rules")
    elif analysis.engine == "rules":
        return None
    engine = "Regeln" if args.modus == "regeln" else f"{analysis.engine} ({analysis.model_classify})"
    return _RunSetup(
        mode=args.modus,
        settings=replace(settings, analysis=analysis),
        parallel=min(MAX_PARALLEL, max(1, args.parallel or analysis.parallel)),
        engine=engine,
    )


def _print_progress(result: CaseResult) -> None:
    """Prints one line per finished case: check mark, id, duration and engine."""
    mark = "✓" if not result.mismatches else "✗"
    seconds = result.duration_ms / MS_PER_SECOND
    print(f"  {mark} {result.case_id:<45} {seconds:6.1f} s  {result.engine}", flush=True)


def _evaluate(cases: list[EvalCase], setup: _RunSetup) -> EvalReport:
    """Runs the cases with the analyzer of ``setup`` and prints the progress and the report."""
    analyzer = build_analyzer(setup.settings, rules_first=setup.mode == "kombiniert")
    print(f"{len(cases)} Fälle, Modus {setup.mode}, {setup.engine}, {setup.parallel} parallel")
    report = run(cases, analyzer, setup.parallel, _print_progress)
    print(format_report(report))
    return report


def _write_report(directory: Path, setup: _RunSetup, report: EvalReport) -> Path:
    """Writes the report as JSON named after the time and the mode, and returns its path."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{datetime.now():%Y%m%d-%H%M%S}-{setup.mode}.json"
    data = {"modus": setup.mode, "engine": setup.engine, "parallel": setup.parallel, **report.to_dict()}
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def _error(text: str) -> int:
    """Prints ``text`` to the error output and returns :data:`EXIT_ERROR`."""
    print(text, file=sys.stderr)
    return EXIT_ERROR


def main(argv: Sequence[str] | None = None) -> int:
    """Runs the evaluation from the command line.

    Args:
        argv: The arguments without the program name; ``None`` uses the real command line.

    Returns:
        0 on success, :data:`EXIT_BELOW_MINIMUM` below ``--mindestens``, :data:`EXIT_ERROR` on errors.
    """
    args = _parser().parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):  # Windows console: umlauts and check marks
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    configure_logging(logging.WARNING)
    try:
        settings = load_settings(args.config)
        cases = load_cases(args.faelle, args.nur)
    except (ConfigError, EvalCaseError) as exc:
        return _error(f"Fehler: {exc}")
    if not cases:
        return _error("Keine Fälle gefunden.")
    setup = _run_setup(settings, args)
    if setup is None:
        return _error('Fehler: in der Konfiguration ist keine KI eingestellt (analysis.engine = "rules").')
    report = _evaluate(cases, setup)
    print(f"\nBericht: {_write_report(args.bericht, setup, report)}")
    if args.mindestens is not None and report.score("kategorie").rate < args.mindestens:
        return EXIT_BELOW_MINIMUM
    return 0


if __name__ == "__main__":
    sys.exit(main())
