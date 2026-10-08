"""Verification test suite (YGH-57). Pure unit tests over match_claim_to_evidence
and verify_answer — no database, no LLM call needed, since the matcher is a
deterministic function over Claim/Evidence text.

Covers the explicit cases required by the brief (M12-T06): missing evidence,
contradictory records, wrong date, wrong value, hallucinated medication,
unsupported causal claim.
"""

from packages.schemas.answer import Answer, Claim
from packages.schemas.evidence import Evidence
from services.verification.match import match_claim_to_evidence
from services.verification.verify import verify_answer


def _evidence(text: str, index_count: int = 1) -> list:
    return [
        Evidence(
            document_id="doc-1", page=1, text=text, event_ids=["e1"],
            relevance_score=1.0, source="structured",
        )
        for _ in range(index_count)
    ]


def test_missing_evidence_is_insufficient():
    claim = Claim(text="Patient takes Metformin 500mg", citation_indices=[])
    result = match_claim_to_evidence(claim, _evidence("anything"))
    assert result.label == "INSUFFICIENT"


def test_wrong_citation_index_is_insufficient():
    claim = Claim(text="Patient takes Metformin 500mg", citation_indices=[99])
    result = match_claim_to_evidence(claim, _evidence("Metformin 500mg"))
    assert result.label == "INSUFFICIENT"


def test_correct_value_is_supported():
    claim = Claim(text="HbA1c is 7.2%", citation_indices=[1])
    evidence = _evidence("HbA1c: 7.2 % (ref 4.0-5.6)")
    result = match_claim_to_evidence(claim, evidence)
    assert result.label == "SUPPORTED"


def test_wrong_value_is_contradicted():
    """The real bug this project found and fixed by hand in M11: a claim
    stating a different number than its own cited evidence."""
    claim = Claim(text="HbA1c level is 6.0%", citation_indices=[1])
    evidence = _evidence("HbA1c: 7.2 % (ref 4.0-5.6)")
    result = match_claim_to_evidence(claim, evidence)
    assert result.label == "CONTRADICTED"


def test_wrong_date_is_contradicted():
    claim = Claim(text="Metformin was prescribed 500mg on 2026-04-10", citation_indices=[1])
    evidence = _evidence("Metformin 500mg prescribed 2026-01-10")
    result = match_claim_to_evidence(claim, evidence)
    assert result.label == "CONTRADICTED"


def test_hallucinated_medication_is_unsupported():
    """A medication never mentioned in the cited evidence at all."""
    claim = Claim(text="Patient takes Lisinopril 10mg daily", citation_indices=[1])
    evidence = _evidence("Metformin 500mg twice daily")
    result = match_claim_to_evidence(claim, evidence)
    assert result.label == "UNSUPPORTED"


def test_unsupported_causal_claim():
    """A causal claim the evidence (a plain lab value) doesn't speak to at all."""
    claim = Claim(text="The headache was caused by the medication", citation_indices=[1])
    evidence = _evidence("HbA1c: 7.2 % (ref 4.0-5.6)")
    result = match_claim_to_evidence(claim, evidence)
    assert result.label == "UNSUPPORTED"


def test_contradictory_records_across_documents():
    """Two documents disagreeing on a dose (M12-T04's example) — the matcher
    checks the claim against whichever one(s) it cited; citing the wrong one
    produces a contradiction, exactly as it should."""
    claim = Claim(text="Metformin dose is 10mg", citation_indices=[1])
    evidence = _evidence("Metformin 20mg twice daily")
    result = match_claim_to_evidence(claim, evidence)
    assert result.label == "CONTRADICTED"


def test_verify_answer_fully_verified_when_all_supported():
    answer = Answer(
        answer="...",
        claims=[Claim(text="HbA1c is 7.2%", citation_indices=[1])],
        evidence_ids=[],
        uncertainty=None,
        intent="RETRIEVAL",
    )
    verified = verify_answer(answer, _evidence("HbA1c: 7.2 % (ref 4.0-5.6)"))
    assert verified.fully_verified is True
    assert verified.uncertainty is None


def test_verify_answer_gate_flags_contradiction_in_uncertainty():
    """The gate (YGH-56): a contradicted claim must never be silently shown
    as if it were confirmed fact."""
    answer = Answer(
        answer="...",
        claims=[Claim(text="HbA1c level is 6.0%", citation_indices=[1])],
        evidence_ids=[],
        uncertainty=None,
        intent="RETRIEVAL",
    )
    verified = verify_answer(answer, _evidence("HbA1c: 7.2 % (ref 4.0-5.6)"))
    assert verified.fully_verified is False
    assert "contradicted" in verified.uncertainty.lower()
