from dataclasses import dataclass, field

from mail_agent.analysis import AnalysisUnavailableError
from mail_agent.analysis.fallback import FallbackAnalyzer
from mail_agent.analysis.rules import RuleBasedAnalyzer
from mail_agent.config import OrganizationSettings
from mail_agent.models import Analysis, MailMessage
from mail_agent.parsing import parse_message
from tests.helpers import make_mail


@dataclass
class FailingAnalyzer:
    code: str
    permanent: bool
    calls: list[str] = field(default_factory=list)

    def analyze(self, message: MailMessage) -> Analysis:
        self.calls.append(message.key)
        raise AnalysisUnavailableError(self.code, permanent=self.permanent)


def _mail(n: int) -> MailMessage:
    return parse_message(make_mail(message_id=f"<m{n}@example.org>", subject="Rechnung"))


def test_falls_back_explains_why_and_marks_for_later() -> None:
    primary = FailingAnalyzer("ai_rate_limited", permanent=False)
    analyzer = FallbackAnalyzer(primary, RuleBasedAnalyzer(OrganizationSettings()))
    analysis = analyzer.analyze(_mail(1))
    assert analysis.engine == "regeln"
    assert analysis.ai_pending
    assert any("KI-Dienst ausgelastet" in w for w in analysis.findings)
    analyzer.analyze(_mail(2))
    assert len(primary.calls) == 2  # temporary errors: try again next time


def test_lasting_failure_pauses_the_ai_and_tries_again_later() -> None:
    now = [1000.0]
    primary = FailingAnalyzer("ai_usage_limit", permanent=True)
    analyzer = FallbackAnalyzer(
        primary, RuleBasedAnalyzer(OrganizationSettings()), pause_seconds=60, clock=lambda: now[0]
    )
    analyzer.analyze(_mail(1))
    second = analyzer.analyze(_mail(2))
    assert len(primary.calls) == 1  # paused: no second call
    assert second.ai_pending
    assert any("Nutzungslimit" in w for w in second.findings)
    assert analyzer.pause_reason is not None
    now[0] += 61  # the agent keeps running; the limit may have reset
    analyzer.analyze(_mail(3))
    assert len(primary.calls) == 2


def test_repeated_temporary_failures_pause_briefly() -> None:
    now = [0.0]
    primary = FailingAnalyzer("ai_timeout", permanent=False)
    analyzer = FallbackAnalyzer(
        primary, RuleBasedAnalyzer(OrganizationSettings()), short_pause_seconds=60, clock=lambda: now[0]
    )
    for n in range(5):
        analyzer.analyze(_mail(n))
    assert len(primary.calls) == 3  # a hanging CLI is not asked again for every message
    assert "mehrmals hintereinander" in (analyzer.pause_reason or "")
    now[0] = 61
    analyzer.analyze(_mail(9))
    assert len(primary.calls) == 4


def test_missing_login_names_the_command() -> None:
    primary = FailingAnalyzer("ai_credentials_missing", permanent=True)
    analysis = FallbackAnalyzer(primary, RuleBasedAnalyzer(OrganizationSettings())).analyze(_mail(1))
    assert any("claude auth login" in w for w in analysis.findings)
