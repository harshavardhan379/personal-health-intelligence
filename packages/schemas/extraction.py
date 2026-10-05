"""Extraction schemas (YGH-40). Pydantic models for each event type's `data` payload.

These are the shape stored in HealthEvent.data (JSONB), and the contract between
the extractors (services/extraction/*) and everything downstream.
"""

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class ExtractedFact(BaseModel):
    """Common envelope every extractor returns, before it becomes a HealthEvent row."""

    event_type: str
    event_date: date
    event_date_precision: str = "day"  # day|month|year
    data: dict
    confidence: float = Field(ge=0.0, le=1.0)
    source_page: int
    quote: str


class LabResult(BaseModel):
    test_name: str
    value: float
    unit: str
    reference_range: Optional[str] = None


class MedicationEvent(BaseModel):
    medication: str
    dose: Optional[str] = None
    frequency: Optional[str] = None
    # Deliberately no "discontinued" field: absence of a mention is not evidence
    # of discontinuation (brief, M6-T03). Discontinuation must be its own
    # explicitly-documented event.


class Encounter(BaseModel):
    encounter_type: str
    provider_or_facility: Optional[str] = None
    reason: Optional[str] = None


class SymptomEvent(BaseModel):
    symptom: str
    detail: Optional[str] = None
