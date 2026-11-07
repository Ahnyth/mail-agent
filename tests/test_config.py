from datetime import date, time
from pathlib import Path
from typing import Any

import pytest

from mail_agent.config import ConfigError, load_settings, parse_settings


def test_defaults(tmp_path: Path) -> None:
    settings = parse_settings({}, tmp_path)
    assert settings.source_type == "eml"
    assert settings.eml_dir == tmp_path / "daten" / "eingang"
    assert settings.database_path == tmp_path / "daten" / "mail_agent.sqlite3"
    assert settings.filing_root == tmp_path / "daten" / "ablage"
    assert settings.analysis.engine == "claude-code"  # the subscription, never the paid API by default
    assert settings.analysis.model_classify == "claude-sonnet-5-5"
    assert settings.analysis.model_draft == "claude-opus-5-5"
    assert (settings.analysis.rules_first, settings.analysis.rules_threshold) == (True, 0.8)
    assert settings.analysis.parallel == 2
    assert settings.analysis.known_senders is None  # default file missing: not used
    assert settings.new_cases_dir == tmp_path / "tests" / "eval" / "neu"
    assert settings.imap.host == "imap.example.org"
    assert settings.web_port == 8765


def test_load_example_config() -> None:
    example = Path(__file__).parent.parent / "mail_agent.example.toml"
    settings = load_settings(example)
    assert settings.organization.name == "Musterpflege GmbH"
    # the template starts safe: no mail goes to an AI until the owner has released it
    assert settings.analysis.engine == "rules"
    assert settings.analysis.allow_mailbox_data is False
    # the example points at the files shipped in config/
    assert settings.analysis.known_senders is not None
    assert settings.analysis.style_guide is not None


def test_load_without_file_uses_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert load_settings().data_dir == tmp_path / "daten"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"unbekannt": {}}, "Unbekannter Abschnitt"),
        ({"imap": {"passwort": "x"}}, "Unbekannte Schlüssel"),
        ({"imap": {"port": "993"}}, "imap.port"),
        ({"imap": {"port": True}}, "imap.port"),
        ({"imap": {"port": 70000}}, "ungültiger Port"),
        ({"imap": {"max_messages": "50"}}, "imap.max_messages"),
        ({"fetch": {"enabled": "ja"}}, "fetch.enabled"),
        ({"fetch": {"start_date": "03.11.2026"}}, "fetch.start_date"),
        ({"fetch": {"times": "09:00"}}, "fetch.times"),
        ({"fetch": {"times": ["9 Uhr"]}}, "ungültige Uhrzeit"),
        ({"fetch": {"times": ["24:00"]}}, "ungültige Uhrzeit"),
        ({"fetch": {"start": "x"}}, "Unbekannte Schlüssel"),
        ({"source": {"type": "pop3"}}, "source.type"),
        ({"analysis": {"engine": "gpt"}}, "analysis.engine"),
        ({"analysis": {"effort_draft": "extrem"}}, "analysis.effort_draft"),
        ({"analysis": {"effort": "low"}}, "ersetzt durch"),
        ({"analysis": {"model": "x"}}, "ersetzt durch"),
        ({"analysis": {"rules_threshold": 1.5}}, "zwischen 0 und 1"),
        ({"analysis": {"rules_threshold": True}}, "zwischen 0 und 1"),
        ({"analysis": {"parallel": 9}}, "analysis.parallel"),
        ({"analysis": {"known_senders": "fehlt.yaml"}}, "nicht gefunden"),
        ({"workflow": {"follow_up_days": 0}}, "workflow.follow_up_days"),
        ({"attachments": {"ocr_language": "deu; rm"}}, "ocr_language"),
        ({"voice": {"piper_model": "fehlt.onnx"}}, "nicht gefunden"),
        ({"imap": {"credential_target": "a b"}}, "credential_target"),
        ({"web": []}, "muss ein Abschnitt sein"),
    ],
)
def test_invalid_values_are_rejected(tmp_path: Path, data: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_settings(data, tmp_path)


def test_imap_requires_allowed_username(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="allowed_username"):
        parse_settings({"source": {"type": "imap"}, "analysis": {"engine": "rules"}}, tmp_path)


def test_mailbox_data_is_not_sent_to_cloud_ai_without_explicit_approval(tmp_path: Path) -> None:
    data: dict[str, Any] = {"source": {"type": "imap"}, "imap": {"allowed_username": "test@example.org"}}
    with pytest.raises(ConfigError, match="Cloud-KI"):
        parse_settings(data, tmp_path)
    data["analysis"] = {"allow_mailbox_data": True}
    assert parse_settings(data, tmp_path).source_type == "imap"
    data["analysis"] = {"engine": "rules"}
    assert parse_settings(data, tmp_path).analysis.engine == "rules"
    data["analysis"] = {"engine": "claude-code"}
    with pytest.raises(ConfigError, match="Cloud-KI"):
        parse_settings(data, tmp_path)


def test_claude_code_engine_and_command(tmp_path: Path) -> None:
    analysis = parse_settings(
        {"analysis": {"engine": "claude-code", "claude_command": r"C:\Tools\claude.exe"}}, tmp_path
    ).analysis
    assert (analysis.engine, analysis.claude_command) == ("claude-code", r"C:\Tools\claude.exe")
    assert parse_settings({}, tmp_path).analysis.claude_command == "claude"


def test_invalid_toml_and_missing_file(tmp_path: Path) -> None:
    broken = tmp_path / "kaputt.toml"
    broken.write_text("[imap\nhost = ", encoding="utf-8")
    with pytest.raises(ConfigError, match="ungültig"):
        load_settings(broken)
    with pytest.raises(ConfigError, match="nicht gefunden"):
        load_settings(tmp_path / "fehlt.toml")


def test_fetch_settings(tmp_path: Path) -> None:
    config = tmp_path / "mail_agent.toml"
    config.write_text(
        '[fetch]\nenabled = true\nstart_date = 2026-11-03\ntimes = ["13:30", "09:00", "9:00"]\n'
        "[imap]\nmax_messages = 50\n",  # older files keep working
        encoding="utf-8",
    )
    fetch = load_settings(config).fetch
    assert fetch.enabled
    assert fetch.start_date == date(2026, 11, 3)
    assert fetch.times == (time(9, 0), time(13, 30))  # sorted, without duplicates


def test_only_the_chosen_pc_fetches_the_real_mailbox(tmp_path: Path) -> None:
    imap: dict[str, Any] = {
        "source": {"type": "imap"},
        "imap": {"allowed_username": "t@example.org"},
        "analysis": {"engine": "rules"},
    }
    assert not parse_settings(imap, tmp_path).fetch.enabled  # must be switched on explicitly
    assert parse_settings({**imap, "fetch": {"enabled": True}}, tmp_path).fetch.enabled
    assert parse_settings({}, tmp_path).fetch.enabled  # test data (.eml) may always be read


def test_phase2_defaults(tmp_path: Path) -> None:
    settings = parse_settings({}, tmp_path)
    assert settings.analysis.send_attachment_text
    assert settings.attachments.read_text
    assert settings.attachments.ocr_language == "deu"
    assert (settings.workflow.follow_up_days, settings.workflow.briefing_days) == (3, 14)
    assert settings.piper_model is None  # default voice file missing: no speech output
    assert settings.analysis.application_template is None
