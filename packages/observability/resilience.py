"""Timeout and retry policy for every outside dependency (YGH-94).

One table, POLICIES, says for each dependency how long to wait, whether to retry, and why. The
rules behind it:

  * Retry only what is transient: a refused or dropped connection, a 429 or 5xx reply, a file
    briefly locked. Never a 4xx (a bad key stays bad), a corrupt document, a missing file or a
    full disk.
  * Never retry a timeout of a slow, expensive call: a model that took 90 s will take 90 s
    again, and three of them is a five-minute wait for the same failure.
  * Back off exponentially with a little jitter, and give up after a few tries. A retry that
    never ends is an outage.
  * Every retry is logged (dependency, attempt, delay, error class; never content).
"""

import errno
import logging
import random
import time
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger("app.resilience")


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 1  # total tries, so 1 means no retry
    base_delay: float = 0.0  # seconds before the 2nd try; doubles each time
    max_delay: float = 0.0
    jitter: float = 0.0  # fraction of the delay added at random


@dataclass(frozen=True)
class Policy:
    name: str
    timeout_s: Optional[float]  # None: the dependency has no network wait to bound
    retry: RetryPolicy
    why: str


NO_RETRY = RetryPolicy(attempts=1)

POLICIES = {
    "llm": Policy(
        "llm", 90.0, RetryPolicy(attempts=3, base_delay=1.0, max_delay=8.0, jitter=0.25),
        "Retry a refused/dropped connection and 429/5xx; never a read timeout (a slow model stays "
        "slow) or a 4xx. The timeout is settings.llm_timeout_seconds."),
    "ocr": Policy(
        "ocr", 60.0, NO_RETRY,
        "Per page. A page that hits the limit will hit it again, so it fails the document with a "
        "clear reason and the user can retry."),
    "embeddings": Policy(
        "embeddings", None, NO_RETRY,
        "The product's embedding is computed in-process: nothing to wait on or retry. A hosted "
        "embedding service, if one is added, takes llm-style retries and a 30 s timeout."),
    "storage": Policy(
        "storage", None, RetryPolicy(attempts=3, base_delay=0.05, max_delay=0.5, jitter=0.25),
        "Local disk: retry a briefly locked or interrupted file operation; never a missing file or "
        "a full disk."),
    "database": Policy(
        "database", 30.0, RetryPolicy(attempts=3, base_delay=0.2, max_delay=2.0, jitter=0.25),
        "5 s to connect, 30 s per statement, 10 s waiting for a pooled connection; retry a dropped "
        "connection on idempotent work (marking a document failed). Request handlers do not retry "
        "their own writes."),
}

DATABASE_CONNECT_TIMEOUT_S = 5
DATABASE_STATEMENT_TIMEOUT_MS = 30_000
DATABASE_POOL_TIMEOUT_S = 10


def is_transient_http(exc: BaseException) -> bool:
    import httpx

    if isinstance(exc, httpx.ReadTimeout):
        return False  # a slow model stays slow
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return False


def is_transient_os(exc: BaseException) -> bool:
    if isinstance(exc, (FileNotFoundError, IsADirectoryError, NotADirectoryError)):
        return False
    if isinstance(exc, (PermissionError, BlockingIOError, InterruptedError)):
        return True  # a lock, an antivirus scan, a signal
    if isinstance(exc, OSError):
        return exc.errno in (errno.EAGAIN, errno.EBUSY, errno.EINTR)
    return False


def is_transient_db(exc: BaseException) -> bool:
    from sqlalchemy.exc import DBAPIError, OperationalError

    if isinstance(exc, OperationalError):
        return True  # connection refused / dropped / timed out
    return isinstance(exc, DBAPIError) and bool(exc.connection_invalidated)


def call_with_retry(
    fn: Callable,
    policy: RetryPolicy,
    *,
    is_transient: Callable[[BaseException], bool],
    name: str,
    sleep: Callable[[float], None] = time.sleep,
    rng: Callable[[], float] = random.random,
):
    """Call fn(); on a transient failure wait and try again, up to policy.attempts tries."""
    attempt = 1
    while True:
        try:
            return fn()
        except Exception as exc:
            if attempt >= policy.attempts or not is_transient(exc):
                raise
            delay = min(policy.max_delay, policy.base_delay * (2 ** (attempt - 1)))
            delay *= 1 + policy.jitter * rng()
            logger.warning(
                "retrying_dependency",
                extra={"dependency": name, "attempt": attempt, "next_delay_s": round(delay, 3),
                       "error_type": type(exc).__name__},
            )
            sleep(delay)
            attempt += 1
