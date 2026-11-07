"""Analysis through the Claude Code CLI (``claude -p``), billed to the user's Claude subscription.

Uses the same two steps, prompts, output schemas and mapping as :class:`ClaudeAnalyzer`; only the
transport differs. The CLI is locked down so that it behaves like a single model call:

* no tools, no MCP servers, no skills, no settings or CLAUDE.md files (``--tools ""``,
  ``--strict-mcp-config``, ``--setting-sources ""``, ``--safe-mode``) – the model cannot act;
* an empty temporary working directory and no saved session;
* a cleaned environment without ``ANTHROPIC_*``/``CLAUDE*`` variables, so neither an API key nor the
  variables of a surrounding Claude Code session redirect the request or its billing;
* an empty ``ANTHROPIC_CONFIG_DIR``: Claude Code prefers an ``ant auth login`` profile (API billing)
  over the subscription login, so the profile is hidden.

Data protection: a consumer subscription has no data processing agreement. Real mails only go to it
after the owner released them (``analysis.allow_mailbox_data``).
"""

import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from http import HTTPStatus
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from ..applications import DEFAULT_TEMPLATE
from ..config import AnalysisSettings, OrganizationSettings
from ..processes import NO_WINDOW
from ..senders import SenderDirectory
from . import AnalysisUnavailableError
from .claude import ClaudeAnalyzer, ModelChoice

TIMEOUT_SECONDS = 300
"""A CLI call is stopped after five minutes, so a hanging CLI cannot hold up the import for long."""
ENGINE_PREFIX = "claude-code"
"""Start of the engine name, so the office can see which analyses were billed to the subscription."""
LOCKDOWN_FLAGS = (
    "--tools",
    "",
    "--strict-mcp-config",
    "--setting-sources",
    "",
    "--safe-mode",
    "--disable-slash-commands",
    "--no-session-persistence",
)
"""CLI options that turn Claude Code into a single model call: nothing to act with, nothing saved."""
WORKDIR_NAME = "arbeitsordner"
"""Empty working folder inside the temporary folder, so no project file is in reach."""
CONFIG_DIR_NAME = "kein-ant-profil"
"""Empty folder for ``ANTHROPIC_CONFIG_DIR``, so no ``ant`` login profile switches to API billing."""

Runner = Callable[..., subprocess.CompletedProcess[str]]
"""Starts a process like :func:`subprocess.run`; tests pass a fake one."""

_HIDDEN_ENV_PREFIXES = ("ANTHROPIC_", "CLAUDE")
"""Variables that could carry an API key or redirect the request; compared without case."""
_LIMIT_HINTS = (
    "usage limit",
    "limit reached",
    "hit your limit",
    "reached your limit",
    "limit will reset",
    "out of extra usage",
)
"""Phrases of the CLI when the subscription limit is reached (the wording differs between versions)."""
_LOGIN_HINTS = ("login", "logged in", "authenticate")
"""Phrases of the CLI when the subscription login is missing or expired."""


def clean_environment(environ: Mapping[str, str], empty_config_dir: Path) -> dict[str, str]:
    """Returns ``environ`` without Anthropic credentials and session variables and without ``ant`` profile.

    Args:
        environ: The environment of the agent.
        empty_config_dir: An empty folder that stands in for the ``ant`` configuration.

    Returns:
        The environment for the CLI.
    """
    env = {key: value for key, value in environ.items() if not key.upper().startswith(_HIDDEN_ENV_PREFIXES)}
    env["ANTHROPIC_CONFIG_DIR"] = str(empty_config_dir)
    return env


def output_schema(output_type: type[BaseModel]) -> str:
    """Returns the JSON schema of ``output_type`` for ``--json-schema``, with umlauts kept readable."""
    return json.dumps(output_type.model_json_schema(), ensure_ascii=False)


def cli_error(result: dict[str, Any]) -> AnalysisUnavailableError:
    """Translates a failed CLI result into an error code without content.

    Args:
        result: The JSON result of the CLI.

    Returns:
        The error to raise; limits and a missing login are lasting and pause the AI.
    """
    text = str(result.get("result", "")).lower()
    status = result.get("api_error_status")
    if "credit balance" in text:
        return AnalysisUnavailableError("ai_no_credit", permanent=True)
    if any(hint in text for hint in _LOGIN_HINTS) or status == HTTPStatus.UNAUTHORIZED:
        return AnalysisUnavailableError("ai_credentials_missing", permanent=True)
    if any(hint in text for hint in _LIMIT_HINTS):
        return AnalysisUnavailableError("ai_usage_limit", permanent=True)
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        return AnalysisUnavailableError("ai_rate_limited")
    if isinstance(status, int):
        return AnalysisUnavailableError(f"ai_status_{status}")
    return AnalysisUnavailableError("ai_cli_error")


