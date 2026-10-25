"""The request id: one value that follows a request through the API, its log lines, the calls it
makes to the model, and the background work it starts (YGH-91)."""

import contextvars
import re
import uuid
from typing import Optional

HEADER = "X-Request-ID"

_request_id: contextvars.ContextVar = contextvars.ContextVar("request_id", default=None)
_ACCEPTED = re.compile(r"[A-Za-z0-9._-]{1,64}")


def new_request_id() -> str:
    return uuid.uuid4().hex


def valid_request_id(value: object) -> bool:
    """Only plain, short tokens are accepted from a caller: an id ends up in logs and response
    headers, so anything with spaces, newlines or other characters is replaced, not echoed."""
    return isinstance(value, str) and _ACCEPTED.fullmatch(value) is not None


def request_id_from(value: Optional[str]) -> str:
    return value if valid_request_id(value) else new_request_id()


def set_request_id(value: Optional[str]) -> None:
    _request_id.set(value)


def get_request_id() -> Optional[str]:
    return _request_id.get()
