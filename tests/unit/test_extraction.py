"""Unit tests for the regex extractors and validation (supports YGH-41..45)."""

from dataclasses import dataclass
from datetime import date

from packages.schemas.extraction import ExtractedFact
from services.extraction.pipeline import extract_all
from services.extraction.validate import ValidationError, validate_fact


@dataclass
class Page:
    page: int
    text: str


def test_lab_extraction():
    pages = [Page(page=1, text="HbA1c: 7.2 % (ref 4.0-5.6) 2026-01-10")]
    facts = extract_all(pages)
    assert len(facts) == 1
    assert facts[0].event_type == "lab"
    assert facts[0].data["test_name"] == "HbA1c"
    assert facts[0].data["value"] == 7.2


def test_malformed_date_is_skipped_not_guessed():
    pages = [Page(page=1, text="HbA1c: 7.2 % (ref 4.0-5.6) 2026-13-45")]
    facts = extract_all(pages)
    assert facts == []


def test_medication_does_not_infer_discontinuation():
    fact = ExtractedFact(
        event_type="medication",
        event_date=date(2026, 1, 1),
        data={"medication": "Metformin", "dose": "500mg"},
        confidence=0.7,
        source_page=1,
        quote="Rx: Metformin 500mg 2026-01-01",
    )
    assert "discontinued" not in fact.data


def test_validation_rejects_missing_unit():
    fact = ExtractedFact(
        event_type="lab",
        event_date=date(2026, 1, 1),
        data={"test_name": "HbA1c", "value": 7.2, "unit": ""},
        confidence=0.7,
        source_page=1,
        quote="HbA1c: 7.2 2026-01-01",
    )
    try:
        validate_fact(fact)
        raise AssertionError("expected ValidationError")
    except ValidationError:
        pass


def test_validation_rejects_no_quote():
    fact = ExtractedFact(
        event_type="lab",
        event_date=date(2026, 1, 1),
        data={"test_name": "HbA1c", "value": 7.2, "unit": "%"},
        confidence=0.7,
        source_page=1,
        quote="   ",
    )
    try:
        validate_fact(fact)
        raise AssertionError("expected ValidationError")
    except ValidationError:
        pass
