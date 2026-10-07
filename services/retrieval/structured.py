"""Structured retrieval over confirmed health events (YGH-76)."""

import re
import uuid

from sqlalchemy.orm import Session

from packages.schemas.evidence import Evidence
from packages.schemas.models import HealthEvent
from packages.schemas.queries import CONFIRMED_STATUSES

# Common words that carry no retrieval signal on their own ("what is my dose?").
# Filtering them out is what lets a full natural-language question still match
# single-keyword event data (found via YGH-82 testing: the original whole-string
# substring check never matched a real question, only a single keyword).
_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "my", "me", "i", "what",
    "when", "where", "how", "do", "does", "did", "of", "for", "to", "in",
    "on", "at", "and", "or", "this", "that", "has", "have", "had", "it",
}
_WORD_RE = re.compile(r"[a-z0-9]+")


def _keywords(query: str) -> list[str]:
    tokens = _WORD_RE.findall(query.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


def search_structured(
    db: Session, user_id: uuid.UUID, query: str, limit: int = 10
) -> list[Evidence]:
    """Token-overlap match against event_type and the JSON data payload's text-ish
    fields — any significant word from the query matching is enough, not the whole
    query as one substring (a real question almost never appears verbatim in a
    single event's data). No ORM-level JSON text search is assumed portable, so
    this filters in Python over the user's confirmed events — fine at MVP scale,
    revisit if it's ever slow.
    """
    events = (
        db.query(HealthEvent)
        .filter(
            HealthEvent.user_id == user_id,
            HealthEvent.review_status.in_(CONFIRMED_STATUSES),
        )
        .all()
    )

    query_keywords = set(_keywords(query))
    if not query_keywords:
        return []

    scored_matches = []
    for event in events:
        haystack = (" ".join(str(v) for v in event.data.values()) + " " + event.event_type).lower()
        hits = sum(1 for kw in query_keywords if kw in haystack)
        if hits > 0:
            scored_matches.append((hits / len(query_keywords), event))

    scored_matches.sort(key=lambda pair: pair[0], reverse=True)

    evidence = []
    for score, event in scored_matches[:limit]:
        provenance = event.provenance[0] if event.provenance else None
        evidence.append(
            Evidence(
                document_id=str(event.document_id),
                page=provenance.page_number if provenance else 0,
                text=provenance.quote if provenance else str(event.data),
                event_ids=[str(event.id)],
                relevance_score=score,  # fraction of query keywords matched
                source="structured",
            )
        )
    return evidence
