"""Runs an analyzer over the evaluation cases and compares the results with the expected ones."""

import statistics
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

from ..analysis import Analyzer, timed_analyze
from ..models import Analysis, Category
from .cases import EvalCase, Expected

FIELDS = ("kategorie", "prioritaet", "entwurf", "zustaendig", "warnung", "frist", "anhang")
"""The compared fields, in the order of the report; the names match the keys of the case files."""
MS_PER_SECOND = 1000
"""Durations are measured in milliseconds and reported in seconds."""

Comparison = tuple[str, object, object]
"""One compared field: its name, the expected and the actual value."""


@dataclass(frozen=True)
class Mismatch:
    """One field where the analysis differs from the expected result.

    Attributes:
        field: The name of the field, one of :data:`FIELDS`.
        expected: The expected value as text.
        actual: The value of the analysis as text.
    """

    field: str
    expected: str
    actual: str


@dataclass(frozen=True)
class CaseResult:
    """The outcome of one case.

    Attributes:
        case_id: The id of the case.
        tags: The tags of the case, e.g. ``weiterleitung``.
        engine: The engine that produced the analysis, e.g. ``rules`` or a Claude model.
        duration_ms: How long the analysis took.
        checked: The fields compared for this case.
        mismatches: The fields that differ.
        unnecessary_draft: A draft was written although advertising or a forward never needs one.
        draft: The reply draft of the analysis.
    """

    case_id: str
    tags: tuple[str, ...]
    engine: str
    duration_ms: int
    checked: tuple[str, ...]
    mismatches: tuple[Mismatch, ...]
    unnecessary_draft: bool
    draft: str = ""


@dataclass(frozen=True)
class FieldScore:
    """How often one field was right.

    Attributes:
        correct: Cases with the expected value.
        total: Cases in which the field was checked.
    """

    correct: int
    total: int

    @property
    def rate(self) -> float:
        """Returns the share of correct cases between 0 and 1; 0 if the field was never checked."""
        return self.correct / self.total if self.total else 0.0


@dataclass
class EvalReport:
    """The results of one evaluation run."""

    results: list[CaseResult] = field(default_factory=list)

    def score(self, name: str) -> FieldScore:
        """Returns how often the field ``name`` was right in the cases that check it."""
        checked = [r for r in self.results if name in r.checked]
        wrong = sum(1 for r in checked if any(m.field == name for m in r.mismatches))
        return FieldScore(len(checked) - wrong, len(checked))

    @property
    def unnecessary_drafts(self) -> list[CaseResult]:
        """Returns the cases with a draft for advertising or a forward."""
        return [r for r in self.results if r.unnecessary_draft]

    @property
    def durations(self) -> list[int]:
        """Returns the duration of every analysis in milliseconds."""
        return [r.duration_ms for r in self.results]

    def ai_share(self) -> int:
        """Returns how many cases were analysed by Claude rather than by the rules."""
        return sum(1 for r in self.results if r.engine.startswith("claude"))

    def to_dict(self) -> dict[str, Any]:
        """Returns the report as data for the JSON file, with German keys."""
        return {
            "felder": {name: asdict(self.score(name)) for name in FIELDS},
            "unnoetige_entwuerfe": [r.case_id for r in self.unnecessary_drafts],
            "laufzeit_ms": {
                "mittel": round(statistics.mean(self.durations)) if self.durations else 0,
                "median": round(statistics.median(self.durations)) if self.durations else 0,
                "max": max(self.durations, default=0),
            },
            "ki_gefragt": self.ai_share(),
            "faelle": [asdict(r) for r in self.results],
        }


def _comparisons(expected: Expected, analysis: Analysis) -> list[Comparison]:
    """Returns the always compared fields plus those the case expects explicitly."""
    pairs: list[Comparison] = [
        ("kategorie", expected.category.value, analysis.category.value),
        ("prioritaet", expected.urgency.value, analysis.urgency.value),
        ("entwurf", expected.reply_needed, analysis.reply_needed),
    ]
    if expected.responsible is not None:
        actual = analysis.responsible.value if analysis.responsible else "–"
        pairs.append(("zustaendig", expected.responsible.value, actual))
    if expected.warning is not None:
        pairs.append(("warnung", expected.warning, bool(analysis.warnings)))
    if expected.deadline is not None:
        pairs.append(("frist", expected.deadline, bool(analysis.deadlines)))
    if expected.attachments_match is not None:
        # not checked (rules) counts as "fits"
        pairs.append(("anhang", expected.attachments_match, analysis.attachments_match is not False))
    return pairs


