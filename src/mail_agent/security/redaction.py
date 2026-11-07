"""Logging that masks e-mail addresses and IMAP credentials.

All modules log through the ``mail_agent`` logger hierarchy. The filter is attached to every
handler installed by :func:`configure_logging`, so records from child loggers are masked as well.
"""

import logging
import re

LOGGER_NAME = "mail_agent"
"""The package logger; every module logs below it (``mail_agent.<name>``)."""
REDACTED = "***"
"""Stands in for every masked part."""

_EMAIL_RE = re.compile(r"([A-Za-z0-9])[A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+")
"""An e-mail address; the first letter is kept, so log lines stay roughly readable."""
_LOGIN_RE = re.compile(r"\b(LOGIN|AUTHENTICATE)\s+\S.*")
"""An IMAP login as sent on the wire, with everything after it (user name and password)."""


def mask_email(value: str) -> str:
    """Returns the address masked: ``anna@example.org`` -> ``a***@***``."""
    if not value:
        return REDACTED
    return f"{value[0]}{REDACTED}@{REDACTED}" if "@" in value else REDACTED


def redact(text: str) -> str:
    """Returns ``text`` with IMAP logins and e-mail addresses masked."""
    text = _LOGIN_RE.sub(r"\1 " + REDACTED, text)
    return _EMAIL_RE.sub(lambda m: f"{m.group(1)}{REDACTED}@{REDACTED}", text)


class RedactingFilter(logging.Filter):
    """Masks message text and drops tracebacks, which may contain mail content or server replies."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Masks ``record`` in place; the record is always kept (``True``), only its content changes."""
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info:
            exc_type = record.exc_info[0]
            record.msg += f" [{exc_type.__name__ if exc_type else 'Exception'}]"
            record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def get_logger(name: str = "") -> logging.Logger:
    """Returns the logger ``mail_agent.<name>``, or the package logger without a name."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def configure_logging(level: int = logging.INFO) -> None:
    """Installs a redacting stderr handler on the package logger (idempotent)."""
    logger = get_logger()
    logger.setLevel(level)
    if not any(isinstance(f, RedactingFilter) for h in logger.handlers for f in h.filters):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler.addFilter(RedactingFilter())
        logger.addHandler(handler)
