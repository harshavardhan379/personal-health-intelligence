"""Ground-truth data structures (framework section 8, dataset spec "Dataset layout").

Plain dataclasses with explicit to_dict() so the JSON is deterministic: dates are ISO
strings, and callers dump with sort_keys=True. Invariants are checked at construction, so
a malformed ground-truth event cannot be created, let alone written to disk.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

EVENT_TYPES = ("lab", "medication", "encounter", "symptom")
DATE_PRECISIONS = ("day", "month", "year", "none")
DOC_TYPES = ("lab_report", "prescription", "visit_note", "discharge_summary", "combined")
LAYOUTS = ("key_value", "table", "prose")
SPLITS = ("dev", "test")

# The field that identifies WHAT an event is, per type (framework 3.1 match rule).
KEY_FIELD = {
    "lab": "test_name",
    "medication": "medication",
    "encounter": "encounter_type",
    "symptom": "symptom",
}


@dataclass(frozen=True)
class EventSource:
    """Where in the inputs a fact can be found (gold evidence, framework sections 4 and 6)."""

    document_id: str
    page: int
    quote: str

    def to_dict(self) -> dict:
        return {"document_id": self.document_id, "page": self.page, "quote": self.quote}


@dataclass
class Event:
    event_id: str
    event_type: str
    event_date: Optional[date]
    date_precision: str
    data: dict
    source: Optional[EventSource] = None
    degradations: list = field(default_factory=list)
    conflicts_with: list = field(default_factory=list)
    duplicate_of: Optional[str] = None

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event_type {self.event_type!r}")
        if self.date_precision not in DATE_PRECISIONS:
            raise ValueError(f"unknown date_precision {self.date_precision!r}")
        # An undated event has no date; a dated one must have one (decision 7).
        if (self.date_precision == "none") != (self.event_date is None):
            raise ValueError("event_date must be None exactly when date_precision is 'none'")
        # month / year events are stored on the first day of the period, so the date and
        # its precision can never disagree.
        if self.date_precision == "month" and self.event_date and self.event_date.day != 1:
            raise ValueError("month-precision date must be the 1st of the month")
        if self.date_precision == "year" and self.event_date and (
            self.event_date.month != 1 or self.event_date.day != 1
        ):
            raise ValueError("year-precision date must be January 1st")
        if KEY_FIELD[self.event_type] not in self.data:
            raise ValueError(f"{self.event_type} event is missing {KEY_FIELD[self.event_type]!r}")

    @property
    def key(self) -> str:
        return str(self.data[KEY_FIELD[self.event_type]])

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "event_date": self.event_date.isoformat() if self.event_date else None,
            "date_precision": self.date_precision,
            "data": self.data,
            "source": self.source.to_dict() if self.source else None,
            "degradations": sorted(self.degradations),
            "conflicts_with": sorted(self.conflicts_with),
            "duplicate_of": self.duplicate_of,
        }


@dataclass
class DocumentMeta:
    document_id: str
    doc_type: str
    document_date: Optional[date]
    layout: str
    date_style: str
    header_date_present: bool
    pages: int
    filename: str
    duplicate_of: Optional[str] = None

    def __post_init__(self) -> None:
        if self.doc_type not in DOC_TYPES:
            raise ValueError(f"unknown doc_type {self.doc_type!r}")
        if self.layout not in LAYOUTS:
            raise ValueError(f"unknown layout {self.layout!r}")
        if self.pages < 1:
            raise ValueError("a document has at least one page")

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "doc_type": self.doc_type,
            "document_date": self.document_date.isoformat() if self.document_date else None,
            "layout": self.layout,
            "date_style": self.date_style,
            "header_date_present": self.header_date_present,
            "pages": self.pages,
            "filename": self.filename,
            "duplicate_of": self.duplicate_of,
        }


@dataclass
class PatientTruth:
    patient_id: str
    split: str
    archetype: str
    tags: dict
    events: list
    documents: list

    def __post_init__(self) -> None:
        if self.split not in SPLITS:
            raise ValueError(f"unknown split {self.split!r}")
        ids = [e.event_id for e in self.events]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate event_id in a patient")

    def to_dict(self) -> dict:
        return {
            "patient_id": self.patient_id,
            "split": self.split,
            "archetype": self.archetype,
            "tags": self.tags,
            "events": [e.to_dict() for e in self.events],
            "documents": [d.to_dict() for d in self.documents],
        }
