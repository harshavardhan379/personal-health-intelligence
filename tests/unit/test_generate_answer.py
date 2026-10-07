"""Unit tests for generate_answer's insufficient-evidence path (YGH-88).

Pure unit test: no database, no LLM call. We patch build_context so
generate_answer's early-return branch (zero evidence -> admit it, don't guess)
is exercised deterministically. Verified live against a real local model
multiple times during M11 development (e.g. "What is my cholesterol level?"
with no matching evidence) — this formalizes that behavior as a regression test.
"""

from unittest.mock import patch

from services.reasoning.context_builder import AssistantContext
from services.reasoning.generate import generate_answer


def test_no_evidence_returns_insufficient_evidence_answer():
    empty_context = AssistantContext(
        question="What is my cholesterol level?", intent="RETRIEVAL", evidence=[]
    )

    with patch("services.reasoning.generate.build_context", return_value=empty_context):
        answer = generate_answer(db=None, user_id=None, question="What is my cholesterol level?")

    assert "don't have enough" in answer.answer.lower()
    assert answer.claims == []
    assert answer.evidence_ids == []
    assert answer.uncertainty is not None
    assert answer.intent == "RETRIEVAL"


def test_no_evidence_never_calls_the_llm_provider():
    """The cheapest, most reliable safeguard: don't even attempt generation
    when there's nothing to ground an answer in."""
    empty_context = AssistantContext(question="...", intent="UNKNOWN", evidence=[])

    with (
        patch("services.reasoning.generate.build_context", return_value=empty_context),
        patch("services.reasoning.generate.get_llm_provider") as mock_get_provider,
    ):
        generate_answer(db=None, user_id=None, question="...")
        mock_get_provider.assert_not_called()
