from dataclasses import dataclass, field, replace

from mail_agent.analysis.routing import RulesFirstAnalyzer, ai_triggers
from mail_agent.analysis.rules import RuleBasedAnalyzer
from mail_agent.config import OrganizationSettings
from mail_agent.models import Analysis, Category, MailMessage, Urgency
from mail_agent.parsing import parse_message
from mail_agent.senders import KnownSender, SenderDirectory
from tests.helpers import make_mail

SENDERS = SenderDirectory(
    own_domains=("musterpflege.example",),
    senders=(KnownSender("News", "newsletter", domains=("news.example",)),),
)


@dataclass
class RecordingAI:
    calls: list[str] = field(default_factory=list)

    def analyze(self, message: MailMessage) -> Analysis:
        self.calls.append(message.subject)
        return Analysis(Category.OTHER, Urgency.NORMAL, "KI", (), (), False, "", "", (), (), "ki")


def _router(threshold: float = 0.8) -> tuple[RulesFirstAnalyzer, RecordingAI]:
    ai = RecordingAI()
    return RulesFirstAnalyzer(RuleBasedAnalyzer(OrganizationSettings(), SENDERS), ai, threshold), ai


def test_known_newsletter_is_decided_by_rules_alone() -> None:
    router, ai = _router()
    mail = make_mail(
        sender="info@news.example", subject="Rechnung tragen", body="Rechnung, Frist bis 01.12.2026"
    )
    analysis = router.analyze(parse_message(mail))
    assert ai.calls == []
    assert analysis.category is Category.ADVERTISING
    assert any("KI nicht gefragt" in r for r in analysis.reasons)


def test_clear_internal_message_without_triggers_stays_with_rules() -> None:
    router, ai = _router(threshold=0.7)
    router.analyze(
        parse_message(
            make_mail(sender="pdl@musterpflege.example", subject="Team", body="Teamsitzung Montag.")
        )
    )
    assert ai.calls == []


def test_triggers_send_message_to_ai() -> None:
    router, ai = _router()
    router.analyze(
        parse_message(make_mail(sender="pdl@musterpflege.example", subject="Rechnung", body="10,00 EUR"))
    )
    router.analyze(parse_message(make_mail(sender="x@unbekannt.example", subject="Hallo", body="Hallo")))
    assert ai.calls == ["Rechnung", "Hallo"]


def test_reply_needed_goes_to_the_ai_for_a_real_draft() -> None:
    rules = RuleBasedAnalyzer(OrganizationSettings(), SENDERS)
    signals = rules.signals(
        parse_message(make_mail(sender="pdl@musterpflege.example", body="Teamsitzung Montag."))
    )
    clear = Analysis(
        Category.INTERNAL, Urgency.NORMAL, "", (), (), False, "", "", (), (), "regeln", confidence=1.0
    )
    assert ai_triggers(clear, signals, 0.8) == ()
    assert ai_triggers(replace(clear, reply_needed=True), signals, 0.8) == ("Antwort nötig",)


def test_trigger_reasons() -> None:
    rules = RuleBasedAnalyzer(OrganizationSettings(), SENDERS)
    message = parse_message(
        make_mail(
            sender="x@unbekannt.example",
            body="Rechnung über 5,00 EUR bis zum 30.11.2026",
            attachments=(("a.pdf", "application/pdf", b"%PDF"),),
        )
    )
    triggers = ai_triggers(rules.analyze(message), rules.signals(message), 0.8)
    assert set(triggers) >= {"Zahlung", "Frist", "Anhang", "unbekannter Absender"}
    assert "Regeln unsicher" in ai_triggers(rules.analyze(message), rules.signals(message), 1.0)
