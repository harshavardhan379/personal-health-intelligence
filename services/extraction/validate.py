"""Deterministic validation of extracted facts (YGH-45).

Runs after extraction, before anything is written to HealthEvent. Malformed
output is rejected here, never silently coerced.
"""

from datetime import date, timedelta

from packages.schemas.extraction import ExtractedFact

_MIN_DATE = date(1900, 1, 1)


class ValidationError(ValueError):
    pass


def validate_fact(fact: ExtractedFact) -> None:
    if fact.event_date < _MIN_DATE or fact.event_date > date.today() + timedelta(days=1):
        raise ValidationError(f"Implausible event date: {fact.event_date}")

    if fact.event_type == "lab":
        value = fact.data.get("value")
        if value is None or not isinstance(value, (int, float)):
            raise ValidationError("Lab result missing a numeric value")
        if not fact.data.get("unit"):
            raise ValidationError("Lab result missing a unit")
        if not fact.data.get("test_name"):
            raise ValidationError("Lab result missing a test name")

    elif fact.event_type == "medication":
        if not fact.data.get("medication"):
            raise ValidationError("Medication event missing a medication name")

    elif fact.event_type == "encounter":
        if not fact.data.get("encounter_type"):
            raise ValidationError("Encounter missing a type")

    elif fact.event_type == "symptom":
        if not fact.data.get("symptom"):
            raise ValidationError("Symptom event missing a symptom name")

    else:
        raise ValidationError(f"Unknown event_type: {fact.event_type}")

    if not fact.quote.strip():
        raise ValidationError("Fact has no source quote (no provenance possible)")


def validate_facts(facts: list[ExtractedFact]) -> list[ExtractedFact]:
    """Returns only the facts that pass validation; invalid ones are dropped
    (and would be logged by the caller) rather than crashing the whole batch.
    """
    valid = []
    for fact in facts:
        try:
            validate_fact(fact)
            valid.append(fact)
        except ValidationError:
            continue
    return valid
