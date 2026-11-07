"""The Jinja templates of the web interface with the filters and values that every page uses."""

from datetime import datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates

from .. import __version__
from ..models import Category, Responsible, Urgency
from ..service import MailAgentService

TEMPLATE_DIR = Path(__file__).parent / "templates"
BYTES_PER_KB = 1024
BYTES_PER_MB = BYTES_PER_KB * 1024


def format_datetime(value: datetime | None) -> str:
    """Formats a point in time as local ``07.11.2026 14:30``; a dash stands for "not yet"."""
    return value.astimezone().strftime("%d.%m.%Y %H:%M") if value else "–"


def format_size(size: int) -> str:
    """Formats a file size in KB below one megabyte, otherwise in MB with one decimal."""
    if size < BYTES_PER_MB:
        return f"{size / BYTES_PER_KB:.0f} KB"
    return f"{size / BYTES_PER_MB:.1f} MB"


def build_templates(service: MailAgentService, *, quit_enabled: bool) -> Jinja2Templates:
    """Builds the templates with the German filters and the values of the page frame.

    Args:
        service: The agent; it decides whether the voice and AI buttons are shown.
        quit_enabled: Whether the "Beenden" button is shown (only when the server can be stopped).

    Returns:
        The configured templates.
    """
    templates = Jinja2Templates(directory=TEMPLATE_DIR)
    templates.env.filters["datum"] = format_datetime
    templates.env.filters["groesse"] = format_size
    templates.env.globals.update(
        categories=list(Category),
        urgencies=list(Urgency),
        responsibles=list(Responsible),
        version=__version__,
        speech_enabled=service.transcriber is not None,
        drafter_enabled=service.drafter is not None,
        quit_enabled=quit_enabled,
    )
    return templates
