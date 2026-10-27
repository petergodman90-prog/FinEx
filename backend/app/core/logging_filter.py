"""
Logging filter to prevent PII leakage.

Attaches to Python's logging system and redacts patterns that look like
phone numbers, emails, or known PII field names from log output.

Per the security charter: "No raw PII in logs, AI prompts, analytics, or errors."
"""

from __future__ import annotations

import logging
import re

_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{7,}\d")
_PII_FIELDS = re.compile(
    r'(?i)("(?:email|phone|password|secret|token|api_key|access_token)'
    r'"\s*:\s*)"[^"]*"',
)

_REDACTED = "***REDACTED***"


class PIIFilter(logging.Filter):
    """Redacts PII patterns from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self._scrub(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self._scrub(a) if isinstance(a, str) else a
                    for a in record.args
                )
        return True

    @staticmethod
    def _scrub(text: str) -> str:
        text = _EMAIL_RE.sub(_REDACTED, text)
        text = _PHONE_RE.sub(_REDACTED, text)
        text = _PII_FIELDS.sub(rf'\1"{_REDACTED}"', text)
        return text


def _add_once(logger_or_handler) -> None:
    if not any(isinstance(f, PIIFilter) for f in logger_or_handler.filters):
        logger_or_handler.addFilter(PIIFilter())


def install_pii_filter() -> None:
    """Install the PII filter on the root logger, its handlers, and uvicorn.

    A filter on the root *logger* only sees records logged directly to it;
    records propagated from ``app.*`` or ``uvicorn.access`` bypass it. Handler
    filters are the reliable catch-all, so install on both.
    """
    root = logging.getLogger()
    _add_once(root)
    for handler in root.handlers:
        _add_once(handler)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "app"):
        _add_once(logging.getLogger(name))
