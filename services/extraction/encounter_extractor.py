"""Encounter extraction (YGH-43).

Expected synthetic format:
    Encounter: Annual Physical at Riverside Clinic reason: routine checkup 2026-01-10
"""

import re
from datetime import date

from packages.schemas.extraction import Encounter, ExtractedFact

_ENCOUNTER_PATTERN = re.compile(
    r"Encounter:\s*(?P<type>[A-Za-z][A-Za-z0-9 \-]{1,50}?)"
    r"(?:\s+at\s+(?P<facility>[A-Za-z0-9 \-]{1,60}?))?"
    r"(?:\s+reason:\s*(?P<reason>[A-Za-z0-9 \-]{1,80}?))?\s*"
    r"(?P<date>\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def extract(pages: list) -> list[ExtractedFact]:
    facts: list[ExtractedFact] = []
    for page in pages:
        for match in _ENCOUNTER_PATTERN.finditer(page.text):
            try:
                event_date = date.fromisoformat(match.group("date"))
            except ValueError:
                continue

            encounter = Encounter(
                encounter_type=match.group("type").strip(),
                provider_or_facility=(match.group("facility") or "").strip() or None,
                reason=(match.group("reason") or "").strip() or None,
            )
            facts.append(
                ExtractedFact(
                    event_type="encounter",
                    event_date=event_date,
                    data=encounter.model_dump(),
                    confidence=0.7,
                    source_page=page.page,
                    quote=match.group(0).strip(),
                )
            )
    return facts
