"""Structured (JSON) logging that excludes health content (YGH-90).

Every line is one JSON object with a timestamp, level, logger, request id and message. Two filters
enforce the "no health content" rule at run time on top of the convention and the test that scans
the source for it:

  * fields passed through `extra=` whose names look like content (question, answer, quote, claim,
    data, prompt, ...) are replaced with "[redacted]";
  * a message longer than a sentence or two (a dump of something) is cut.

Exception messages are never printed (SafeJsonFormatter), nor are request URLs with their query
strings (the uvicorn access log is disabled).

Free text interpolated into a message cannot be recognised, so the rule for code is: log ids,
counts, durations, statuses and the error CLASS, never the content or the exception text of
something that handled content (tests/unit/test_logging_hygiene.py checks the source for it).
"""

import logging
import traceback

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


def safe_traceback(exc_info) -> str:
    """A traceback with every exception's CLASS but never its message.

    The stock formatter prints `ValueError: <message>`, and a message can quote what was being
    handled: a validation error carries the whole request body (a password, a health record), a
    parser error carries a fragment of the document. The frames (file, line, function) are what
    a developer needs and hold no values, so they stay. Chained causes are kept the same way."""
    chain, seen = [], set()
    exc = exc_info[1]
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        chain.append(exc)
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    parts = []
    for exc in reversed(chain):  # oldest cause first, as the standard library prints it
        frames = "".join(traceback.format_list(traceback.extract_tb(exc.__traceback__)))
        parts.append(f"Traceback (most recent call last):\n{frames}{type(exc).__qualname__}")
    joiner = "\n\nThe above exception was the direct cause of the following exception:\n\n"
    return joiner.join(parts)


class SafeJsonFormatter(JsonFormatter):
    def formatException(self, ei):  # noqa: N802 - the logging.Formatter hook name
        return safe_traceback(ei)


def build_handler(stream=None) -> logging.Handler:
    """The JSON handler with both filters (stderr unless a stream is given)."""
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SafeJsonFormatter(
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
    # uvicorn's access log prints every URL with its query string, and a search puts the user's
    # own words there (/search?q=...). The app's own `request_finished` line logs the route
    # template instead, so the access log is switched off whatever way the server was started.
    access = logging.getLogger("uvicorn.access")
    access.handlers, access.propagate, access.disabled = [], False, True