def structured_output[T: BaseModel](completed: subprocess.CompletedProcess[str], output_type: type[T]) -> T:
    """Returns the checked answer from the JSON result of the CLI.

    Args:
        completed: The finished CLI process.
        output_type: The schema the answer must match.

    Returns:
        The answer.

    Raises:
        AnalysisUnavailableError: The CLI failed or its answer does not match the schema.
    """
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise AnalysisUnavailableError("ai_cli_error") from None
    if not isinstance(result, dict):
        raise AnalysisUnavailableError("ai_cli_error")
    if result.get("is_error") or completed.returncode != 0:
        raise cli_error(result)
    try:
        return output_type.model_validate(result.get("structured_output"))
    except ValidationError:
        raise AnalysisUnavailableError("ai_output_invalid") from None


class ClaudeCodeAnalyzer(ClaudeAnalyzer):
    """Analyses messages like :class:`ClaudeAnalyzer`, but through the locked-down Claude Code CLI."""

    engine_prefix = ENGINE_PREFIX

    def __init__(  # noqa: PLR0913 - public signature used by service.setup and the tests
        self,
        settings: AnalysisSettings,
        organization: OrganizationSettings,
        runner: Runner = subprocess.run,
        environ: Mapping[str, str] = os.environ,
        *,
        senders: SenderDirectory | None = None,
        style_guide: str = "",
        application_template: str = DEFAULT_TEMPLATE,
        care_template: str = "",
    ) -> None:
        """Prepares the analysis; the CLI is only started for a request.

        Args:
            settings: Models, effort and the CLI command (``analysis.claude_command``).
            organization: Name and signature of the care service.
            runner: Starts the CLI; tests pass a fake one.
            environ: The environment the cleaned CLI environment is made from.
            senders: The known senders; without them every sender counts as unknown.
            style_guide: The office's writing style for drafts.
            application_template: Fixed confirmation of receipt for applications.
            care_template: The office's pattern for replies to care requests; empty without one.
        """
        super().__init__(
            settings,
            organization,
            senders=senders,
            style_guide=style_guide,
            application_template=application_template,
            care_template=care_template,
        )
        self._runner = runner
        self._environ = environ

    def _command(self, system: str, output_type: type[BaseModel], choice: ModelChoice) -> list[str]:
        """Builds the locked-down command line; the mail itself goes through standard input."""
        return [
            self._settings.claude_command,
            "--print",
            "--output-format",
            "json",
            "--json-schema",
            output_schema(output_type),
            *LOCKDOWN_FLAGS,
            "--model",
            choice.model,
            "--effort",
            choice.effort,
            "--system-prompt",
            system,
        ]

    def _request[T: BaseModel](
        self, system: str, content: str, output_type: type[T], choice: ModelChoice
    ) -> T:
        """Runs the CLI once and returns the checked answer.

        Raises:
            AnalysisUnavailableError: The CLI could not run or its answer is unusable.
        """
        completed = self._run(self._command(system, output_type, choice), content)
        return structured_output(completed, output_type)

    def _run(self, command: list[str], content: str) -> subprocess.CompletedProcess[str]:
        """Runs ``command`` in an empty temporary folder with a cleaned environment.

        Raises:
            AnalysisUnavailableError: The CLI is missing, blocked, too slow or wrote unreadable output.
        """
        with tempfile.TemporaryDirectory(prefix="mail-agent-claude-") as root:
            workdir, config_dir = Path(root, WORKDIR_NAME), Path(root, CONFIG_DIR_NAME)
            workdir.mkdir()
            config_dir.mkdir()
            try:
                return self._runner(
                    command,
                    input=content,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    env=clean_environment(self._environ, config_dir),
                    cwd=workdir,
                    timeout=TIMEOUT_SECONDS,
                    **NO_WINDOW,
                )
            except FileNotFoundError:
                raise AnalysisUnavailableError("ai_cli_missing", permanent=True) from None
            except subprocess.TimeoutExpired:
                raise AnalysisUnavailableError("ai_timeout") from None
            except OSError:  # e.g. blocked by Windows application control after an update
                raise AnalysisUnavailableError("ai_cli_blocked", permanent=True) from None
            except UnicodeDecodeError:
                raise AnalysisUnavailableError("ai_cli_error") from None
