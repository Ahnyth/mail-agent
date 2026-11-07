"""Message analysis: categorisation, urgency, summary, tasks and reply draft."""

import time
from dataclasses import replace
from typing import Protocol

from ..models import Analysis, MailMessage

MILLISECONDS_PER_SECOND = 1000
"""Converts the measured seconds into the milliseconds stored in ``duration_ms``."""


class Analyzer(Protocol):
    """Anything that turns a message into an analysis: the rules, Claude or a combination of both."""

    def analyze(self, message: MailMessage) -> Analysis:
        """Returns the analysis of ``message``."""


class AnalysisUnavailableError(Exception):
    """The analyzer cannot produce a result, e.g. because the AI service is unreachable.

    Attributes:
        code: A fixed error code such as ``ai_usage_limit``; it never contains mail content.
        permanent: Whether the failure lasts (limit, missing login), so the AI is paused for a while.
    """

    def __init__(self, code: str, *, permanent: bool = False) -> None:
        """Keeps the error code and whether the failure lasts."""
        super().__init__(code)
        self.code = code
        self.permanent = permanent


def timed_analyze(analyzer: Analyzer, message: MailMessage) -> Analysis:
    """Returns ``analyzer.analyze(message)`` with the elapsed time in ``duration_ms``."""
    start = time.perf_counter()
    analysis = analyzer.analyze(message)
    return replace(analysis, duration_ms=round((time.perf_counter() - start) * MILLISECONDS_PER_SECOND))
