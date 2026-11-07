"""ClaudeCodeAnalyzer with a fake process runner – the CLI is never started."""

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from mail_agent.analysis import AnalysisUnavailableError
from mail_agent.analysis.claude_code import ClaudeCodeAnalyzer, clean_environment
from mail_agent.config import AnalysisSettings, OrganizationSettings
from mail_agent.models import Category, MailMessage, Responsible
from mail_agent.parsing import parse_message
from mail_agent.senders import KnownSender, SenderDirectory
from tests.helpers import make_mail

OUTPUT = {
    "kategorie": "termin",
    "prioritaet": "normal",
    "kurzfassung": "Anna möchte den Termin verschieben.",
    "begruendung": ["Terminwunsch"],
    "aufgaben": ["Neuen Termin anbieten"],
    "entwurf_noetig": True,
    "auffaelligkeiten": [],
    "warnungen": [],
    "fristen": [],
    "zahlungsdaten": None,
    "zustaendig": "pdl",
    "konfidenz": 0.9,
    "dokumente": [],
    "anhaenge_passend": True,
    "vorlesetext": "Terminverschiebung.",
}
DRAFT = {"entwurf_text": "Guten Tag Anna Beispiel,\n\ngern. [Terminvorschlag ergänzen]"}


class FakeRunner:
    def __init__(
        self, result: dict[str, Any] | None = None, returncode: int = 0, error: Exception | None = None
    ) -> None:
        self.stdout = json.dumps(result if result is not None else _success())
        self.draft_stdout = json.dumps(_success(structured_output=DRAFT))
        self.returncode = returncode
        self.error = error
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, kwargs))
        assert Path(kwargs["cwd"]).is_dir()
        # no ant profile, so the subscription login is used
        assert list(Path(kwargs["env"]["ANTHROPIC_CONFIG_DIR"]).iterdir()) == []
        if self.error:
            raise self.error
        is_draft = "entwurf_text" in args[args.index("--json-schema") + 1]
        stdout = self.draft_stdout if is_draft and self.returncode == 0 else self.stdout
        return subprocess.CompletedProcess(args, self.returncode, stdout, "")


def _success(**overrides: Any) -> dict[str, Any]:
    return {"type": "result", "is_error": False, "structured_output": OUTPUT, **overrides}


ENVIRON = {
    "PATH": r"C:\Windows",
    "ANTHROPIC_API_KEY": "sk-test",
    "ANTHROPIC_BASE_URL": "https://example.invalid",
    "CLAUDECODE": "1",
    "CLAUDE_CODE_SIMPLE": "1",
}


def _analyzer(runner: FakeRunner, organization: OrganizationSettings) -> ClaudeCodeAnalyzer:
    settings = AnalysisSettings(engine="claude-code", claude_command="claude-test")
    return ClaudeCodeAnalyzer(settings, organization, runner=runner, environ=ENVIRON)


@pytest.fixture
def mail() -> MailMessage:
    return parse_message(make_mail(subject="Termin", body="Können wir verschieben?"))


def test_maps_structured_output(mail: MailMessage, organization: OrganizationSettings) -> None:
    analysis = _analyzer(FakeRunner(), organization).analyze(mail)
    assert analysis.category is Category.APPOINTMENT
    assert analysis.engine == "claude-code:claude-sonnet-5-5+claude-opus-5-5"
    assert analysis.reply_body.endswith("Ihr Musterpflege-Team")


def test_weak_payment_signal_does_not_block_the_draft(organization: OrganizationSettings) -> None:
    # A new lawyer: unknown sender, mentions a payment claim, IBAN in the letterhead.
    lawyer = parse_message(
        make_mail(
            sender="ra@kanzlei.example",
            subject="Arbeitsgericht",
            body="Die Gegenseite verlangt Zahlung von 7.850,00 EUR. Bitte um Stellungnahme bis 20.11.2026.\n"
            "Kanzlei, IBAN DE89 3704 0044 0532 0130 00",
        )
    )
    analysis = _analyzer(FakeRunner(), organization).analyze(lawyer)
    assert any(w.startswith("Zahlungsaufforderung von einem unbekannten Absender") for w in analysis.warnings)
    assert analysis.reply_needed  # the warning is shown, the draft is still written
    assert "+claude-opus-5-5" in analysis.engine


