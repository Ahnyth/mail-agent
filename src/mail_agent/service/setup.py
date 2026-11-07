"""Building the analysis from the configuration: known senders, rules, Claude and text recognition."""

from ..analysis import Analyzer
from ..analysis.claude import ClaudeAnalyzer
from ..analysis.claude_code import ClaudeCodeAnalyzer
from ..analysis.fallback import FallbackAnalyzer
from ..analysis.routing import RulesFirstAnalyzer
from ..analysis.rules import RuleBasedAnalyzer
from ..applications import load_template
from ..attachments import TesseractOcr, configured_ocr, find_tesseract
from ..config import Settings
from ..security.redaction import get_logger
from ..senders import SenderDirectory, load_local_senders, load_senders
from .sender_book import local_senders_path

_log = get_logger("service")


def build_senders(settings: Settings) -> SenderDirectory:
    """Returns the known senders: ``config/bekannte_absender.yaml`` plus those saved in the web interface.

    The entries saved on this PC win over the shared list.
    """
    path = settings.analysis.known_senders
    shared = load_senders(path) if path else SenderDirectory()
    return shared.merged(load_local_senders(local_senders_path(settings)))


def build_ocr(settings: Settings) -> TesseractOcr | None:
    """Returns the text recognition for scans, or ``None`` if Tesseract is not installed."""
    command = find_tesseract(settings.attachments.tesseract_command)
    if command is None:
        _log.info("Tesseract nicht gefunden – Scans werden ohne Texterkennung abgelegt")
    return configured_ocr(command, settings.attachments.ocr_language)


def care_template(settings: Settings) -> str:
    """Returns the office's pattern for replies to care requests; empty if the file is missing."""
    path = settings.analysis.care_request_template
    return load_template(path) if path else ""


def style_guide(settings: Settings) -> str:
    """Returns the writing style for drafts, or an empty text without a style guide."""
    path = settings.analysis.style_guide
    return path.read_text(encoding="utf-8") if path else ""


def claude_analyzer(settings: Settings, senders: SenderDirectory) -> ClaudeAnalyzer:
    """Returns Claude as configured: through Claude Code (subscription) or through the API."""
    analysis = settings.analysis
    claude_class = ClaudeCodeAnalyzer if analysis.engine == "claude-code" else ClaudeAnalyzer
    return claude_class(
        analysis,
        settings.organization,
        senders=senders,
        style_guide=style_guide(settings),
        application_template=load_template(analysis.application_template),
        care_template=care_template(settings),
    )


def build_drafter(settings: Settings) -> ClaudeAnalyzer | None:
    """Returns Claude for "Von KI formulieren lassen", or ``None`` when only the rules are configured."""
    if settings.analysis.engine == "rules":
        return None
    return claude_analyzer(settings, build_senders(settings))


def build_analyzer(settings: Settings, *, rules_first: bool | None = None) -> Analyzer:
    """Returns the analysis as configured: rules only, Claude with the rules as fallback, or rules first.

    Args:
        settings: The configuration.
        rules_first: Overrides ``analysis.rules_first`` (used by the evaluation).

    Returns:
        The analyzer for new and re-analysed messages.
    """
    analysis = settings.analysis
    senders = build_senders(settings)
    template = load_template(analysis.application_template)
    rules = RuleBasedAnalyzer(settings.organization, senders, template, care_template(settings))
    if analysis.engine == "rules":
        return rules
    # Claude always falls back to the rules, so the office keeps working without the AI.
    ai = FallbackAnalyzer(claude_analyzer(settings, senders), rules)
    use_rules_first = analysis.rules_first if rules_first is None else rules_first
    return RulesFirstAnalyzer(rules, ai, analysis.rules_threshold) if use_rules_first else ai
