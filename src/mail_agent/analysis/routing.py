"""Rules first, AI second: clear cases are decided by the rules alone.

The AI is asked when the rules are unsure (confidence below the threshold), when the message
involves payments, deadlines, attachments or an unknown sender, or when it needs a reply (the draft
is written by the stronger model, not taken from a rule template). Known newsletter senders are
always clear. This saves time and subscription quota and keeps the AI for the cases where it adds
value.
"""

from dataclasses import replace

from ..models import Analysis, MailMessage
from . import Analyzer
from .rules import RuleBasedAnalyzer
from .signals import MailSignals

RULES_ONLY_REASON = "Eindeutiger Fall – nur Regeln, KI nicht gefragt"
"""Reason added to an analysis the rules decided alone, so the office can see the AI was not asked."""


def ai_triggers(analysis: Analysis, signals: MailSignals, threshold: float) -> tuple[str, ...]:
    """Returns why the AI should look at a message; empty if the rules' result is clear.

    Args:
        analysis: The rules' analysis.
        signals: What the rules found in the message.
        threshold: Confidence below which the rules count as unsure.

    Returns:
        Short German reasons, e.g. ``("Zahlung", "Frist")``.
    """
    if signals.sender is not None and signals.sender.type == "newsletter":
        return ()
    triggers = []
    if analysis.confidence is None or analysis.confidence < threshold:
        triggers.append("Regeln unsicher")
    if signals.payment_request or signals.ibans:
        triggers.append("Zahlung")
    if signals.deadlines:
        triggers.append("Frist")
    if signals.has_attachments:
        triggers.append("Anhang")
    if not signals.known:
        triggers.append("unbekannter Absender")
    if analysis.reply_needed:
        triggers.append("Antwort nötig")
    return tuple(triggers)


class RulesFirstAnalyzer:
    """Lets the rules decide clear cases alone and hands every other message to the AI."""

    def __init__(self, rules: RuleBasedAnalyzer, ai: Analyzer, threshold: float) -> None:
        """Combines the rules with the AI.

        Args:
            rules: The rule-based analysis; it runs for every message.
            ai: The AI, usually with the rules as fallback.
            threshold: Confidence below which the rules count as unsure (``analysis.rules_threshold``).
        """
        self._rules = rules
        self._ai = ai
        self._threshold = threshold

    def analyze(self, message: MailMessage) -> Analysis:
        """Returns the rules' analysis for a clear case, otherwise the AI's analysis."""
        result = self._rules.analyze(message)
        if ai_triggers(result, self._rules.signals(message), self._threshold):
            return self._ai.analyze(message)
        return replace(result, reasons=(*result.reasons, RULES_ONLY_REASON))
