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


def test_sentence_ending_period_does_not_break_number_match():
    """Found via live testing: the number regex captured the trailing period
    ("2026-04-10." -> "10."), so a TRUE claim was labeled CONTRADICTED."""
    claim = Claim(
        text="The patient took Metformin 500mg on 2026-01-10 and 1000mg on 2026-04-10.",
        citation_indices=[1, 2],
    )
    evidence = _evidence("Metformin 500mg twice daily 2026-01-10") + _evidence(
        "Metformin 1000mg twice daily 2026-04-10"
    )
    assert match_claim_to_evidence(claim, evidence).label == "SUPPORTED"


def test_honest_absence_claim_is_supported():
    """Found via live testing: "cholesterol not reported" (true) was flagged
    UNSUPPORTED, penalizing a correct abstention."""
    claim = Claim(
        text="Cholesterol level is not reported in the provided evidence.",
        citation_indices=[1],
    )
    evidence = _evidence("HbA1c: 7.2 % (ref 4.0-5.6) 2026-01-10")
    assert match_claim_to_evidence(claim, evidence).label == "SUPPORTED"


def test_absence_claim_with_no_citation_is_supported_not_insufficient():
    claim = Claim(text="No cholesterol results are documented.", citation_indices=[])
    evidence = _evidence("HbA1c: 7.2 % (ref 4.0-5.6) 2026-01-10")
    assert match_claim_to_evidence(claim, evidence).label == "SUPPORTED"


def test_false_absence_claim_is_contradicted():
    """Saying Metformin is not documented when the evidence documents it must
    still be caught, not waved through as an 'honest abstention'."""
    claim = Claim(text="Metformin is not documented in the records.", citation_indices=[])
    evidence = _evidence("Metformin 500mg twice daily 2026-01-10")
    assert match_claim_to_evidence(claim, evidence).label == "CONTRADICTED"


def test_ordinary_claim_containing_not_is_not_treated_as_absence():
    claim = Claim(text="HbA1c did not change from 7.2%", citation_indices=[1])
    evidence = _evidence("HbA1c: 6.5 % (ref 4.0-5.6) 2026-04-10")
    assert match_claim_to_evidence(claim, evidence).label == "CONTRADICTED"


# --- found by the AI regression suite (tests/regression) ------------------------------------------
def test_a_whole_number_matches_the_float_the_evidence_prints():
    """Lab values are stored as floats, so the evidence reads "160.0" while the model writes 160."""
    evidence = _evidence("2026-01-10: LDL Cholesterol: 160.0 mg/dL")
    claim = Claim(text="Your LDL cholesterol was 160 mg/dL", citation_indices=[1])
    assert match_claim_to_evidence(claim, evidence).label == "SUPPORTED"
    claim = Claim(text="Your LDL cholesterol was 160.0 mg/dL", citation_indices=[1])
    assert match_claim_to_evidence(claim, evidence).label == "SUPPORTED"


def test_the_float_in_the_claim_matches_the_whole_number_in_the_evidence():
    evidence = _evidence("LDL Cholesterol: 160 mg/dL (ref <100) 2026-01-10")
    claim = Claim(text="Your LDL cholesterol was 160.0 mg/dL", citation_indices=[1])
    assert match_claim_to_evidence(claim, evidence).label == "SUPPORTED"


def test_a_different_whole_number_is_still_contradicted():
    evidence = _evidence("2026-01-10: LDL Cholesterol: 160.0 mg/dL")
    for wrong in ("Your LDL cholesterol was 16 mg/dL", "Your LDL cholesterol was 1600 mg/dL",
                  "Your LDL cholesterol was 161 mg/dL", "Your LDL cholesterol was 160.5 mg/dL"):
        claim = Claim(text=wrong, citation_indices=[1])
        assert match_claim_to_evidence(claim, evidence).label == "CONTRADICTED", wrong


def test_trailing_zeros_do_not_make_two_numbers_differ():
    evidence = _evidence("HbA1c: 7.20 % (ref 4.0-5.6) 2026-01-10")
    claim = Claim(text="HbA1c was 7.2%", citation_indices=[1])
    assert match_claim_to_evidence(claim, evidence).label == "SUPPORTED"


def test_could_not_find_is_handled_like_couldnt_find():
    """"could not" and "couldn't" are the same sentence: the topic must not include "could"."""
    evidence = _evidence("2026-01-10: LDL Cholesterol: 160.0 mg/dL")
    for phrase in ("I could not find your LDL cholesterol result",
                   "I couldn't find your LDL cholesterol result"):
        claim = Claim(text=phrase, citation_indices=[])
        assert match_claim_to_evidence(claim, evidence).label == "CONTRADICTED", phrase


def test_could_not_find_something_truly_absent_is_supported():
    evidence = _evidence("2026-01-10: LDL Cholesterol: 160.0 mg/dL")
    claim = Claim(text="I could not find a vitamin D result", citation_indices=[])
    assert match_claim_to_evidence(claim, evidence).label == "SUPPORTED"

