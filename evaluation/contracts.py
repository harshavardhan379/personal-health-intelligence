"""What a system under test receives and returns (framework section 8).

Two deliberate differences from the framework's first sketch, found while implementing it:

  * A system is given an `AskedQuestion` (id and text) and NEVER the question record. The
    record holds the expected answers; handing it over would let a system read the key.
  * A system is given a `PatientRecord` that carries the patient's documents and, only in
    ORACLE mode, the ground-truth events. In EXTRACTED mode `oracle_events` is None, so a
    system that wants events has to extract them from the documents itself.

`SystemAnswer.claim_labels` is optional and set only by a system that verifies its own
claims (the Full system); it exists so the benchmark can measure how well that verifier
catches claims that are really unsupported.
"""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal, Optional, Protocol

Mode = Literal["ORACLE", "EXTRACTED"]
MODES = ("ORACLE", "EXTRACTED")


@dataclass(frozen=True)
class PredictedEvent:
    """An event as a pipeline produced it. event_date is None for an undated event."""

    event_type: str
    event_date: Optional[date]
    date_precision: str
    data: dict


@dataclass(frozen=True)
class AskedQuestion:
    question_id: str
    text: str


@dataclass(frozen=True)
class DocumentInput:
    filename: str
    pdf_path: Path
    text_path: Path

    def pdf_bytes(self) -> bytes:
        return self.pdf_path.read_bytes()

    def pages(self) -> list:
        """Page texts, as in the dataset's .txt file (pages separated by a form-feed)."""
        return self.text_path.read_text(encoding="utf-8").split("\f")


@dataclass(frozen=True)
class PatientRecord:
    patient_id: str
    documents: tuple  # DocumentInput, in upload order
    oracle_events: Optional[tuple] = None  # ground-truth events; ORACLE mode only


@dataclass
class EvidenceItem:
    event_ids: list = field(default_factory=list)  # empty for raw-text chunks (Naive RAG)
    text: str = ""


@dataclass
class AnswerClaim:
    text: str
    evidence_ids: list = field(default_factory=list)


@dataclass
class SystemAnswer:
    answer: str
    claims: list = field(default_factory=list)  # AnswerClaim
    evidence: list = field(default_factory=list)  # EvidenceItem, ranked as retrieved
    abstained: bool = False  # EXPLICIT (framework decision 6)
    safety_action: Optional[str] = None  # only the Full system sets this
    claim_labels: Optional[list] = None  # per-claim verifier label, if the system verifies


class System(Protocol):
    name: str

    def answer(self, patient: PatientRecord, question: AskedQuestion, mode: str) -> SystemAnswer:
        ...
