"""Explicit failure handling for every pipeline stage (YGH-95), and the policies behind it
(YGH-94: OCR timeout, storage retry, database timeouts).

Real pipeline code against an in-memory SQLite database; only the parser/OCR/extractor are
replaced where a test needs them to fail."""

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.observability import metrics
from packages.observability.context import get_request_id, set_request_id
from packages.schemas import db as db_module
from packages.schemas.db import get_db
from packages.schemas.extraction import ExtractedFact
from packages.schemas.models import Document, DocumentPage, HealthEvent, Provenance, User
from services.api.app.core import pipeline, storage
from services.api.app.core.deps import get_current_user
from services.api.main import app
from services.document_processing import normalize, ocr

SECRET = "HbA1c 9.9 patient Jane Doe"
GOOD_TEXT = "HbA1c: 7.2 % (ref 4.0-5.6) 2026-01-10"
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def world(tmp_path, monkeypatch):
    metrics.registry.reset()
    set_request_id(None)
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    for table in (User, Document, DocumentPage, HealthEvent, Provenance):
        table.__table__.create(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(pipeline, "SessionLocal", factory)
    monkeypatch.setattr(storage, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(normalize.parse, "extract_pdf_pages",
                        lambda content: [{"page": 1, "text": GOOD_TEXT}])
    session = factory()
    me = User(id=uuid.uuid4(), email="me@example.com", hashed_password="x")
    other = User(id=uuid.uuid4(), email="other@example.com", hashed_password="x")
    session.add_all([me, other])
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: me
    yield SimpleNamespace(db=session, factory=factory, me=me, other=other, root=tmp_path,
                          client=TestClient(app))
    app.dependency_overrides.clear()
    set_request_id(None)
    session.close()


def add_document(w, status="uploaded", started=None, user=None, stored=True, uploaded=None):
    user = user or w.me
    doc = Document(id=uuid.uuid4(), user_id=user.id, filename="lab.pdf",
                   mime_type="application/pdf", storage_path=f"{user.id}/{uuid.uuid4().hex}.pdf",
                   processing_status=status, processing_started_at=started,
                   uploaded_at=uploaded or NOW)
    w.db.add(doc)
    w.db.commit()
    if stored:
        target = w.root / doc.storage_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"%PDF-1.4 stand-in")
    return doc


def run(w, doc, request_id=None):
    pipeline.run_pipeline(str(doc.id), doc.mime_type, doc.storage_path, request_id)
    w.db.expire_all()
    return w.db.get(Document, doc.id)


def counts(w):
    return (w.db.query(DocumentPage).count(), w.db.query(HealthEvent).count(),
            w.db.query(Provenance).count())


# --- the success path ----------------------------------------------------------------------
def test_a_good_document_is_processed_and_everything_is_saved(world):
    doc = run(world, add_document(world))
    assert doc.processing_status == "processed"
    assert doc.failed_stage is None and doc.processing_error is None
    assert doc.classification is not None and doc.processing_started_at is not None
    assert counts(world) == (1, 1, 1)


def test_processing_and_extraction_are_timed(world):
    run(world, add_document(world))
    snapshot = metrics.registry.snapshot()
    assert snapshot["processing"]["count"] == 1 and snapshot["processing"]["errors"] == 0
    assert snapshot["extraction"]["count"] == 1


def test_a_document_that_no_longer_exists_is_ignored(world):
    ghost = SimpleNamespace(id=uuid.uuid4(), mime_type="application/pdf", storage_path="x")
    pipeline.run_pipeline(str(ghost.id), ghost.mime_type, ghost.storage_path)
    assert "processing" not in metrics.registry.snapshot()


def test_the_upload_request_id_follows_the_work_into_the_background(world, monkeypatch):
    seen = []
    real = normalize.process_document
    monkeypatch.setattr(pipeline, "process_document",
                        lambda *a: seen.append(get_request_id()) or real(*a))
    run(world, add_document(world), request_id="req-upload-1")
    assert seen == ["req-upload-1"]


# --- each stage fails on its own terms ------------------------------------------------------
def test_a_missing_file_fails_at_read_file(world):
    doc = run(world, add_document(world, stored=False))
    assert (doc.processing_status, doc.failed_stage) == ("failed", "read_file")
    assert "could not be found" in doc.processing_error
    assert counts(world) == (0, 0, 0)


def test_a_damaged_pdf_fails_at_parse_without_keeping_the_parser_text(world, monkeypatch, caplog):
    def explode(content):
        raise ValueError(SECRET)
    monkeypatch.setattr(normalize.parse, "extract_pdf_pages", explode)
    with caplog.at_level(logging.DEBUG):
        doc = run(world, add_document(world))
    assert (doc.processing_status, doc.failed_stage) == ("failed", "parse")
    assert "could not be read" in doc.processing_error
    assert SECRET not in doc.processing_error
    assert SECRET not in " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)


def test_a_file_type_nobody_can_process_fails_at_parse(world):
    doc = add_document(world)
    doc.mime_type = "text/plain"
    world.db.commit()
    doc = run(world, doc)
    assert (doc.failed_stage, doc.processing_error) == (
        "parse", "This file type cannot be processed.")


def test_a_page_that_takes_too_long_to_read_fails_at_ocr_and_says_to_try_again(world, monkeypatch):
    monkeypatch.setattr(normalize.parse, "extract_pdf_pages",
                        lambda content: [{"page": 1, "text": ""}])

    def slow(content, page):
        raise ocr.OcrTimeout("tesseract timed out")
    monkeypatch.setattr(ocr, "ocr_pdf_page", slow)
    doc = run(world, add_document(world))
    assert (doc.processing_status, doc.failed_stage) == ("failed", "ocr")
    assert "too long" in doc.processing_error and "again" in doc.processing_error


def test_an_unreadable_scan_fails_at_ocr(world, monkeypatch):
    monkeypatch.setattr(normalize.parse, "extract_pdf_pages",
                        lambda content: [{"page": 1, "text": ""}])

    def broken(content, page):
        raise RuntimeError(SECRET)
    monkeypatch.setattr(ocr, "ocr_pdf_page", broken)
    doc = run(world, add_document(world))
    assert doc.failed_stage == "ocr" and SECRET not in doc.processing_error


def test_an_unreadable_image_fails_at_ocr(world, monkeypatch):
    def broken(content):
        raise OSError(SECRET)
    monkeypatch.setattr(ocr, "ocr_image", broken)
    doc = add_document(world)
    doc.mime_type = "image/png"
    world.db.commit()
    doc = run(world, doc)
    assert (doc.failed_stage, doc.processing_error) == ("ocr", "The image could not be read.")


def test_a_classifier_failure_fails_at_classify(world, monkeypatch):
    def broken(text):
        raise RuntimeError(SECRET)
    monkeypatch.setattr(normalize, "classify", broken)
    doc = run(world, add_document(world))
    assert doc.failed_stage == "classify" and SECRET not in doc.processing_error


def test_an_extractor_failure_fails_at_extract_and_saves_nothing(world, monkeypatch):
    def broken(pages):
        raise RuntimeError(SECRET)
    monkeypatch.setattr(pipeline, "extract_all", broken)
    doc = run(world, add_document(world))
    assert (doc.processing_status, doc.failed_stage) == ("failed", "extract")
    assert SECRET not in doc.processing_error
    assert counts(world) == (0, 0, 0)
    assert metrics.registry.snapshot()["extraction"]["errors"] == 1


def test_a_failure_while_saving_rolls_back_the_pages_and_events_already_added(world, monkeypatch):
    good = ExtractedFact(event_type="lab", event_date=date(2026, 1, 10),
                         data={"test_name": "HbA1c"}, confidence=0.9, source_page=1, quote="q")
    bad = SimpleNamespace(event_type="lab", event_date=None,  # NOT NULL: the database refuses it
                          event_date_precision="day", data={"test_name": "x"}, confidence=0.9,
                          source_page=1, quote="q")
    monkeypatch.setattr(pipeline, "extract_all", lambda pages: [good, bad])
    doc = run(world, add_document(world))
    assert (doc.processing_status, doc.failed_stage) == ("failed", "save")
    assert counts(world) == (0, 0, 0), "a half-saved document must not be left behind"
    assert doc.classification is None


def test_an_unforeseen_crash_still_ends_in_a_recorded_failure(world, monkeypatch, caplog):
    def broken(*args):
        raise MemoryError(SECRET)
    monkeypatch.setattr(pipeline, "process_document", broken)
    with caplog.at_level(logging.DEBUG):
        doc = run(world, add_document(world))
    assert (doc.processing_status, doc.failed_stage) == ("failed", "unknown")
    assert SECRET not in doc.processing_error
    assert SECRET not in " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    assert metrics.registry.snapshot()["processing"]["errors"] == 1


def test_a_failure_is_logged_with_its_stage_and_class_only(world, monkeypatch, caplog):
    monkeypatch.setattr(pipeline, "extract_all", lambda pages: 1 / 0)
    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        run(world, add_document(world))
    record = next(r for r in caplog.records if r.message == "stage_failed")
    assert (record.stage, record.error_type) == ("extract", "ZeroDivisionError")
    assert all(r.exc_info is None for r in caplog.records)


def test_running_again_clears_the_earlier_failure(world):
    doc = run(world, add_document(world, stored=False))
    assert doc.failed_stage == "read_file"
    (world.root / doc.storage_path).parent.mkdir(parents=True, exist_ok=True)
    (world.root / doc.storage_path).write_bytes(b"%PDF-1.4")
    doc = run(world, doc)
    assert (doc.processing_status, doc.failed_stage, doc.processing_error) == (
        "processed", None, None)


# --- the failed run lets go of its row before the failure is recorded -----------------------
@pytest.fixture
def lock_watch(world, monkeypatch):
    """Records, at the moment a failure is written, whether the run's own session still holds an
    open transaction. On Postgres an open one holds a lock on the document row, so the failure
    write (a separate session) would wait for it until the statement timeout."""
    sessions, held = [], []

    def make():
        session = world.factory()
        sessions.append(session)
        return session
    real = pipeline.mark_failed
    monkeypatch.setattr(pipeline, "SessionLocal", make)
    monkeypatch.setattr(pipeline, "mark_failed", lambda *args: (
        held.append((sessions[0].in_transaction(), len(sessions[0].new))), real(*args))[1])
    return held


def test_a_stage_failure_releases_the_run_before_recording_it(world, lock_watch, monkeypatch):
    bad = SimpleNamespace(event_type="lab", event_date=None, event_date_precision="day", data={},
                          confidence=0.9, source_page=1, quote="q")
    monkeypatch.setattr(pipeline, "extract_all", lambda pages: [bad])
    run(world, add_document(world))
    assert lock_watch == [(False, 0)]


def test_a_crash_releases_the_run_before_recording_it(world, lock_watch, monkeypatch):
    from sqlalchemy.orm import Session
    real, calls = Session.commit, []

    def dropped(self):
        calls.append(1)
        if len(calls) == 1:  # the first commit is the run's own, "processing"
            raise OperationalError("commit", {}, Exception("server closed the connection"))
        return real(self)
    doc = add_document(world)
    monkeypatch.setattr(Session, "commit", dropped)
    doc = run(world, doc)
    assert doc.failed_stage == "unknown"
    assert lock_watch == [(False, 0)]


def test_the_old_failure_is_gone_while_the_document_is_running_again(world, monkeypatch):
    doc = add_document(world, "failed")
    doc.failed_stage, doc.processing_error = "ocr", "old reason"
    world.db.commit()
    seen = []
    real = normalize.process_document

    def peek(*args):
        other = world.factory()
        row = other.get(Document, doc.id)
        seen.append((row.processing_status, row.failed_stage, row.processing_error))
        other.close()
        return real(*args)
    monkeypatch.setattr(pipeline, "process_document", peek)
    run(world, doc)
    assert seen == [("processing", None, None)]


# --- recording a failure survives a flaky database -------------------------------------------
class Flaky:
    """A SessionLocal that cannot connect for the first `failures` calls."""

    def __init__(self, real, failures):
        self.real, self.failures, self.calls = real, failures, 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise OperationalError("connect", {}, Exception("connection refused"))
        return self.real()


def test_marking_a_failure_is_retried_when_the_database_drops_the_connection(world, monkeypatch):
    doc = add_document(world)
    flaky = Flaky(world.factory, failures=2)
    monkeypatch.setattr(pipeline, "SessionLocal", flaky)
    assert pipeline.mark_failed(str(doc.id), "parse", "reason") is True
    assert flaky.calls == 3
    world.db.expire_all()
    assert world.db.get(Document, doc.id).failed_stage == "parse"


def test_marking_a_failure_gives_up_after_the_policy_and_says_so(world, monkeypatch, caplog):
    doc = add_document(world)
    flaky = Flaky(world.factory, failures=99)
    monkeypatch.setattr(pipeline, "SessionLocal", flaky)
    with caplog.at_level(logging.ERROR, logger="app.pipeline"):
        assert pipeline.mark_failed(str(doc.id), "parse", "reason") is False
    assert flaky.calls == 3
    record = next(r for r in caplog.records if r.message == "document_failure_not_recorded")
    assert record.error_type == "OperationalError" and record.stage == "parse"


def test_a_bug_while_marking_is_not_retried(world, monkeypatch):
    doc = add_document(world)
    calls = []

    def broken():
        calls.append(1)
        raise KeyError("bug")
    monkeypatch.setattr(pipeline, "SessionLocal", broken)
    assert pipeline.mark_failed(str(doc.id), "parse", "reason") is False
    assert len(calls) == 1


# --- documents a dead process left behind ---------------------------------------------------
def test_stuck_documents_are_marked_interrupted(world):
    old = NOW - timedelta(hours=1)
    stuck_a = add_document(world, "processing", started=old)
    stuck_b = add_document(world, "uploaded", started=old)
    assert pipeline.recover_stale_documents(world.db, now=NOW) == 2
    for doc in (stuck_a, stuck_b):
        world.db.refresh(doc)
        assert (doc.processing_status, doc.failed_stage) == ("failed", "interrupted")
        assert "interrupted" in doc.processing_error and "again" in doc.processing_error


def test_recent_and_finished_documents_are_left_alone(world):
    fresh = add_document(world, "processing", started=NOW - timedelta(minutes=2))
    done = add_document(world, "processed", started=NOW - timedelta(hours=5))
    failed = add_document(world, "failed", started=NOW - timedelta(hours=5))
    assert pipeline.recover_stale_documents(world.db, now=NOW) == 0
    for doc, status in ((fresh, "processing"), (done, "processed"), (failed, "failed")):
        world.db.refresh(doc)
        assert doc.processing_status == status and doc.failed_stage is None


def test_the_age_is_counted_from_the_latest_attempt_not_the_upload(world):
    """A document uploaded last week and run again a minute ago is not stale."""
    rerun = add_document(world, "processing", uploaded=NOW - timedelta(days=7),
                         started=NOW - timedelta(minutes=1))
    assert pipeline.recover_stale_documents(world.db, now=NOW) == 0
    world.db.refresh(rerun)
    assert rerun.processing_status == "processing"


def test_without_a_start_time_the_upload_time_is_used(world):
    never_started = add_document(world, "uploaded", uploaded=NOW - timedelta(hours=2))
    assert pipeline.recover_stale_documents(world.db, now=NOW) == 1
    world.db.refresh(never_started)
    assert never_started.failed_stage == "interrupted"


def test_the_age_limit_is_a_parameter(world):
    add_document(world, "processing", started=NOW - timedelta(minutes=2))
    assert pipeline.recover_stale_documents(world.db, older_than=timedelta(minutes=1),
                                            now=NOW) == 1


def test_the_document_list_recovers_stuck_documents(world):
    doc = add_document(world, "processing", started=datetime.now(timezone.utc) - timedelta(hours=2))
    body = world.client.get("/api/v1/documents").json()
    assert body[0]["processing_status"] == "failed" and body[0]["failed_stage"] == "interrupted"
    world.db.refresh(doc)


def test_startup_recovers_and_survives_an_unreachable_database(world, monkeypatch, caplog):
    import services.api.main as main

    stuck = add_document(world, "processing", started=NOW - timedelta(hours=2))
    monkeypatch.setattr(main, "SessionLocal", world.factory)
    with TestClient(app):
        pass
    world.db.refresh(stuck)
    assert stuck.failed_stage == "interrupted"

    monkeypatch.setattr(main, "SessionLocal", Flaky(world.factory, failures=99))
    with caplog.at_level(logging.WARNING, logger="app"), TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
    assert any(r.message == "startup_recovery_skipped" for r in caplog.records)


# --- the reprocess endpoint -----------------------------------------------------------------
@pytest.fixture
def background(monkeypatch):
    """Record what the endpoint hands to the background instead of running it."""
    calls = []
    from services.api.app.api.v1 import documents
    monkeypatch.setattr(documents, "run_pipeline", lambda *args: calls.append(args))
    return calls


def test_a_failed_document_can_be_processed_again(world, background):
    doc = add_document(world, "failed")
    doc.failed_stage, doc.processing_error = "ocr", "old reason"
    world.db.commit()
    set_request_id(None)
    response = world.client.post(f"/api/v1/documents/{doc.id}/reprocess")
    assert response.status_code == 202
    body = response.json()
    assert body["processing_status"] == "uploaded"
    assert body["failed_stage"] is None and body["processing_error"] is None
    assert len(background) == 1
    assert background[0][:3] == (str(doc.id), "application/pdf", doc.storage_path)
    world.db.refresh(doc)
    assert doc.processing_started_at is not None


def test_the_rerun_carries_the_request_id_of_the_request_that_asked(world, background):
    doc = add_document(world, "failed")
    world.client.post(f"/api/v1/documents/{doc.id}/reprocess", headers={"X-Request-ID": "req-r1"})
    assert background[0][3] == "req-r1"


@pytest.mark.parametrize("status", ["uploaded", "processing", "processed"])
def test_only_a_failed_document_can_be_processed_again(world, background, status):
    doc = add_document(world, status)
    response = world.client.post(f"/api/v1/documents/{doc.id}/reprocess")
    assert response.status_code == 409 and background == []
    world.db.refresh(doc)
    assert doc.processing_status == status


def test_nobody_can_reprocess_someone_elses_document(world, background):
    doc = add_document(world, "failed", user=world.other)
    assert world.client.post(f"/api/v1/documents/{doc.id}/reprocess").status_code == 404
    assert world.client.post(f"/api/v1/documents/{uuid.uuid4()}/reprocess").status_code == 404
    assert background == []


def test_a_second_click_does_not_start_a_second_run(world, background):
    doc = add_document(world, "failed")
    assert world.client.post(f"/api/v1/documents/{doc.id}/reprocess").status_code == 202
    assert world.client.post(f"/api/v1/documents/{doc.id}/reprocess").status_code == 409
    assert len(background) == 1


def test_a_rerun_drops_the_earlier_attempt_but_never_the_users_reviewed_events(world, background):
    doc = add_document(world, "failed")
    world.db.add(DocumentPage(document_id=doc.id, page_number=1, text="partial"))
    pending = HealthEvent(id=uuid.uuid4(), user_id=world.me.id, document_id=doc.id,
                          event_type="lab", event_date=date(2026, 1, 1),
                          event_date_precision="day", data={}, confidence=0.5,
                          review_status="pending")
    confirmed = HealthEvent(id=uuid.uuid4(), user_id=world.me.id, document_id=doc.id,
                            event_type="lab", event_date=date(2026, 1, 2),
                            event_date_precision="day", data={}, confidence=0.5,
                            review_status="confirmed")
    world.db.add_all([pending, confirmed])
    world.db.flush()
    for event in (pending, confirmed):
        world.db.add(Provenance(health_event_id=event.id, document_id=doc.id, page_number=1,
                                quote="q"))
    world.db.commit()
    world.client.post(f"/api/v1/documents/{doc.id}/reprocess")
    assert [e.review_status for e in world.db.query(HealthEvent)] == ["confirmed"]
    assert world.db.query(Provenance).count() == 1
    assert world.db.query(DocumentPage).count() == 0


def test_the_failed_stage_is_in_the_document_the_page_reads(world):
    doc = add_document(world, "failed")
    doc.failed_stage, doc.processing_error = "parse", "The PDF could not be read."
    world.db.commit()
    body = world.client.get(f"/api/v1/documents/{doc.id}").json()
    assert (body["failed_stage"], body["processing_error"]) == (
        "parse", "The PDF could not be read.")


# --- upload ---------------------------------------------------------------------------------
def upload(w):
    return w.client.post("/api/v1/documents", files={"file": ("lab.pdf", b"%PDF-1.4 x",
                                                              "application/pdf")})


def test_an_upload_is_timed_and_hands_its_request_id_to_the_background_run(world, background):
    response = world.client.post(
        "/api/v1/documents", headers={"X-Request-ID": "req-up-1"},
        files={"file": ("lab.pdf", b"%PDF-1.4 x", "application/pdf")})
    assert response.status_code == 201
    assert metrics.registry.snapshot()["upload"]["count"] == 1
    assert background[0][3] == "req-up-1"
    doc = world.db.get(Document, uuid.UUID(response.json()["id"]))
    assert doc.processing_started_at is not None


def test_a_storage_failure_on_upload_is_a_clear_503_and_leaves_no_document(world, background,
                                                                          monkeypatch):
    def disk_full(*args):
        raise OSError(28, "No space left on device")
    from services.api.app.api.v1 import documents
    monkeypatch.setattr(documents, "save_upload", disk_full)
    response = upload(world)
    assert response.status_code == 503
    assert "could not be stored" in response.json()["error"]["message"]
    assert world.db.query(Document).count() == 0 and background == []
    assert metrics.registry.snapshot()["upload"]["errors"] == 1


# --- OCR timeout (YGH-94) -------------------------------------------------------------------
def fake_tesseract(monkeypatch, behaviour):
    seen = {}

    def image_to_string(image, **kwargs):
        seen.update(kwargs)
        return behaviour()
    monkeypatch.setattr(ocr, "pytesseract", SimpleNamespace(image_to_string=image_to_string),
                        raising=False)
    return seen


def test_ocr_is_given_the_policy_timeout(monkeypatch):
    seen = fake_tesseract(monkeypatch, lambda: " text ")
    assert ocr._recognise(object()) == "text"
    assert seen == {"timeout": 60}


def test_a_tesseract_timeout_becomes_an_ocr_timeout(monkeypatch):
    def slow():
        raise RuntimeError("Tesseract process timeout")
    fake_tesseract(monkeypatch, slow)
    with pytest.raises(ocr.OcrTimeout):
        ocr._recognise(object())


def test_other_tesseract_errors_are_not_mistaken_for_a_timeout(monkeypatch):
    def broken():
        raise RuntimeError("bad image")
    fake_tesseract(monkeypatch, broken)
    with pytest.raises(RuntimeError, match="bad image"):
        ocr._recognise(object())


def test_a_page_that_will_not_render_is_an_ocr_timeout(monkeypatch):
    class Stuck(Exception):
        pass

    def render(content, **kwargs):
        raise Stuck()
    monkeypatch.setattr(ocr, "_OCR_AVAILABLE", True)
    monkeypatch.setattr(ocr, "PDFPopplerTimeoutError", Stuck, raising=False)
    monkeypatch.setattr(ocr, "convert_from_bytes", render, raising=False)
    with pytest.raises(ocr.OcrTimeout):
        ocr.ocr_pdf_page(b"pdf", 1)


def test_page_rendering_is_bounded_too(monkeypatch):
    seen = {}

    def render(content, **kwargs):
        seen.update(kwargs)
        return []
    monkeypatch.setattr(ocr, "_OCR_AVAILABLE", True)
    monkeypatch.setattr(ocr, "convert_from_bytes", render, raising=False)
    assert ocr.ocr_pdf_page(b"pdf", 3) == ""
    assert seen == {"first_page": 3, "last_page": 3, "timeout": 60}


# --- storage retry (YGH-94) -----------------------------------------------------------------
class Locked:
    """A file that is locked for the first `failures` reads."""

    def __init__(self, failures, error=PermissionError):
        self.failures, self.error, self.calls = failures, error, 0

    def read_bytes(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error("locked")
        return b"data"


def test_a_briefly_locked_file_is_read_on_the_second_try(monkeypatch):
    locked = Locked(1)
    monkeypatch.setattr(storage, "resolve_upload_path", lambda path: locked)
    assert storage.read_upload("a/b.pdf") == b"data" and locked.calls == 2


def test_a_file_that_stays_locked_fails_after_three_tries(monkeypatch):
    locked = Locked(99)
    monkeypatch.setattr(storage, "resolve_upload_path", lambda path: locked)
    with pytest.raises(PermissionError):
        storage.read_upload("a/b.pdf")
    assert locked.calls == 3


def test_a_missing_file_is_not_retried(monkeypatch):
    locked = Locked(99, error=FileNotFoundError)
    monkeypatch.setattr(storage, "resolve_upload_path", lambda path: locked)
    with pytest.raises(FileNotFoundError):
        storage.read_upload("a/b.pdf")
    assert locked.calls == 1


def test_writing_an_upload_retries_a_locked_file_and_stores_it(world, monkeypatch):
    real = type(world.root).write_bytes
    attempts = []

    def flaky_write(self, content):
        attempts.append(1)
        if len(attempts) == 1:
            raise PermissionError("antivirus")
        return real(self, content)
    monkeypatch.setattr(type(world.root), "write_bytes", flaky_write)
    path, _ = storage.save_upload(world.me.id, "lab.pdf", b"content")
    assert len(attempts) == 2 and (world.root / path).read_bytes() == b"content"


# --- database timeouts (YGH-94) -------------------------------------------------------------
def test_postgres_gets_connect_statement_and_pool_timeouts():
    options = db_module.engine_options("postgresql+psycopg://u:p@localhost/db")
    assert options["connect_args"] == {"connect_timeout": 5,
                                       "options": "-c statement_timeout=30000"}
    assert options["pool_timeout"] == 10 and options["pool_recycle"] == 3600
    assert options["pool_pre_ping"] is True


def test_other_databases_get_no_postgres_only_settings():
    options = db_module.engine_options("sqlite://")
    assert options == {"pool_pre_ping": True}
    assert make_url("sqlite://").get_backend_name() == "sqlite"
