"""What one assistant call did, as metadata only (YGH-92).

Each stage of the call (retrieval, the model, verification, the safety gate) records what it
did into the trace of the request in progress; the API route writes the finished trace to
`ai_traces`. A stage that runs with no trace active (the benchmark, a script) records nothing
and costs nothing.

Kept: request id, intent, provider, model, prompt version, model latency, token counts, the
ids of the events retrieved, how many claims got each verification label, the safety action,
an outcome status, and the LENGTH of the question. Never kept: the question, the answer, the
evidence text or any field of a health record. Telemetry that held those would be a second
copy of the health data.
"""

import contextvars
from dataclasses import dataclass, field, fields
from typing import Optional

from packages.observability.context import get_request_id

# status values
OK = "ok"
BLOCKED = "blocked"  # the safety layer refused before retrieval or after generation
NO_EVIDENCE = "no_evidence"
PROVIDER_ERROR = "provider_error"
ERROR = "error"  # the call crashed before it could answer


@dataclass
class AITraceData:
    request_id: Optional[str] = None
    intent: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    prompt_version: Optional[str] = None
    llm_ms: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    retrieval_event_ids: list = field(default_factory=list)
    evidence_count: Optional[int] = None
    claim_labels: dict = field(default_factory=dict)
    safety_action: Optional[str] = None
    status: str = OK
    question_chars: Optional[int] = None


_FIELDS = {f.name for f in fields(AITraceData)}
_current: contextvars.ContextVar = contextvars.ContextVar("ai_trace", default=None)


def start_trace(question_chars: Optional[int] = None) -> AITraceData:
    trace = AITraceData(request_id=get_request_id(), question_chars=question_chars)
    _current.set(trace)
    return trace


def current_trace() -> Optional[AITraceData]:
    return _current.get()


def end_trace() -> None:
    _current.set(None)


def record(**values) -> None:
    """Set fields on the active trace; does nothing when there is none. An unknown field is a
    programming error and raises, so a typo cannot silently drop telemetry."""
    unknown = set(values) - _FIELDS
    if unknown:
        raise TypeError(f"unknown trace field(s): {sorted(unknown)}")
    trace = _current.get()
    if trace is not None:
        for key, value in values.items():
            setattr(trace, key, value)


def label_counts(labels: list) -> dict:
    counts: dict = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return counts
