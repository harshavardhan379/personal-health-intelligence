"""Semantic retrieval over embedded CONFIRMED health events (YGH-74, YGH-75).

Embeddings are built from confirmed events, never from raw page text. Raw text
belongs to documents whose facts a human may not have reviewed (or has rejected),
and letting it into the assistant's evidence bypassed the review gate (found via
live testing: an unreviewed cholesterol value was quoted back as fact). An event
has an embedding exactly while its review_status is confirmed or edited, kept in
step by sync_event_embedding(), which the review endpoints call.
"""

import uuid

from sqlalchemy.orm import Session

from packages.schemas.evidence import Evidence
from packages.schemas.models import Embedding, HealthEvent
from packages.schemas.queries import CONFIRMED_STATUSES
from services.retrieval.describe import describe_event
from services.retrieval.embeddings import embed


def embedding_text(event: HealthEvent) -> str:
    """What gets embedded and later shown as evidence: the dated fact, written the
    same way every other retrieval path writes it."""
    return f"{event.event_date.isoformat()}: {describe_event(event)}"


def sync_event_embedding(db: Session, event: HealthEvent) -> None:
    """Make the embedding table agree with the event's review status: one row while
    it is confirmed/edited, none otherwise. Idempotent; commits are the caller's."""
    db.query(Embedding).filter(Embedding.health_event_id == event.id).delete()

    if event.review_status not in CONFIRMED_STATUSES:
        return

    text = embedding_text(event)
    page = event.provenance[0].page_number if event.provenance else 1
    db.add(
        Embedding(
            user_id=event.user_id,
            health_event_id=event.id,
            document_id=event.document_id,
            page_number=page,
            chunk_text=text,
            vector=embed(text),
        )
    )


def search_semantic(db: Session, user_id: uuid.UUID, query: str, limit: int = 5) -> list[Evidence]:
    query_vector = embed(query)

    # pgvector's cosine_distance: 0 = identical, 2 = opposite. Rows are ordered by
    # it. The join to HealthEvent re-checks the review status as defence in depth,
    # so a stale embedding row could still never surface an unconfirmed fact.
    rows = (
        db.query(Embedding)
        .join(HealthEvent, HealthEvent.id == Embedding.health_event_id)
        .filter(
            Embedding.user_id == user_id,
            HealthEvent.user_id == user_id,
            HealthEvent.review_status.in_(CONFIRMED_STATUSES),
        )
        .order_by(Embedding.vector.cosine_distance(query_vector))
        .limit(limit)
        .all()
    )

    return [
        Evidence(
            document_id=str(row.document_id),
            page=row.page_number,
            text=row.chunk_text,
            event_ids=[str(row.health_event_id)],
            relevance_score=1.0,  # recomputed precisely in ranking.py
            source="semantic",
        )
        for row in rows
    ]