def compare(case: EvalCase, analysis: Analysis) -> CaseResult:
    """Compares the analysis of one case with its expected result.

    Args:
        case: The case with its expected result.
        analysis: What the analyzer made of the case's mail.

    Returns:
        The checked fields, the mismatches and whether an unnecessary draft was written.
    """
    expected = case.expected
    pairs = _comparisons(expected, analysis)
    mismatches = tuple(Mismatch(name, str(soll), str(ist)) for name, soll, ist in pairs if soll != ist)
    # Advertising and forwards must never get a draft; this is reported on its own.
    no_draft_expected = expected.category is Category.ADVERTISING or "weiterleitung" in case.tags
    return CaseResult(
        case_id=case.id,
        tags=case.tags,
        engine=analysis.engine,
        duration_ms=analysis.duration_ms or 0,
        checked=tuple(name for name, _, _ in pairs),
        mismatches=mismatches,
        unnecessary_draft=no_draft_expected and analysis.reply_needed,
        draft=analysis.reply_body,
    )


def run(
    cases: list[EvalCase],
    analyzer: Analyzer,
    parallel: int = 1,
    progress: Callable[[CaseResult], None] | None = None,
) -> EvalReport:
    """Analyses every case and compares it with its expected result.

    Args:
        cases: The cases to evaluate.
        analyzer: The analysis under test.
        parallel: How many cases are analysed at the same time.
        progress: Called with every result as soon as it is in, in the order of ``cases``.

    Returns:
        The report with one result per case.
    """
    report = EvalReport()

    def evaluate(case: EvalCase) -> CaseResult:
        return compare(case, timed_analyze(analyzer, case.message))

    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        for result in pool.map(evaluate, cases):
            report.results.append(result)
            if progress:
                progress(result)
    return report


def _seconds(milliseconds: float) -> str:
    """Returns a duration in seconds with one decimal, e.g. ``2.4``."""
    return f"{milliseconds / MS_PER_SECOND:.1f}"


def _duration_line(durations: list[int]) -> str:
    """Returns the line with mean, median and longest duration per mail."""
    return (
        f"Laufzeit pro Mail: Mittel {_seconds(statistics.mean(durations))} s, "
        f"Median {_seconds(statistics.median(durations))} s, "
        f"Maximum {_seconds(max(durations))} s"
    )


def _mismatch_lines(report: EvalReport) -> list[str]:
    """Returns the heading and one line per case with mismatches."""
    wrong = [r for r in report.results if r.mismatches]
    lines = [f"\nAbweichungen ({len(wrong)} Fälle):"]
    for result in wrong:
        details = ", ".join(f"{m.field}: soll {m.expected}, ist {m.actual}" for m in result.mismatches)
        lines.append(f"  {result.case_id} [{result.engine}]: {details}")
    return lines


def format_report(report: EvalReport) -> str:
    """Returns the report as German text for the console: hit rates, drafts, durations and mismatches."""
    lines = ["", "Trefferquote je Feld:"]
    for name in FIELDS:
        score = report.score(name)
        if score.total:
            lines.append(f"  {name:<11} {score.rate:6.1%}  ({score.correct}/{score.total})")
    drafts = report.unnecessary_drafts
    lines.append(f"Unnötige Entwürfe bei Werbung/Weiterleitung: {len(drafts)}")
    lines.extend(f"  - {r.case_id}" for r in drafts)
    if report.durations:
        lines.append(_duration_line(report.durations))
    lines.append(f"KI gefragt: {report.ai_share()} von {len(report.results)} Mails")
    lines.extend(_mismatch_lines(report))
    return "\n".join(lines)
