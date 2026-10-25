"""Rate limits for the few endpoints worth attacking or abusing (YGH-115).

A sliding window per key, in memory:

  * login      failed attempts only, per client address + email: brute-forcing one account from
               one address stops after LOGIN_FAILURES in LOGIN_WINDOW_S; a success clears it
  * register   per client address: account spam
  * assistant  per user: every call costs a model run, and a small local model serves one at a time

Limits of an in-process limiter, stated plainly: each API process counts separately and a restart
forgets everything, so behind several workers the real limit is limit x workers. That is fine for
this single-process MVP; a shared store (Redis) is the upgrade. Behind a reverse proxy the client
address is the proxy's unless uvicorn runs with --proxy-headers and --forwarded-allow-ips.
"""

import threading
import time
from collections import deque
from typing import Callable, Optional

from fastapi import HTTPException, Request

MAX_KEYS = 10_000  # memory bound: the oldest keys are forgotten first

LOGIN_FAILURES = 5
LOGIN_WINDOW_S = 15 * 60
REGISTER_ATTEMPTS = 10
REGISTER_WINDOW_S = 60 * 60
ASK_CALLS = 30
ASK_WINDOW_S = 60


class SlidingWindow:
    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window_s, self.clock = limit, window_s, clock
        self._hits: dict = {}
        self._lock = threading.Lock()

    def _live(self, key: str) -> deque:
        hits = self._hits.get(key)
        if hits is None:
            return deque()
        cutoff = self.clock() - self.window_s
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if not hits:
            self._hits.pop(key, None)
        return hits

    def retry_after(self, key: str) -> int:
        """Whole seconds until a slot frees up, or 0 if the key is under its limit."""
        with self._lock:
            hits = self._live(key)
            if len(hits) < self.limit:
                return 0
            return max(1, int(hits[0] + self.window_s - self.clock()) + 1)

    def record(self, key: str) -> None:
        with self._lock:
            hits = self._live(key)
            if not hits and len(self._hits) >= MAX_KEYS:
                self._hits.pop(next(iter(self._hits)))
            hits.append(self.clock())
            self._hits[key] = hits

    def reset(self, key: Optional[str] = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


login_failures = SlidingWindow(LOGIN_FAILURES, LOGIN_WINDOW_S)
register_attempts = SlidingWindow(REGISTER_ATTEMPTS, REGISTER_WINDOW_S)
ask_calls = SlidingWindow(ASK_CALLS, ASK_WINDOW_S)


def client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def refuse_if_limited(limiter: SlidingWindow, key: str) -> None:
    """429 with a Retry-After header if `key` is at its limit. Does not count the call."""
    wait = limiter.retry_after(key)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"Too many attempts. Try again in {wait} seconds.",
            headers={"Retry-After": str(wait)},
        )


def count_and_refuse_if_limited(limiter: SlidingWindow, key: str) -> None:
    """For limits that count every call: refuse when full, otherwise record this call."""
    refuse_if_limited(limiter, key)
    limiter.record(key)
