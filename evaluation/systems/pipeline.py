"""Running the product's REAL extraction pipeline on a patient's documents.

This is an adapter, so it is allowed to import the system under test (the scorers are not).
It does what the product does when a user uploads documents and confirms everything:

  extract_events   PDF -> text (services/document_processing) -> facts (services/extraction)
  build_timeline   the product's timeline: events ordered by date, then de-duplicated by the
                   product's own identity key, keeping the first occurrence of each

EXTRACTED mode means "no human reviewer": every extracted event is treated as confirmed.
"""

from evaluation.contracts import PatientRecord, PredictedEvent
from services.api.app.api.v1.timeline import _dedup_key
from services.document_processing.normalize import ProcessingError, process_document
from services.extraction.pipeline import extract_all


def extract_events(patient: PatientRecord) -> list:
    """What the pipeline extracts from every document, in upload order."""
    events = []
    for document in patient.documents:
        try:
            normalized = process_document(document.filename, "application/pdf",
                                          document.pdf_bytes())
        except ProcessingError:
            continue  # an unparseable document yields nothing, exactly as in the product
        for fact in extract_all(normalized.pages):
            events.append(PredictedEvent(fact.event_type, fact.event_date,
                                         fact.event_date_precision, dict(fact.data)))
    return events


def build_timeline(events: list) -> list:
    """The product's timeline over `events`: date order, first occurrence of each identity."""
    ordered = sorted(events, key=lambda e: (e.event_date is None, e.event_date))
    seen, timeline = set(), []
    for event in ordered:
        key = _dedup_key(event)
        if key in seen:
            continue
        seen.add(key)
        timeline.append(event)
    return timeline
