"""Replies to care requests follow the office's pattern: adapted by the AI, used as it is without it."""

from pathlib import Path
from typing import Any

import anthropic
import pytest

from mail_agent.analysis.claude import ClaudeAnalyzer
from mail_agent.analysis.rules import RuleBasedAnalyzer
from mail_agent.applications import load_template
from mail_agent.config import AnalysisSettings, ConfigError, OrganizationSettings, parse_settings
from mail_agent.models import Category, MailMessage
from mail_agent.parsing import parse_message
from tests.helpers import make_mail
from tests.test_claude import _REQUEST, FakeMessages, _client, _output, _response

PROJECT_TEMPLATE = Path(__file__).resolve().parents[1] / "config" / "vorlagen" / "pflegeanfrage_antwort.md"
TEMPLATE = "{anrede}\n\nvielen Dank für Ihre Anfrage an die {firma}. Wir melden uns innerhalb eines Werktags."


@pytest.fixture
def request_mail() -> MailMessage:
    return parse_message(
        make_mail(
            sender="Sabine Krüger <sabine@example.org>",
            subject="Pflege für meine Mutter",
            body="Meine Mutter wird Freitag entlassen und braucht 24-Stunden-Intensivpflege in 79098 Freiburg.",
        )
    )


def _claude(messages: FakeMessages, organization: OrganizationSettings) -> ClaudeAnalyzer:
    return ClaudeAnalyzer(AnalysisSettings(), organization, client=_client(messages), care_template=TEMPLATE)


def _draft_request(messages: FakeMessages) -> str:
    draft_call = messages.calls[-1]
    return str(draft_call["messages"][0]["content"])


def test_ai_gets_the_pattern_for_care_requests(
    request_mail: MailMessage, organization: OrganizationSettings
) -> None:
    messages = FakeMessages(_response(_output()))
    _claude(messages, organization).analyze(request_mail)
    request = _draft_request(messages)
    assert "<muster>" in request
    assert "Guten Tag Sabine Krüger," in request
    assert "vielen Dank für Ihre Anfrage an die Musterpflege." in request
    assert "nur nach dem, was fehlt" in request


def test_no_pattern_for_other_categories(
    request_mail: MailMessage, organization: OrganizationSettings
) -> None:
    messages = FakeMessages(_response(_output(kategorie="termin")))
    _claude(messages, organization).analyze(request_mail)
    assert "<muster>" not in _draft_request(messages)


def test_pattern_is_the_fallback_without_ai_draft(
    request_mail: MailMessage, organization: OrganizationSettings
) -> None:
    messages = FakeMessages(_response(_output()), draft_error=anthropic.APIConnectionError(request=_REQUEST))
    analysis = _claude(messages, organization).analyze(request_mail)
    assert analysis.reply_body.startswith(
        "Guten Tag Sabine Krüger,\n\nvielen Dank für Ihre Anfrage an die Musterpflege"
    )
    assert analysis.ai_pending


def test_rules_use_the_pattern(request_mail: MailMessage, organization: OrganizationSettings) -> None:
    analysis = RuleBasedAnalyzer(organization, care_template=TEMPLATE).analyze(request_mail)
    assert analysis.category is Category.CARE_REQUEST
    assert "innerhalb eines Werktags" in analysis.reply_body


def test_project_template_is_clean() -> None:
    text = load_template(PROJECT_TEMPLATE)
    assert text.startswith("{anrede}")
    assert "{firma}" in text
    assert not any(line.startswith("#") for line in text.splitlines())


def test_config_finds_or_rejects_the_template(tmp_path: Path) -> None:
    assert parse_settings({}, tmp_path).analysis.care_request_template is None
    (tmp_path / "config" / "vorlagen").mkdir(parents=True)
    (tmp_path / "config" / "vorlagen" / "pflegeanfrage_antwort.md").write_text(TEMPLATE, encoding="utf-8")
    assert parse_settings({}, tmp_path).analysis.care_request_template is not None
    with pytest.raises(ConfigError, match="care_request_template"):
        parse_settings({"analysis": {"care_request_template": "fehlt.md"}}, tmp_path)


def test_draft_effort_defaults_to_high() -> None:
    settings: Any = AnalysisSettings()
    assert (settings.effort_classify, settings.effort_draft) == ("low", "high")