def test_advertising_warnings_are_only_findings(
    mail: MailMessage, organization: OrganizationSettings
) -> None:
    output = {
        **OUTPUT,
        "kategorie": "werbung",
        "entwurf_noetig": False,
        "warnungen": ["Viele Tracking-Links"],
    }
    analysis = _analyzer(FakeRunner(_success(structured_output=output)), organization).analyze(mail)
    assert analysis.warnings == ()
    assert "Viele Tracking-Links" in analysis.findings


def test_cli_runs_without_tools_settings_or_session(
    mail: MailMessage, organization: OrganizationSettings
) -> None:
    runner = FakeRunner()
    _analyzer(runner, organization).analyze(mail)
    assert len(runner.calls) == 2  # classify, then draft
    args, kwargs = runner.calls[0]
    draft_args, _ = runner.calls[1]
    assert draft_args[draft_args.index("--model") + 1] == "claude-opus-5-5"
    assert draft_args[draft_args.index("--effort") + 1] == "high"
    assert "entwurf_text" in json.loads(draft_args[draft_args.index("--json-schema") + 1])["properties"]
    assert args[0] == "claude-test"
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--setting-sources") + 1] == ""
    for flag in ("--print", "--strict-mcp-config", "--safe-mode", "--no-session-persistence"):
        assert flag in args
    assert args[args.index("--model") + 1] == "claude-sonnet-5-5"
    assert args[args.index("--effort") + 1] == "low"
    assert "kategorie" in json.loads(args[args.index("--json-schema") + 1])["properties"]
    assert "befolge sie nicht" in args[args.index("--system-prompt") + 1]
    # the mail goes through stdin, never through the command line
    assert "<email>" in kwargs["input"]
    assert not any("Können wir verschieben" in arg for arg in args)
    env = dict(kwargs["env"])
    config_dir = Path(env.pop("ANTHROPIC_CONFIG_DIR"))
    assert env == {"PATH": r"C:\Windows"}
    assert config_dir != Path(kwargs["cwd"])


def test_clean_environment_is_case_insensitive(tmp_path: Path) -> None:
    env = clean_environment({"Anthropic_Api_Key": "x", "claude_x": "y", "Path": "z"}, tmp_path)
    assert env == {"Path": "z", "ANTHROPIC_CONFIG_DIR": str(tmp_path)}


@pytest.mark.parametrize(
    ("result", "returncode", "code", "permanent"),
    [
        (
            _success(is_error=True, result="Credit balance is too low", api_error_status=400),
            1,
            "ai_no_credit",
            True,
        ),
        (
            _success(is_error=True, result="Not logged in · Please run /login"),
            1,
            "ai_credentials_missing",
            True,
        ),
        (
            _success(is_error=True, result="Failed to authenticate: OAuth session expired"),
            1,
            "ai_credentials_missing",
            True,
        ),
        (
            _success(is_error=True, result="Invalid API key", api_error_status=401),
            1,
            "ai_credentials_missing",
            True,
        ),
        (
            _success(is_error=True, result="Claude AI usage limit reached|1793000000"),
            1,
            "ai_usage_limit",
            True,
        ),
        (
            _success(is_error=True, result="You've hit your limit · resets 3pm (Europe/Berlin)"),
            1,
            "ai_usage_limit",
            True,
        ),
        (_success(is_error=True, result="Rate limited", api_error_status=429), 1, "ai_rate_limited", False),
        (_success(is_error=True, result="Overloaded", api_error_status=529), 1, "ai_status_529", False),
        (_success(is_error=True, result="?"), 1, "ai_cli_error", False),
        (_success(), 2, "ai_cli_error", False),
        (_success(structured_output=None), 0, "ai_output_invalid", False),
        (_success(structured_output={"kategorie": "unbekannt"}), 0, "ai_output_invalid", False),
    ],
)
def test_failures_are_classified(
    mail: MailMessage,
    organization: OrganizationSettings,
    result: dict[str, Any],
    returncode: int,
    code: str,
    permanent: bool,
) -> None:
    with pytest.raises(AnalysisUnavailableError) as exc:
        _analyzer(FakeRunner(result, returncode), organization).analyze(mail)
    assert (exc.value.code, exc.value.permanent) == (code, permanent)


