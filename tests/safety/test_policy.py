"""Safe response policy tests (YGH-60)."""

import pytest

from packages.schemas.safety import ALL_CATEGORIES
from services.safety.output_guard import check_text
from services.safety.policy import (
    BLOCKING_ACTIONS,
    CLARIFY_MESSAGE,
    POLICY,
    REFUSAL_MESSAGES,
    action_for,
    message_for,
)

VALID_ACTIONS = {
    "ALLOW",
    "ALLOW_WITH_UNCERTAINTY",
    "CLARIFY",
    "ABSTAIN",
    "REDIRECT_TO_PROFESSIONAL",
}
DANGEROUS = [
    "HIGH_RISK_MEDICAL",
    "DIAGNOSIS_REQUEST",
    "PRESCRIPTION_REQUEST",
    "TREATMENT_REQUEST",
    "EMERGENCY_REQUEST",
]


def test_every_category_has_a_valid_action():
    assert set(POLICY) == set(ALL_CATEGORIES)
    assert set(POLICY.values()) <= VALID_ACTIONS


@pytest.mark.parametrize("category", DANGEROUS)
def test_dangerous_categories_never_map_to_an_allowing_action(category):
    assert action_for(category) in BLOCKING_ACTIONS
    assert action_for(category) not in ("ALLOW", "ALLOW_WITH_UNCERTAINTY")


def test_only_personal_history_is_allowed_outright():
    assert [c for c, a in POLICY.items() if a == "ALLOW"] == ["PERSONAL_HISTORY"]


@pytest.mark.parametrize("category", DANGEROUS)
def test_every_blocking_category_has_a_refusal_message(category):
    assert REFUSAL_MESSAGES[category].strip()
    assert message_for(category, action_for(category)) == REFUSAL_MESSAGES[category]


def test_emergency_message_names_emergency_services_and_a_crisis_line():
    text = REFUSAL_MESSAGES["EMERGENCY_REQUEST"].lower()
    assert "emergency" in text
    assert "911" in text
    assert "988" in text


@pytest.mark.parametrize(
    "category",
    ["DIAGNOSIS_REQUEST", "PRESCRIPTION_REQUEST", "TREATMENT_REQUEST", "HIGH_RISK_MEDICAL"],
)
def test_non_emergency_refusals_point_to_a_human(category):
    text = REFUSAL_MESSAGES[category].lower()
    assert any(w in text for w in ("clinician", "doctor", "prescriber", "pharmacist"))


def test_refusals_say_what_the_assistant_can_still_do():
    for category in ("DIAGNOSIS_REQUEST", "PRESCRIPTION_REQUEST", "TREATMENT_REQUEST"):
        assert "records" in REFUSAL_MESSAGES[category].lower()


def test_no_canned_message_itself_diagnoses_prescribes_or_directs_treatment():
    """The safety layer's own wording is held to the same standard as the model's."""
    for text in list(REFUSAL_MESSAGES.values()) + [CLARIFY_MESSAGE]:
        assert check_text(text) is None, text
