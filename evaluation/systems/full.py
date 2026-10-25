"""The full system (YGH-71): the product's own answer pipeline, run on benchmark patients.

This is the product, not a re-implementation of it. For each patient and mode the patient's
events are stored as CONFIRMED health events (with provenance and embeddings, through the
product's own `sync_event_embedding`) in a dedicated benchmark database, and each question goes
through the product's functions in the product's order:

    evaluate_question -> classify_intent -> (blocked? fixed refusal)
        -> build_context (intent routing + hybrid retrieval) -> LLM -> verify_answer -> guard_output

`run_product_pipeline` repeats the five lines of `generate_verified_answer` because the
benchmark needs the retrieved evidence list too, which that function does not return; a test
checks the two give identical answers.

Modes. ORACLE stores the patient's ground-truth events, as a perfect reviewer would confirm
them. EXTRACTED stores what the real extraction pipeline finds, all auto-confirmed. The product
cannot store an event without a date (`health_events.event_date` is NOT NULL), so undated events
are not stored in either mode and are counted in `events_not_storable`: a real product gap that
the benchmark exposes (framework section 9), not a scoring choice.

The benchmark database. Events go to a database whose name must end in `_bench`
(BENCHMARK_DATABASE_URL, default phi_bench on the docker-compose Postgres); the code refuses any
other name, so a run can never write into the product's own database. It is created on first
use with the product's tables.

`abstained` follows the product's own logic (framework section 8): the question was blocked by
the safety layer (before or after generation), or nothing was retrieved, or the answer's only
supported claims are verified "this is not in your records" claims.
"""

import os
import re
import uuid
from collections import Counter
from typing import Callable, Optional

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from evaluation.contracts import AnswerClaim, EvidenceItem, SystemAnswer
from evaluation.systems.pipeline import extract_events
from packages.schemas.db import Base
from packages.schemas.models import Document, HealthEvent, Provenance, User
from services.api.app.core.config import settings
from services.reasoning.generate import generate_answer_with_context
from services.reasoning.intent_classifier import classify_intent
from services.retrieval.semantic import sync_event_embedding
from services.safety.gate import evaluate_question, guard_output, refusal_answer
from services.safety.policy import BLOCKING_ACTIONS
from services.verification.verify import verify_answer

DEFAULT_URL = "postgresql+psycopg://phi:phi_dev_password@localhost:5433/phi_bench"
URL_ENV = "BENCHMARK_DATABASE_URL"
_NAME = re.compile(r"[a-z0-9_]+_bench")
_NAMESPACE = uuid.UUID("5d0f6f0e-6c0e-4f3e-9d7a-1a5b0c3c9e21")

# The product's own wording for a verified "this is not in your records" claim
# (services/verification/match.py); a test keeps this in step with the verifier.
ABSENCE_EXPLANATION = "Claim states the topic is absent"


class ProviderUnavailable(RuntimeError):
    """The product swallowed an LLM failure into an apology; the benchmark counts it as an error."""


def benchmark_database_url() -> str:
    return os.environ.get(URL_ENV, DEFAULT_URL)


def check_benchmark_url(url: str) -> str:
    """The database name, which must end in `_bench`: a run never writes to the product DB."""
    name = make_url(url).database or ""
    if not _NAME.fullmatch(name):
        raise ValueError(
            f"refusing to use database {name!r}: the benchmark database name must end in "
            f"'_bench' (set {URL_ENV})")
    return name


