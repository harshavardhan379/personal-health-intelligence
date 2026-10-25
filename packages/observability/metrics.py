"""In-process latency metrics per stage (YGH-93).

Counts, errors, mean, p50, p95 and max for each named stage: upload, processing, extraction,
retrieval, llm, verification, and every API route's total time. Percentiles come from the last
SAMPLES observations of each stage, so memory is bounded however long the server runs.

Limits, stated: per process (a restart resets it, and several workers each have their own),
and read through GET /api/v1/metrics. It is a diagnostic for a prototype, not a monitoring system.
"""

import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Optional

SAMPLES = 1000

# The stages named in YGH-93.
UPLOAD = "upload"
PROCESSING = "processing"
EXTRACTION = "extraction"
RETRIEVAL = "retrieval"
LLM = "llm"
VERIFICATION = "verification"
STAGES = (UPLOAD, PROCESSING, EXTRACTION, RETRIEVAL, LLM, VERIFICATION)


class _Series:
    __slots__ = ("count", "errors", "total_ms", "max_ms", "recent")

    def __init__(self) -> None:
        self.count = 0
        self.errors = 0
        self.total_ms = 0.0
        self.max_ms = 0.0
        self.recent: deque = deque(maxlen=SAMPLES)


def _percentile(sorted_values: list, fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, int(round(fraction * (len(sorted_values) - 1))))
    return sorted_values[index]


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._series: dict = {}

    def observe(self, name: str, milliseconds: float, ok: bool = True) -> None:
        with self._lock:
            series = self._series.setdefault(name, _Series())
            series.count += 1
            series.errors += 0 if ok else 1
            series.total_ms += milliseconds
            series.max_ms = max(series.max_ms, milliseconds)
            series.recent.append(milliseconds)

    @contextmanager
    def timed(self, name: str):
        """Time a block. An exception counts as an error and still propagates."""
        started = time.perf_counter()
        ok = True
        try:
            yield
        except BaseException:
            ok = False
            raise
        finally:
            self.observe(name, (time.perf_counter() - started) * 1000, ok)

    def snapshot(self) -> dict:
        with self._lock:
            out = {}
            for name, s in sorted(self._series.items()):
                ordered = sorted(s.recent)
                out[name] = {
                    "count": s.count,
                    "errors": s.errors,
                    "mean_ms": round(s.total_ms / s.count, 2) if s.count else 0.0,
                    "p50_ms": round(_percentile(ordered, 0.50), 2),
                    "p95_ms": round(_percentile(ordered, 0.95), 2),
                    "max_ms": round(s.max_ms, 2),
                }
            return out

    def reset(self) -> None:
        with self._lock:
            self._series.clear()


registry = Metrics()


def timed(name: str):
    return registry.timed(name)


def request_metric(method: str, template: str) -> str:
    return f"request {method} {template}"


def route_template(path: str, path_params: Optional[dict]) -> str:
    """"/api/v1/documents/<uuid>/file" -> "/api/v1/documents/{document_id}/file", so one route is
    one metric however many different ids are requested."""
    template = path
    for key, value in (path_params or {}).items():
        template = template.replace(str(value), "{" + str(key) + "}")
    return template
