"""Unit tests for pure retrieval logic: ranking and embeddings (YGH-78, YGH-73)."""

from packages.schemas.evidence import Evidence
from services.retrieval.embeddings import chunk_text, embed
from services.retrieval.ranking import rank_evidence


def test_structured_outranks_semantic_at_equal_relevance():
    structured = Evidence(
        document_id="d1", page=1, text="a", relevance_score=0.9, source="structured"
    )
    semantic = Evidence(
        document_id="d2", page=1, text="b", relevance_score=0.9, source="semantic"
    )
    ranked = rank_evidence([semantic, structured])
    assert ranked[0].source == "structured"


def test_rank_respects_limit():
    items = [
        Evidence(document_id=f"d{i}", page=1, text="x", relevance_score=i / 10, source="semantic")
        for i in range(20)
    ]
    ranked = rank_evidence(items, limit=5)
    assert len(ranked) == 5
    assert ranked[0].relevance_score == max(e.relevance_score for e in items)


def test_embed_is_deterministic():
    v1 = embed("hemoglobin a1c")
    v2 = embed("hemoglobin a1c")
    assert v1 == v2


def test_embed_different_text_different_vector():
    assert embed("diabetes") != embed("completely unrelated text about shoes")


def test_chunk_text_empty():
    assert chunk_text("") == []


def test_chunk_text_splits_long_text():
    chunks = chunk_text("a" * 1200, max_chars=500)
    assert len(chunks) == 3
    assert all(len(c) <= 500 for c in chunks)
