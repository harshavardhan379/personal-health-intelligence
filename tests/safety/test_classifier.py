"""Safety classifier regression tests (YGH-59, YGH-61, YGH-62)."""

import pytest

from packages.schemas.safety import ALL_CATEGORIES
from services.safety.classifier import classify_safety
from services.safety.gate import evaluate_question
from tests.safety.cases import CLASSIFIER_CASES, SAFE_PROBES, UNSAFE_PROBES


@pytest.mark.parametrize("question,expected", CLASSIFIER_CASES)
def test_question_maps_to_expected_category(question, expected):
    assert classify_safety(question).category == expected, question


def test_every_category_in_the_taxonomy_is_exercised_by_a_case():
    covered = {category for _, category in CLASSIFIER_CASES}
    assert covered == set(ALL_CATEGORIES)


def test_most_dangerous_signal_wins_when_a_question_has_several():
    # A treatment question that also mentions a result must still be a treatment
    # question, and an emergency outranks everything.
    assert classify_safety("My HbA1c is 7.2, should I stop my medicine?").category == (
        "TREATMENT_REQUEST"
    )
    assert classify_safety("I took too many pills, should I stop taking them?").category == (
        "EMERGENCY_REQUEST"
    )


def test_classification_is_case_and_whitespace_insensitive():
    assert classify_safety("  DO   I HAVE   CANCER?  ").category == "DIAGNOSIS_REQUEST"
    assert classify_safety("Should I stop taking this medicine’s dose?").category in (
        "TREATMENT_REQUEST",
        "PRESCRIPTION_REQUEST",
    )


def test_classifier_records_a_rule_name_not_the_question():
    result = classify_safety("Do I have cancer?")
    assert result.rule
    assert "cancer" not in result.rule


_ANSWERED = ("ALLOW", "ALLOW_WITH_UNCERTAINTY")


@pytest.mark.parametrize("question", UNSAFE_PROBES)
def test_unsafe_probe_is_never_answered(question):
    assert evaluate_question(question).action not in _ANSWERED, question


@pytest.mark.parametrize("question", SAFE_PROBES)
def test_legitimate_records_question_is_answered(question):
    """Over-blocking is a product failure too: a safety layer that refuses
    everything is not a safe assistant, it is a useless one."""
    assert evaluate_question(question).action in _ANSWERED, question
