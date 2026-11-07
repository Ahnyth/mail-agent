"""Shared fixtures. Every test runs without network: any outbound socket connection fails."""

import socket
from pathlib import Path

import pytest

from mail_agent.analysis.rules import RuleBasedAnalyzer
from mail_agent.config import OrganizationSettings, Settings, parse_settings
from mail_agent.filing import DocumentStore
from mail_agent.sample_data import SAMPLES, build_message
from mail_agent.service import MailAgentService
from mail_agent.sources.eml_folder import EmlFolderSource
from mail_agent.storage import MailRepository


class NetworkAccessError(RuntimeError):
    pass


_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Refuse every non-loopback connection (asyncio on Windows needs a loopback socket pair).

    Also hides the developer's real Anthropic credentials (environment and `ant auth login` profile).
    """
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo

    def _check(address: object) -> None:
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOOPBACK:
            raise NetworkAccessError(f"Netzwerkzugriff in Unit-Tests ist verboten: {host!r}")

    def connect(self: socket.socket, address: object) -> None:
        _check(address)
        original_connect(self, address)  # type: ignore[arg-type]

    def connect_ex(self: socket.socket, address: object) -> int:
        _check(address)
        return original_connect_ex(self, address)  # type: ignore[arg-type]

    def getaddrinfo(host: object, *args: object, **kwargs: object) -> object:
        _check(host)
        return original_getaddrinfo(host, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    for name in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_PROFILE",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_CONFIG_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    # The SDK looks for login profiles under %APPDATA% (Windows) or ~/.config (elsewhere).
    empty_home = str(tmp_path_factory.mktemp("home"))
    monkeypatch.setenv("APPDATA", empty_home)
    monkeypatch.setenv("HOME", empty_home)


@pytest.fixture
def sample_raw() -> dict[str, bytes]:
    return {sample.filename: bytes(build_message(sample)) for sample in SAMPLES}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return parse_settings({"analysis": {"engine": "rules"}}, tmp_path)


@pytest.fixture
def organization() -> OrganizationSettings:
    return OrganizationSettings(
        name="Musterpflege", signature="Mit freundlichen Grüßen\nIhr Musterpflege-Team"
    )


@pytest.fixture
def service(settings: Settings, organization: OrganizationSettings) -> MailAgentService:
    return MailAgentService(
        repository=MailRepository(settings.database_path),
        analyzer=RuleBasedAnalyzer(organization),
        documents=DocumentStore(settings.filing_root),
        source=EmlFolderSource(settings.eml_dir),
    )
