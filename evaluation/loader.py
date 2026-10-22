"""Reading a generated dataset back (used by the scorers, the runner and the tests).

A system adapter is only ever given a `PatientRecord` built by `patient_record()`: the
documents, and (ORACLE mode only) the sanitised `OracleEvent` view of the ground-truth events.
Truth and questions are read by the scorer and the runner, never passed to a system.
"""

import json
from datetime import date
from pathlib import Path

from evaluation.contracts import DocumentInput, OracleEvent, PatientRecord
from evaluation.facts import Fact
from evaluation.model import Event, EventSource
from evaluation.questions import SafetyQuestion


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def patient_ids(dataset_dir: Path, split: str = "both") -> list:
    manifest = read_json(dataset_dir / "manifest.json")
    if split == "both":
        return manifest["split"]["dev"] + manifest["split"]["test"]
    return list(manifest["split"][split])


def load_truth(dataset_dir: Path, patient_id: str) -> dict:
    return read_json(dataset_dir / "truth" / f"{patient_id}.json")


def events_from_truth(truth: dict) -> list:
    """The truth file's events as `Event` objects (invariants re-checked on load)."""
    events = []
    for raw in truth["events"]:
        source = raw.get("source")
        events.append(Event(
            event_id=raw["event_id"], event_type=raw["event_type"],
            event_date=date.fromisoformat(raw["event_date"]) if raw["event_date"] else None,
            date_precision=raw["date_precision"], data=raw["data"],
            source=EventSource(source["document_id"], source["page"], source["quote"])
            if source else None,
            degradations=list(raw["degradations"]), conflicts_with=list(raw["conflicts_with"]),
            duplicate_of=raw["duplicate_of"]))
    return events


def load_questions(dataset_dir: Path, patient_id: str) -> dict:
    """{'qa': [question dicts], 'safety': [...]} for one patient."""
    return read_json(dataset_dir / "questions" / f"{patient_id}.json")


def question_facts(question: dict) -> tuple:
    """(expected facts, forbidden facts) of a QA question dict."""
    return ([Fact.from_dict(f) for f in question["expected_facts"]],
            [Fact.from_dict(f) for f in question["forbidden_facts"]])


def safety_question(raw: dict) -> SafetyQuestion:
    return SafetyQuestion(**raw)


# Keys inside an event's data that are benchmark bookkeeping: no document contains them.
_BENCHMARK_ONLY_DATA = ("stay_id",)


def oracle_view(events: list) -> tuple:
    """The sanitised events a system may see in ORACLE mode (see contracts.OracleEvent)."""
    return tuple(
        OracleEvent(
            event_id=e.event_id, event_type=e.event_type, event_date=e.event_date,
            date_precision=e.date_precision,
            data={k: v for k, v in e.data.items() if k not in _BENCHMARK_ONLY_DATA},
            document_id=e.source.document_id, page=e.source.page, quote=e.source.quote)
        for e in events)


def patient_record(dataset_dir: Path, patient_id: str, mode: str) -> PatientRecord:
    """What a system may see for one patient. Ground-truth events only in ORACLE mode."""
    folder = dataset_dir / "inputs" / patient_id
    index = read_json(folder / "index.json")["documents"]
    documents = tuple(
        DocumentInput(entry["filename"], folder / entry["filename"],
                      folder / entry["text_filename"]) for entry in index)
    oracle = None
    if mode == "ORACLE":
        oracle = oracle_view(events_from_truth(load_truth(dataset_dir, patient_id)))
    return PatientRecord(patient_id=patient_id, documents=documents, oracle_events=oracle)
