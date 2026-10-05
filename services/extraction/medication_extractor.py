"""Medication extraction (YGH-42). See lab_extractor.py for why this is regex-based.

Expected synthetic format:
    Rx: Metformin 500mg twice daily 2026-01-10
"""

import re
from datetime import date

from packages.schemas.extraction import ExtractedFact, MedicationEvent

_MED_PATTERN = re.compile(
    r"Rx:\s*(?P<medication>[A-Za-z][A-Za-z0-9 \-]{1,60}?)\s+"
    r"(?P<dose>\d+(?:\.\d+)?\s*(?:mg|mcg|g|mL|IU))\s*"
    r"(?P<frequency>[a-zA-Z ]{0,30}?daily|once|twice|[a-zA-Z ]{0,30}?weekly)?\s*"
    r"(?P<date>\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def extract(pages: list) -> list[ExtractedFact]:
    facts: list[ExtractedFact] = []
    for page in pages:
        for match in _MED_PATTERN.finditer(page.text):
            try:
                event_date = date.fromisoformat(match.group("date"))
            except ValueError:
                continue

            med = MedicationEvent(
                medication=match.group("medication").strip(),
                dose=match.group("dose"),
                frequency=(match.group("frequency") or "").strip() or None,
            )
            facts.append(
                ExtractedFact(
                    event_type="medication",
                    event_date=event_date,
                    data=med.model_dump(),
                    confidence=0.7,
                    source_page=page.page,
                    quote=match.group(0).strip(),
                )
            )
    return facts
