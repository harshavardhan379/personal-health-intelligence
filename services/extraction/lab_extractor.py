"""Lab result extraction (YGH-41).

Regex-based extractor over normalized page text. This is a deliberate placeholder
for an LLM-based extractor (same reasoning as services/document_processing/classify.py):
it works offline, is cheap, and is good enough to exercise the rest of the pipeline
(review, timeline, trends, retrieval) honestly. Swap the implementation behind
`extract(pages) -> list[ExtractedFact]` later; nothing else needs to change.

Expected synthetic format (what the benchmark's synthetic patients will use):
    HbA1c: 7.2 % (ref 4.0-5.6) 2026-01-10
"""

import re
from datetime import date

from packages.schemas.extraction import ExtractedFact, LabResult

# TestName: value unit (ref range) YYYY-MM-DD
_LAB_PATTERN = re.compile(
    r"(?P<test>[A-Za-z][A-Za-z0-9 /%\-]{1,40}?):\s*"
    r"(?P<value>-?\d+(?:\.\d+)?)\s*(?P<unit>%|mg/dL|mmol/L|g/dL|mmHg|bpm|IU/L|ng/mL)\s*"
    r"(?:\(ref\s*(?P<range>[^)]+)\))?\s*"
    r"(?P<date>\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def extract(pages: list) -> list[ExtractedFact]:
    """`pages` is a list of services.document_processing.normalize.NormalizedPage."""
    facts: list[ExtractedFact] = []

    for page in pages:
        for match in _LAB_PATTERN.finditer(page.text):
            try:
                event_date = date.fromisoformat(match.group("date"))
            except ValueError:
                continue  # malformed date: skip rather than guess (YGH-45)

            lab = LabResult(
                test_name=match.group("test").strip(),
                value=float(match.group("value")),
                unit=match.group("unit"),
                reference_range=match.group("range"),
            )
            facts.append(
                ExtractedFact(
                    event_type="lab",
                    event_date=event_date,
                    data=lab.model_dump(),
                    confidence=0.75,
                    source_page=page.page,
                    quote=match.group(0).strip(),
                )
            )

    return facts
