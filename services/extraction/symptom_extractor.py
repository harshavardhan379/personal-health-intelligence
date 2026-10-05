"""Symptom/event extraction (YGH-44). Only explicitly documented symptoms, never inferred.

Expected synthetic format:
    Symptom: headache detail: mild, intermittent 2026-01-10
"""

import re
from datetime import date

from packages.schemas.extraction import ExtractedFact, SymptomEvent

_SYMPTOM_PATTERN = re.compile(
    r"Symptom:\s*(?P<symptom>[A-Za-z][A-Za-z0-9 \-]{1,50}?)"
    r"(?:\s+detail:\s*(?P<detail>[A-Za-z0-9 ,\-]{1,100}?))?\s*"
    r"(?P<date>\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def extract(pages: list) -> list[ExtractedFact]:
    facts: list[ExtractedFact] = []
    for page in pages:
        for match in _SYMPTOM_PATTERN.finditer(page.text):
            try:
                event_date = date.fromisoformat(match.group("date"))
            except ValueError:
                continue

            symptom = SymptomEvent(
                symptom=match.group("symptom").strip(),
                detail=(match.group("detail") or "").strip() or None,
            )
            facts.append(
                ExtractedFact(
                    event_type="symptom",
                    event_date=event_date,
                    data=symptom.model_dump(),
                    confidence=0.65,
                    source_page=page.page,
                    quote=match.group(0).strip(),
                )
            )
    return facts
