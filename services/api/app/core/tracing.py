"""Persist what one assistant call did (YGH-92). Metadata only (packages/observability/trace.py)."""

import logging
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from packages.observability.trace import AITraceData
from packages.schemas.models import AITrace

logger = logging.getLogger("app.tracing")


def verification_summary(claim_labels: dict) -> str:
    """One word for how the claims fared: no_claims, all_supported or flagged."""
    if not claim_labels:
        return "no_claims"
    return "all_supported" if set(claim_labels) == {"SUPPORTED"} else "flagged"


def write_ai_trace(
    db: Session, user_id: uuid.UUID, data: Optional[AITraceData], total_ms: int
) -> None:
    """Save the trace. Telemetry must never break the thing it observes: a failure here is logged
    (class only) and swallowed, and the user still gets their answer."""
    if data is None:
        return
    try:
        both = data.input_tokens is not None and data.output_tokens is not None
        db.add(AITrace(
            user_id=user_id,
            request_id=data.request_id,
            intent=data.intent,
            provider=data.provider or "none",
            model=data.model,
            prompt_version=data.prompt_version,
            latency_ms=total_ms,
            llm_ms=data.llm_ms,
            token_usage=data.input_tokens + data.output_tokens if both else None,
            input_tokens=data.input_tokens,
            output_tokens=data.output_tokens,
            retrieval_event_ids=data.retrieval_event_ids,
            evidence_count=data.evidence_count,
            claim_labels=data.claim_labels,
            verification_result=verification_summary(data.claim_labels),
            safety_action=data.safety_action,
            status=data.status,
            question_chars=data.question_chars,
        ))
        db.commit()
    except Exception as exc:  # noqa: BLE001 - see the docstring
        db.rollback()
        logger.warning("ai_trace_write_failed", extra={"error_type": type(exc).__name__})
