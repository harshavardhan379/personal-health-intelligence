"""Semantic retrieval over embedded document chunks via pgvector (YGH-74, YGH-75)."""

import uuid

from sqlalchemy.orm import Session

from packages.schemas.evidence import Evidence
from packages.schemas.models import Embedding
from services.retrieval.embeddings import embed


def search_semantic(db: Session, user_id: uuid.UUID, query: str, limit: int = 5) -> list[Evidence]:
    query_vector = embed(query)

    # pgvector's cosine_distance: 0 = identical, 2 = opposite. Convert to a
    # 0..1 relevance score for the caller.
    rows = (
        db.query(Embedding)
        .filter(Embedding.user_id == user_id)
        .order_by(Embedding.vector.cosine_distance(query_vector))
        .limit(limit)
        .all()
    )

    evidence = []
    for row in rows:
        evidence.append(
            Evidence(
                document_id=str(row.document_id),
                page=row.page_number,
                text=row.chunk_text,
                event_ids=[],
                relevance_score=1.0,  # recomputed precisely in evidence_ranking.py
                source="semantic",
            )
        )
    return evidence


def embed_document_pages(db: Session, user_id: uuid.UUID, document_id, pages) -> None:
    """Called from the ingest pipeline once a document is normalized, so its text
    becomes searchable (YGH-73). `pages` is a list of NormalizedPage.
    """
    from services.retrieval.embeddings import chunk_text

    for page in pages:
        for chunk in chunk_text(page.text):
            db.add(
                Embedding(
                    user_id=user_id,
                    document_id=document_id,
                    page_number=page.page,
                    chunk_text=chunk,
                    vector=embed(chunk),
                )
            )
