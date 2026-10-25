"""Temporal retrieval (YGH-85, YGH-87): evidence for "what changed" / "recent" /
"when did X start" questions.

Unlike hybrid_search (relevance-ranked), this is date-ordered chronologically
(oldest first) with the date baked directly into each Evidence's text — the
model can't reason about sequence, recency, or "when was this first
documented" (YGH-87) from unordered, undated evidence. Chronological order
serves both "what changed recently" and "when did this start" equally: the
model reads explicit dates either way, so one ordered, sufficiently-wide
evidence set covers both question shapes rather than needing two separate
retrieval paths.
"""

import re
import uuid

from sqlalchemy.orm import Session

from packages.schemas.evidence import Evidence
from packages.schemas.models import HealthEvent
from packages.schemas.queries import CONFIRMED_STATUSES
from services.retrieval.describe import describe_event

# "Latest" questions (found by using the assistant: "What is my latest HbA1c?" was answered with
# the January value because the evidence listed the whole history oldest-first and a small model
# reads the first item). They get only the facts about the topic asked, newest first.
_LATEST_WORDS = ("latest", "most recent", "most recently", "newest", "last time")
_ORIGIN_WORDS = ("first", "start", "began", "begin", "initially", "history of", "since")
_NOT_A_TOPIC = {
    "latest", "recent", "recently", "most", "newest", "last", "time", "current", "currently",
    "result", "results", "reading", "readings", "value", "values", "level", "levels", "test",
    "tests", "what", "which", "when", "was", "were", "is", "are", "did", "do", "does", "my", "me",
    "the", "an", "of", "for", "to", "in", "on", "at", "and", "or", "with", "have", "had", "has",
    "report", "reports", "record", "records", "show", "tell", "give", "list", "much", "many",
}


def wants_latest(question: str) -> bool:
    """True for "what is my latest X" and false for "when did X first appear" or "since"."""
    lowered = question.lower()
    return any(w in lowered for w in _LATEST_WORDS) and not any(
        w in lowered for w in _ORIGIN_WORDS
    )


def topic_words(question: str) -> list:
    """What the question is about ("hba1c", "metformin"), without the words that only say
    how to look it up (latest, result, my...)."""
    tokens = re.findall(r"[a-z0-9]+", question.lower())
    return [t for t in tokens if t not in _NOT_A_TOPIC and len(t) > 1]


def _haystack(event: HealthEvent) -> str:
    return (" ".join(str(v) for v in event.data.values()) + " " + event.event_type).lower()


def _evidence_for(event: HealthEvent) -> Evidence:
    provenance = event.provenance[0] if event.provenance else None
    return Evidence(
        document_id=str(event.document_id),
        page=provenance.page_number if provenance else 0,
        text=f"{event.event_date.isoformat()}: {describe_event(event)}",
        event_ids=[str(event.id)],
        relevance_score=1.0,
        source="temporal",
    )


def search_latest(
    db: Session, user_id: uuid.UUID, question: str, limit: int = 8
) -> list[Evidence]:
    """The confirmed facts about the topic asked, newest first (so the first item is the latest).

    A question naming a topic nothing matches returns NOTHING, which the assistant answers with
    its "not enough confirmed information" message, rather than handing the model unrelated
    facts to answer from. A question naming no topic ("what is my latest result?") gets the
    newest facts overall."""
    words = topic_words(question)
    events = (
        db.query(HealthEvent)
        .filter(
            HealthEvent.user_id == user_id,
            HealthEvent.review_status.in_(CONFIRMED_STATUSES),
        )
        .order_by(HealthEvent.event_date.desc())
        .all()
    )
    if words:
        events = [e for e in events if any(w in _haystack(e) for w in words)]
    return [_evidence_for(e) for e in events[:limit]]


def search_temporal(db: Session, user_id: uuid.UUID, limit: int = 20) -> list[Evidence]:
    events = (
        db.query(HealthEvent)
        .filter(
            HealthEvent.user_id == user_id,
            HealthEvent.review_status.in_(CONFIRMED_STATUSES),
        )
        .order_by(HealthEvent.event_date.asc())
        .limit(limit)
        .all()
    )

    evidence = []
    for event in events:
        provenance = event.provenance[0] if event.provenance else None
        evidence.append(
            Evidence(
                document_id=str(event.document_id),
                page=provenance.page_number if provenance else 0,
                text=f"{event.event_date.isoformat()}: {describe_event(event)}",
                event_ids=[str(event.id)],
                relevance_score=1.0,
                source="temporal",
            )
        )
    return evidence
