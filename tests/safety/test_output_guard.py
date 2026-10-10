"""Output guard regression tests (YGH-61): what the model SAYS must be safe too."""

import pytest

from services.safety.output_guard import check_text
from tests.safety.cases import SAFE_OUTPUTS, UNSAFE_OUTPUTS


@pytest.mark.parametrize("text", UNSAFE_OUTPUTS)
def test_unsafe_model_output_is_flagged(text):
    assert check_text(text) is not None, text


@pytest.mark.parametrize("text", SAFE_OUTPUTS)
def test_ordinary_record_answers_are_not_flagged(text):
    assert check_text(text) is None, text


def test_apostrophe_style_does_not_bypass_the_guard():
    assert check_text("You’re fine, nothing to worry about.") is not None


def test_violation_reports_a_rule_name_not_the_text():
    violation = check_text("You should stop taking Metformin.")
    assert violation is not None
    assert violation.rule == "directs_treatment"
