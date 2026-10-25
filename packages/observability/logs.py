"""Structured (JSON) logging that excludes health content (YGH-90).

Every line is one JSON object with a timestamp, level, logger, request id and message. Two filters
enforce the "no health content" rule at run time on top of the convention and the test that scans
the source for it:

  * fields passed through `extra=` whose names look like content (question, answer, quote, claim,
    data, prompt, ...) are replaced with "[redacted]";
  * a message longer than a sentence or two (a dump of something) is cut.

Free text interpolated into a message cannot be recognised, so the rule for code is: log ids,
counts, durations, statuses and the error CLASS, never the content or the exception text of
something that handled content (tests/unit/test_logging_hygiene.py checks the source for it).
"""

import logging

from packages.observability.context import get_request_id

try:  # python-json-logger moved its formatter in 4.x
    from pythonjsonlogger.json import JsonFormatter
except ImportError:  # pragma: no cover - older releases
    from pythonjsonlogger.jsonlogger import JsonFormatter

# Names that must never carry a value into a log line.
SENSITIVE_FIELDS = frozenset({
    "question", "answer", "text", "quote", "claim", "claims", "claim_text", "data", "prompt",
    "content", "body", "evidence", "email", "password", "token", "access_token", "authorization",
    "document_name", "explanation", "uncertainty", "flag_text",
})
MAX_MESSAGE_CHARS = 1000
REDACTED = "[redacted]"


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for name in list(record.__dict__):
            if name.lower() in SENSITIVE_FIELDS:
                record.__dict__[name] = REDACTED
        message = record.getMessage()
        if len(message) > MAX_MESSAGE_CHARS:
            record.msg = message[:MAX_MESSAGE_CHARS] + "...[truncated]"
            record.args = ()
        return True


def build_handler(stream=None) -> logging.Handler:
    """The JSON handler with both filters (stderr unless a stream is given)."""
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
    ))
    handler.addFilter(RequestIdFilter())
    handler.addFilter(RedactionFilter())
    return handler


def configure_logging(level: str) -> None:
    """Route all logs through one JSON handler. Replaces any handlers already installed."""
    root = logging.getLogger()
    root.handlers = [build_handler()]
    root.setLevel(level)