def ensure_benchmark_database(url: str):
    """Create the benchmark database and the product's tables if missing; return an engine."""
    name = check_benchmark_url(url)
    parsed = make_url(url)
    admin = create_engine(parsed.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        exists = connection.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"),
                                    {"n": name}).scalar()
        if not exists:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    engine = create_engine(parsed, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    if _schema_drifted(engine):
        # The product's tables gained a column since this scratch database was built. It holds
        # nothing but regenerated benchmark users, so rebuild it (the name check above makes
        # this unable to touch the product database).
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
    return engine


def _schema_drifted(engine) -> bool:
    """True if any table is missing a column the product's models now have."""
    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        have = {column["name"] for column in inspector.get_columns(table.name)}
        if {column.name for column in table.columns} - have:
            return True
    return False


def store_patient(db, patient_id: str, mode: str, rows: list) -> tuple:
    """Replace this patient's benchmark user with one holding `rows` as confirmed events.

    rows: (benchmark id, PredictedEvent-like) pairs. Returns (user id, {stored event uuid ->
    benchmark id}, number of events that could not be stored)."""
    email = f"{patient_id}.{mode.lower()}@benchmark.invalid"
    user_id = uuid.uuid5(_NAMESPACE, email)
    # Remove any earlier copy, children first: provenance and embeddings point at documents
    # without a cascade, so deleting the user alone would be refused.
    for statement in (
            "DELETE FROM provenance WHERE health_event_id IN "
            "(SELECT id FROM health_events WHERE user_id = :u)",
            "DELETE FROM embeddings WHERE user_id = :u",
            "DELETE FROM health_events WHERE user_id = :u",
            "DELETE FROM documents WHERE user_id = :u",
            "DELETE FROM users WHERE id = :u"):
        db.execute(text(statement), {"u": user_id})
    db.expunge_all()  # objects of the copy just removed must not linger in the session
    user = User(id=user_id, email=email, hashed_password="!")
    db.add(user)
    db.flush()

    documents, stored, not_storable = {}, {}, 0
    for bench_id, event in rows:
        if event.event_date is None or event.date_precision == "none":
            not_storable += 1
            continue
        key = getattr(event, "document_key", None) or f"{patient_id}-upload-{event.document_index}"
        if key not in documents:
            document = Document(user_id=user.id, filename=key, mime_type="application/pdf",
                                storage_path="benchmark", processing_status="processed")
            db.add(document)
            db.flush()
            documents[key] = document
        record = HealthEvent(
            id=uuid.uuid5(_NAMESPACE, f"{email}:{bench_id}"), user_id=user.id,
            document_id=documents[key].id, event_type=event.event_type,
            event_date=event.event_date, event_date_precision=event.date_precision,
            data=dict(event.data), confidence=1.0, extraction_method="benchmark",
            review_status="confirmed")
        db.add(record)
        db.flush()
        db.add(Provenance(health_event_id=record.id, document_id=documents[key].id,
                          page_number=event.page, quote=event.quote))
        stored[str(record.id)] = (bench_id, record)
    db.flush()
    for _, record in stored.values():
        sync_event_embedding(db, record)  # the product's own step when an event is confirmed
    db.commit()
    return user.id, {uuid_text: pair[0] for uuid_text, pair in stored.items()}, not_storable


def run_product_pipeline(db, user_id, text_of_question: str) -> tuple:
    """The product's order of operations (generate_verified_answer), also returning the
    retrieved evidence. Returns (VerifiedAnswer, evidence list)."""
    decision = evaluate_question(text_of_question)
    intent = classify_intent(text_of_question)
    if decision.blocks_generation:
        return refusal_answer(decision, intent), []

    answer, context = generate_answer_with_context(db, user_id, text_of_question)
    if (answer.uncertainty or "").startswith("Provider error:"):
        raise ProviderUnavailable(answer.uncertainty)
    verified = guard_output(verify_answer(answer, context.evidence), decision, intent)
    return verified, context.evidence


def to_system_answer(verified, evidence: list, id_map: dict) -> SystemAnswer:
    """The product's answer in the benchmark's shape, with event ids as the benchmark knows them."""
    def ids_of(event_ids) -> list:
        return [id_map.get(i, f"unknown:{i}") for i in event_ids]

    claims = []
    for verification in verified.claim_verifications:
        cited = []
        for index in verification.citation_indices:
            if 1 <= index <= len(evidence):
                cited += [i for i in ids_of(evidence[index - 1].event_ids) if i not in cited]
        claims.append(AnswerClaim(text=verification.claim_text, evidence_ids=cited))

    absence = [v for v in verified.claim_verifications
               if v.label == "SUPPORTED" and (v.explanation or "").startswith(ABSENCE_EXPLANATION)]
    other_supported = [v for v in verified.claim_verifications
                       if v.label == "SUPPORTED" and v not in absence]
    abstained = (verified.safety_action in BLOCKING_ACTIONS or not evidence
                 or (bool(absence) and not other_supported))

    return SystemAnswer(
        answer=verified.answer, claims=claims,
        evidence=[EvidenceItem(event_ids=ids_of(e.event_ids), text=e.text) for e in evidence],
        abstained=abstained, safety_action=verified.safety_action,
        claim_labels=[v.label for v in verified.claim_verifications])


class FullSystem:
    name = "full"
    modes = ("ORACLE", "EXTRACTED")
    cites_events = True

    def __init__(self, database_url: Optional[str] = None,
                 extract: Optional[Callable[[object], list]] = None):
        self._url = database_url or benchmark_database_url()
        check_benchmark_url(self._url)  # refuse a wrong database before anything is touched
        self.model_name = f"{settings.llm_provider}:{settings.ollama_model}"
        self._extract = extract or extract_events
        self._sessions = None
        self._stored: dict = {}  # (patient_id, mode) -> (user id, stored uuid -> benchmark id)
        self._extracted: dict = {}  # patient_id -> [PredictedEvent]
        self.diagnostics: Counter = Counter()

    def _session(self):
        if self._sessions is None:
            self._sessions = sessionmaker(bind=ensure_benchmark_database(self._url),
                                          autoflush=False, autocommit=False)
        return self._sessions()

    def _extracted_events(self, patient) -> list:
        if patient.patient_id not in self._extracted:
            self._extracted[patient.patient_id] = list(self._extract(patient))
        return self._extracted[patient.patient_id]

    def extracted_events(self, patient) -> dict:
        """The pipeline's events under the ids this system cites them by (see citations.py)."""
        events = self._extracted_events(patient)
        return {f"x{n}": event for n, event in enumerate(events, start=1)}

    def _prepare(self, db, patient, mode: str) -> tuple:
        key = (patient.patient_id, mode)
        if key not in self._stored:
            if mode == "ORACLE":
                rows = [(e.event_id, _OracleRow(e)) for e in (patient.oracle_events or ())]
            else:
                rows = list(self.extracted_events(patient).items())
            user_id, id_map, not_storable = store_patient(db, patient.patient_id, mode, rows)
            self._stored[key] = (user_id, id_map)
            self.diagnostics[f"events_stored_{mode.lower()}"] += len(id_map)
            self.diagnostics["events_not_storable"] += not_storable
        return self._stored[key]

    def answer(self, patient, question, mode) -> SystemAnswer:
        with self._session() as db:
            user_id, id_map = self._prepare(db, patient, mode)
            verified, evidence = run_product_pipeline(db, user_id, question.text)
        result = to_system_answer(verified, evidence, id_map)
        if verified.safety_action in BLOCKING_ACTIONS:
            self.diagnostics["blocked_by_safety"] += 1
        elif not evidence:
            self.diagnostics["no_evidence"] += 1
        elif result.abstained:
            self.diagnostics["absence_abstentions"] += 1
        else:
            self.diagnostics["answered"] += 1
        return result


class _OracleRow:
    """An OracleEvent in the shape `store_patient` reads."""

    def __init__(self, event):
        self.event_type, self.event_date = event.event_type, event.event_date
        self.date_precision, self.data = event.date_precision, event.data
        self.document_key, self.page, self.quote = event.document_id, event.page, event.quote
        self.document_index = 0
