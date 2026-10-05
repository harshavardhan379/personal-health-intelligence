"""Runs all extractors over a normalized document, validates the results, and
returns facts ready to become HealthEvent + Provenance rows (YGH-46, YGH-47).
"""


from packages.schemas.extraction import ExtractedFact
from services.extraction import (
    encounter_extractor,
    lab_extractor,
    medication_extractor,
    symptom_extractor,
)
from services.extraction.validate import validate_facts

_EXTRACTORS = [
    lab_extractor.extract,
    medication_extractor.extract,
    encounter_extractor.extract,
    symptom_extractor.extract,
]


def extract_all(pages: list) -> list[ExtractedFact]:
    facts: list[ExtractedFact] = []
    for extractor in _EXTRACTORS:
        facts.extend(extractor(pages))
    return validate_facts(facts)