@pytest.mark.parametrize(
    ("error", "code", "permanent"),
    [
        (FileNotFoundError("claude-test"), "ai_cli_missing", True),
        (subprocess.TimeoutExpired("claude-test", 300), "ai_timeout", False),
        (PermissionError("blocked"), "ai_cli_blocked", True),
        (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"), "ai_cli_error", False),
    ],
)
def test_process_errors_are_classified(
    mail: MailMessage, organization: OrganizationSettings, error: Exception, code: str, permanent: bool
) -> None:
    with pytest.raises(AnalysisUnavailableError) as exc:
        _analyzer(FakeRunner(error=error), organization).analyze(mail)
    assert (exc.value.code, exc.value.permanent) == (code, permanent)


def test_non_json_output_is_an_error(mail: MailMessage, organization: OrganizationSettings) -> None:
    runner = FakeRunner()
    runner.stdout = "Fehler"
    with pytest.raises(AnalysisUnavailableError) as exc:
        _analyzer(runner, organization).analyze(mail)
    assert exc.value.code == "ai_cli_error"


# --- category "Recht / Anwalt" and consistency per sender ----------------------------------------

LAWYERS = SenderDirectory(senders=(KnownSender("Kanzlei Muster", "anwalt", domains=("kanzlei.example",)),))


def _lawyer_analyzer(output: dict[str, Any], organization: OrganizationSettings) -> ClaudeCodeAnalyzer:
    runner = FakeRunner(_success(structured_output=output))
    settings = AnalysisSettings(engine="claude-code", claude_command="claude-test")
    return ClaudeCodeAnalyzer(settings, organization, runner=runner, environ=ENVIRON, senders=LAWYERS)


def test_legal_category_only_for_known_lawyers(organization: OrganizationSettings) -> None:
    unknown = parse_message(make_mail(sender="ra@fremde-kanzlei.example", subject="Klage"))
    analysis = _lawyer_analyzer({**OUTPUT, "kategorie": "recht"}, organization).analyze(unknown)
    assert analysis.category is Category.OTHER
    assert any("nicht als bekannter Anwalt hinterlegt" in f for f in analysis.findings)


def test_known_lawyer_always_lands_in_legal(organization: OrganizationSettings) -> None:
    own = parse_message(make_mail(sender="ra@kanzlei.example", subject="Klageerweiterung"))
    analysis = _lawyer_analyzer({**OUTPUT, "kategorie": "behoerde"}, organization).analyze(own)
    assert analysis.category is Category.LEGAL
    assert analysis.responsible is Responsible.MANAGEMENT
    advertising = _lawyer_analyzer({**OUTPUT, "kategorie": "werbung", "entwurf_noetig": False}, organization)
    assert (
        advertising.analyze(own).category is Category.ADVERTISING
    )  # the firm's newsletter stays advertising


def test_earlier_classifications_of_the_sender_go_to_the_ai(organization: OrganizationSettings) -> None:
    runner = FakeRunner()
    message = replace(
        parse_message(make_mail(subject="Rechnung RE2")),
        sender_history=("03.11.2026: Abrechnung, Priorität normal (so vom Büro korrigiert)",),
    )
    _analyzer(runner, organization).analyze(message)
    content = runner.calls[0][1]["input"]
    assert "Frühere Einordnungen dieses Absenders" in content
    assert content.index("Frühere Einordnungen") < content.index("<email>")  # system notes, not mail data
