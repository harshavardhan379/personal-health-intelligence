"""Document, events and account endpoints added for the frontend (YGH-116, 118, 124).

In-memory SQLite for the tables involved; no Postgres, no model."""

import uuid
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.schemas.db import get_db
from packages.schemas.models import Document, HealthEvent, Provenance, User
from services.api.app.api.v1 import account
from services.api.app.core import storage
from services.api.app.core.deps import get_current_user
from services.api.main import app

PDF = b"%PDF-1.4 stand-in original"


@pytest.fixture
def world(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    for table in (User, Document, HealthEvent, Provenance):
        table.__table__.create(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    monkeypatch.setattr(storage, "STORAGE_DIR", tmp_path)

    me = User(id=uuid.uuid4(), email="me@example.com", hashed_password="x",
              created_at=datetime(2026, 1, 5, tzinfo=timezone.utc))
    other = User(id=uuid.uuid4(), email="other@example.com", hashed_password="x")
    session.add_all([me, other])
    session.commit()

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: me
    yield session, me, other, tmp_path, TestClient(app)
    app.dependency_overrides.clear()
    session.close()


def add_document(session, user, root, filename="lab.pdf", mime="application/pdf",
                 storage_path=None, content=PDF):
    doc = Document(id=uuid.uuid4(), user_id=user.id, filename=filename, mime_type=mime,
                   storage_path=storage_path or f"{user.id}/{uuid.uuid4().hex}.pdf",
                   processing_status="processed", classification="lab_report",
                   uploaded_at=datetime(2026, 10, 10, 7, 14, tzinfo=timezone.utc))
    session.add(doc)
    session.commit()
    if content is not None and storage_path is None:
        target = root / doc.storage_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return doc


def add_event(session, user, doc, status="confirmed", day=date(2026, 1, 10), etype="lab",
              data=None, page=1, quote="HbA1c: 7.2 %"):
    event = HealthEvent(id=uuid.uuid4(), user_id=user.id, document_id=doc.id, event_type=etype,
                        event_date=day, event_date_precision="day",
                        data=data or {"test_name": "HbA1c", "value": 7.2, "unit": "%"},
                        confidence=0.9, review_status=status)
    session.add(event)
    session.flush()
    session.add(Provenance(health_event_id=event.id, document_id=doc.id, page_number=page,
                           quote=quote))
    session.commit()
    return event


# --- the upload date -----------------------------------------------------------------------
def test_the_document_list_carries_the_upload_date(world):
    session, me, _, root, client = world
    add_document(session, me, root)
    body = client.get("/api/v1/documents").json()
    assert body[0]["uploaded_at"].startswith("2026-10-10T07:14")
    assert body[0]["classification"] == "lab_report"


# --- the original file ---------------------------------------------------------------------
def test_the_owner_gets_the_original_file_shown_inline(world):
    session, me, _, root, client = world
    doc = add_document(session, me, root, filename="Lab Report #1.pdf")
    response = client.get(f"/api/v1/documents/{doc.id}/file")
    assert response.status_code == 200 and response.content == PDF
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    assert "Lab%20Report%20%231.pdf" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in response.headers["cache-control"]


def test_another_users_file_is_a_404_and_its_bytes_are_never_returned(world):
    session, me, other, root, client = world
    theirs = add_document(session, other, root, content=b"%PDF their private report")
    response = client.get(f"/api/v1/documents/{theirs.id}/file")
    assert response.status_code == 404 and b"private" not in response.content


def test_a_file_missing_from_disk_is_a_404_not_a_crash(world):
    session, me, _, root, client = world
    doc = add_document(session, me, root, content=None)
    response = client.get(f"/api/v1/documents/{doc.id}/file")
    assert response.status_code == 404
    assert response.json()["error"]["message"] == "The original file is not available"


def test_a_stored_path_that_escapes_the_storage_folder_is_never_read(world):
    session, me, _, root, client = world
    secret = root.parent / "outside-secret.txt"
    secret.write_bytes(b"do not serve me")
    doc = add_document(session, me, root, storage_path="../outside-secret.txt")
    response = client.get(f"/api/v1/documents/{doc.id}/file")
    assert response.status_code == 404 and b"do not serve" not in response.content


def test_a_type_that_is_not_allowed_is_sent_as_a_download_not_rendered(world):
    session, me, _, root, client = world
    doc = add_document(session, me, root, filename="x.html", mime="text/html",
                       content=b"<script>alert(1)</script>")
    response = client.get(f"/api/v1/documents/{doc.id}/file")
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")


def test_the_path_guard_accepts_files_inside_and_refuses_everything_else(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "STORAGE_DIR", tmp_path)
    (tmp_path / "u").mkdir()
    (tmp_path / "u" / "a.pdf").write_bytes(b"x")
    assert storage.resolve_upload_path("u/a.pdf") == (tmp_path / "u" / "a.pdf").resolve()
    for bad in ("..", "../a.pdf", "u/../../a.pdf", str(tmp_path.parent / "elsewhere.pdf")):
        with pytest.raises(FileNotFoundError):
            storage.resolve_upload_path(bad)


# --- the events extracted from a document --------------------------------------------------
def test_a_documents_events_are_listed_with_every_review_status_and_their_provenance(world):
    session, me, _, root, client = world
    doc = add_document(session, me, root)
    add_event(session, me, doc, status="confirmed", day=date(2026, 1, 10))
    add_event(session, me, doc, status="pending", etype="medication", day=date(2026, 1, 10),
              data={"medication": "Metformin", "dose": "500mg"}, page=2, quote="Metformin 500mg")
    add_event(session, me, doc, status="rejected", etype="symptom", day=date(2026, 1, 9),
              data={"symptom": "headache"}, quote="headache")
    body = client.get(f"/api/v1/documents/{doc.id}/events").json()
    assert [e["event_date"] for e in body] == ["2026-01-09", "2026-01-10", "2026-01-10"]
    assert {e["review_status"] for e in body} == {"confirmed", "pending", "rejected"}
    medication = next(e for e in body if e["event_type"] == "medication")
    assert medication["provenance"] == [{"page_number": 2, "quote": "Metformin 500mg"}]
    assert medication["data"]["dose"] == "500mg" and medication["event_date_precision"] == "day"


def test_events_of_someone_elses_document_are_a_404(world):
    session, me, other, root, client = world
    theirs = add_document(session, other, root)
    add_event(session, other, theirs)
    assert client.get(f"/api/v1/documents/{theirs.id}/events").status_code == 404


def test_a_document_with_no_events_gives_an_empty_list(world):
    session, me, _, root, client = world
    doc = add_document(session, me, root)
    assert client.get(f"/api/v1/documents/{doc.id}/events").json() == []


# --- the account summary -------------------------------------------------------------------
def test_the_account_summary_counts_only_my_documents_and_events_by_status(world):
    session, me, other, root, client = world
    mine = add_document(session, me, root)
    add_document(session, me, root, filename="second.pdf")
    add_event(session, me, mine, status="confirmed")
    add_event(session, me, mine, status="confirmed", day=date(2026, 2, 1))
    add_event(session, me, mine, status="edited", day=date(2026, 3, 1))
    add_event(session, me, mine, status="pending", day=date(2026, 4, 1))
    add_event(session, me, mine, status="rejected", day=date(2026, 5, 1))
    theirs = add_document(session, other, root)
    add_event(session, other, theirs, status="rejected")
    add_event(session, other, theirs, status="confirmed", day=date(2026, 6, 1))
    body = client.get("/api/v1/account").json()
    assert body["email"] == "me@example.com" and body["created_at"].startswith("2026-01-05")
    assert body["documents"] == 2
    assert body["events"] == {"confirmed": 2, "edited": 1, "pending": 1, "rejected": 1}


def test_the_privacy_block_says_whether_the_model_runs_on_this_machine(world, monkeypatch):
    _, _, _, _, client = world
    assert client.get("/api/v1/account").json()["privacy"] == {
        "llm_provider": "ollama", "ai_runs_locally": True}
    monkeypatch.setattr(account.settings, "llm_provider", "OpenAI")
    assert client.get("/api/v1/account").json()["privacy"] == {
        "llm_provider": "openai", "ai_runs_locally": False}


def test_a_new_account_has_zero_of_everything(world):
    _, _, _, _, client = world
    body = client.get("/api/v1/account").json()
    assert body["documents"] == 0
    assert body["events"] == {"confirmed": 0, "edited": 0, "pending": 0, "rejected": 0}


def test_all_of_these_require_a_signed_in_user():
    app.dependency_overrides.clear()
    client = TestClient(app)
    some = uuid.uuid4()
    for path in (f"/api/v1/documents/{some}/file", f"/api/v1/documents/{some}/events",
                 "/api/v1/account"):
        assert client.get(path).status_code in (401, 403), path
