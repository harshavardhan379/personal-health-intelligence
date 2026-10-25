"""YGH-119 (delete data) and YGH-121 (delete account), against a real Postgres.

The real database matters here: `provenance.document_id` and `embeddings.document_id` have no
ON DELETE CASCADE, so a deletion done in the wrong order is refused by Postgres, which SQLite (the
unit tests) would never notice."""

import logging
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from packages.schemas.models import (
    AITrace,
    Document,
    DocumentPage,
    Embedding,
    HealthEvent,
    Provenance,
    User,
)
from services.api.app.core import deletion, ratelimit, storage
from services.api.main import app
from tests.helpers import app_log_text
from tests.integration.conftest import PASSWORD

LAB = "HbA1c: 7.2 % (ref 4.0-5.6) 2026-01-10"
OTHER = "Glucose: 99 mg/dL 2026-03-01"
TABLES = (Document, DocumentPage, HealthEvent, Provenance, Embedding, AITrace)


def totals(stack, **where):
    """Row counts per table; a filter (user_id=...) applies only to tables that have the column."""
    with stack.db() as db:
        return {m.__tablename__: db.query(m).filter_by(
                    **{k: v for k, v in where.items() if hasattr(m, k)}).count()
                for m in TABLES}


def owned(stack, user_id):
    """Row counts of the tables that carry a user id, for exactly that user."""
    with stack.db() as db:
        return {m.__tablename__: db.query(m).filter_by(user_id=user_id).count()
                for m in TABLES if hasattr(m, "user_id")}


def files_of(stack):
    return sorted(p for p in stack.root.rglob("*") if p.is_file())


def delete(stack, headers, path, password=PASSWORD):
    return stack.client.request("DELETE", path, headers=headers, json={"password": password})


@pytest.fixture
def alice(stack):
    headers = stack.register("alice@example.com")
    stack.confirmed_document(headers, LAB, "alice-one.pdf")
    stack.confirmed_document(headers, OTHER, "alice-two.pdf")
    stack.llm.reply = {"answer": "Your HbA1c was 7.2%.",
                       "claims": [{"text": "HbA1c was 7.2%", "citations": [1]}],
                       "uncertainty": None}
    assert stack.ask(headers, "What is my latest HbA1c result?").status_code == 200
    with stack.db() as db:
        user_id = db.query(User).filter_by(email="alice@example.com").one().id
    return stack, headers, user_id


# --- one document ---------------------------------------------------------------------------
def test_a_document_goes_with_everything_derived_from_it(alice):
    stack, headers, _ = alice
    before = totals(stack)
    assert before["documents"] == 2 and before["embeddings"] == 2 and before["provenance"] == 2
    victim = stack.client.get("/api/v1/documents", headers=headers).json()[-1]["id"]

    response = stack.client.delete(f"/api/v1/documents/{victim}", headers=headers)
    assert response.status_code == 204

    after = totals(stack)
    assert after["documents"] == 1 and after["health_events"] == 1
    assert after["embeddings"] == 1 and after["provenance"] == 1 and after["document_pages"] == 1
    assert len(files_of(stack)) == 1
    assert stack.client.get(f"/api/v1/documents/{victim}", headers=headers).status_code == 404


def test_what_is_left_still_works_and_what_went_is_gone_from_every_view(alice):
    stack, headers, _ = alice
    documents = stack.client.get("/api/v1/documents", headers=headers).json()
    gone = next(d["id"] for d in documents if d["filename"] == "alice-one.pdf")
    stack.client.delete(f"/api/v1/documents/{gone}", headers=headers)

    timeline = stack.client.get("/api/v1/timeline", headers=headers).json()
    assert [e["data"].get("test_name") for e in timeline] == ["Glucose"]
    # Semantic search always returns the nearest fact that exists, so what matters is that the
    # deleted one can no longer be found.
    found = stack.client.get("/api/v1/search", headers=headers, params={"q": "HbA1c"}).json()
    assert all(item["document_id"] != gone for item in found)
    assert stack.client.get("/api/v1/trends/HbA1c", headers=headers).json()["points"] == []
    stack.llm.calls.clear()
    answer = stack.ask(headers, "What is my latest HbA1c result?").json()
    assert answer["evidence"] == [] and stack.llm.calls == []


def test_a_failed_or_half_processed_document_can_be_deleted_too(stack):
    headers = stack.register()
    document_id = stack.upload(headers).json()["id"]
    with stack.db() as db:
        db.query(Document).update({"processing_status": "failed", "failed_stage": "ocr"})
        db.commit()
    assert stack.client.delete(f"/api/v1/documents/{document_id}",
                               headers=headers).status_code == 204
    assert totals(stack)["documents"] == 0 and totals(stack)["health_events"] == 0


def test_deleting_a_document_twice_is_a_404_not_an_error(alice):
    stack, headers, _ = alice
    victim = stack.client.get("/api/v1/documents", headers=headers).json()[0]["id"]
    assert stack.client.delete(f"/api/v1/documents/{victim}", headers=headers).status_code == 204
    assert stack.client.delete(f"/api/v1/documents/{victim}", headers=headers).status_code == 404


def test_a_document_whose_file_is_already_missing_still_deletes(alice):
    stack, headers, _ = alice
    for path in files_of(stack):
        path.unlink()
    victim = stack.client.get("/api/v1/documents", headers=headers).json()[0]["id"]
    assert stack.client.delete(f"/api/v1/documents/{victim}", headers=headers).status_code == 204
    assert totals(stack)["documents"] == 1


def test_a_failed_database_deletion_removes_nothing(alice, monkeypatch):
    stack, headers, _ = alice
    before, files = totals(stack), files_of(stack)
    real = deletion._delete_document_rows

    def delete_then_crash(db, ids):
        real(db, ids)
        raise RuntimeError("crash after the rows were deleted but before the commit")
    monkeypatch.setattr(deletion, "_delete_document_rows", delete_then_crash)
    victim = stack.client.get("/api/v1/documents", headers=headers).json()[0]["id"]
    quiet = TestClient(app, raise_server_exceptions=False)
    assert quiet.delete(f"/api/v1/documents/{victim}", headers=headers).status_code == 500
    assert totals(stack) == before and files_of(stack) == files  # all or nothing


def test_a_file_that_cannot_be_removed_is_logged_without_its_path_and_the_rows_still_go(
        alice, monkeypatch, caplog):
    stack, headers, _ = alice

    def locked(path):
        raise PermissionError(f"locked: {path}")
    monkeypatch.setattr(storage, "delete_stored_file", locked)
    victim = stack.client.get("/api/v1/documents", headers=headers).json()[0]["id"]
    with caplog.at_level(logging.INFO, logger="app.deletion"):
        assert stack.client.delete(f"/api/v1/documents/{victim}",
                                   headers=headers).status_code == 204
    assert totals(stack)["documents"] == 1
    record = next(r for r in caplog.records if r.message == "stored_file_not_deleted")
    assert record.error_type == "PermissionError"
    assert "locked" not in " ".join(str(r.__dict__) for r in caplog.records)


def test_the_result_counts_what_could_not_be_removed(alice, monkeypatch):
    stack, headers, _ = alice

    def locked(path):
        raise PermissionError("locked")
    monkeypatch.setattr(storage, "delete_stored_file", locked)
    with stack.db() as db:
        document = db.query(Document).first()
        path = document.storage_path
        result = deletion.delete_document(db, document)
    assert (result.documents, result.events, result.files_failed) == (1, 1, 1)
    assert result.failed_paths == [path]


def test_a_stored_path_that_points_outside_storage_is_never_deleted(stack, tmp_path):
    outside = tmp_path.parent / "must_survive.txt"
    outside.write_text("not ours")
    headers = stack.register()
    document_id = stack.upload(headers).json()["id"]
    with stack.db() as db:
        db.get(Document, uuid.UUID(document_id)).storage_path = "../must_survive.txt"
        db.commit()
    assert stack.client.delete(f"/api/v1/documents/{document_id}",
                               headers=headers).status_code == 204
    assert outside.exists()
    outside.unlink()


# --- all of one person's data ----------------------------------------------------------------
def test_the_counts_say_documents_and_facts_apart(stack):
    headers = stack.register()
    stack.confirmed_document(
        headers, "HbA1c: 7.2 % (ref 4.0-5.6) 2026-01-10\nGlucose: 99 mg/dL 2026-01-10", "two.pdf")
    response = delete(stack, headers, "/api/v1/account/data")
    assert response.json() == {"documents": 1, "events": 2, "files_failed": 0}


def test_wrong_password_deletes_nothing(alice):
    stack, headers, _ = alice
    before, files = totals(stack), files_of(stack)
    response = delete(stack, headers, "/api/v1/account/data", password="wrong password")
    assert response.status_code == 403
    assert totals(stack) == before and files_of(stack) == files


def test_deleting_my_data_empties_everything_but_keeps_the_account(alice):
    stack, headers, user_id = alice
    assert totals(stack, user_id=user_id)["ai_traces"] == 1
    other = stack.register("bob@example.com")
    stack.confirmed_document(other, "Ferritin: 40 ng/mL 2026-04-01", "bob.pdf")
    bob_id = stack.db().query(User).filter_by(email="bob@example.com").one().id
    bob_before = owned(stack, bob_id)

    response = delete(stack, headers, "/api/v1/account/data")
    assert response.status_code == 200
    assert response.json() == {"documents": 2, "events": 2, "files_failed": 0}

    with stack.db() as db:
        for model in (Document, HealthEvent, Embedding, AITrace):
            assert db.query(model).filter_by(user_id=user_id).count() == 0, model
        assert db.query(DocumentPage).count() == 1  # only Bob's page remains
        assert db.query(Provenance).count() == 1
    assert not (stack.root / str(user_id)).exists()
    assert stack.login("alice@example.com").status_code == 200  # the account itself is kept
    assert stack.client.get("/api/v1/account", headers=headers).json()["documents"] == 0
    assert owned(stack, bob_id) == bob_before


def test_files_nothing_points_at_are_removed_with_the_users_folder(alice):
    stack, headers, user_id = alice
    stray = stack.root / str(user_id) / "orphan-from-a-crashed-upload.pdf"
    stray.write_bytes(b"%PDF-1.4 orphan")
    delete(stack, headers, "/api/v1/account/data")
    assert not stray.exists() and not (stack.root / str(user_id)).exists()


def test_a_folder_that_cannot_be_removed_is_reported(alice, monkeypatch):
    stack, headers, _ = alice

    def locked(user_id):
        raise PermissionError("locked")
    monkeypatch.setattr(storage, "delete_user_storage", locked)
    response = delete(stack, headers, "/api/v1/account/data")
    assert response.status_code == 200 and response.json()["files_failed"] == 1
    assert totals(stack)["documents"] == 0  # the records are gone regardless


def test_a_failed_deletion_of_all_data_rolls_back_completely(alice, monkeypatch):
    stack, headers, _ = alice
    before = totals(stack)
    real = deletion._delete_document_rows

    def delete_then_crash(db, ids):
        real(db, ids)
        raise RuntimeError("boom")
    monkeypatch.setattr(deletion, "_delete_document_rows", delete_then_crash)
    quiet = TestClient(app, raise_server_exceptions=False)
    response = quiet.request("DELETE", "/api/v1/account/data", headers=headers,
                             json={"password": PASSWORD})
    assert response.status_code == 500 and totals(stack) == before


def test_guessing_the_password_to_delete_is_rate_limited(alice):
    stack, headers, _ = alice
    codes = [delete(stack, headers, "/api/v1/account/data", "nope").status_code
             for _ in range(ratelimit.LOGIN_FAILURES)]
    assert codes == [403] * ratelimit.LOGIN_FAILURES
    blocked = delete(stack, headers, "/api/v1/account/data", PASSWORD)  # even the right one
    assert blocked.status_code == 429 and "Retry-After" in blocked.headers
    assert totals(stack)["documents"] == 2


# --- the account -----------------------------------------------------------------------------
def test_wrong_password_keeps_the_account(alice):
    stack, headers, _ = alice
    assert delete(stack, headers, "/api/v1/account", password="wrong").status_code == 403
    assert stack.client.get("/api/v1/account", headers=headers).status_code == 200


def test_deleting_the_account_removes_every_trace_of_it(alice):
    stack, headers, user_id = alice
    bob = stack.register("bob@example.com")
    stack.confirmed_document(bob, "Ferritin: 40 ng/mL 2026-04-01", "bob.pdf")

    assert delete(stack, headers, "/api/v1/account").status_code == 204

    with stack.db() as db:
        assert db.get(User, user_id) is None
        for model in (Document, HealthEvent, Embedding, AITrace):
            assert db.query(model).filter_by(user_id=user_id).count() == 0, model
        # Bob's data is all that is left
        assert db.query(Document).count() == 1 and db.query(Provenance).count() == 1
        assert db.query(User).count() == 1
    assert not (stack.root / str(user_id)).exists()


def test_the_login_stops_working_the_moment_the_account_is_gone(alice):
    stack, headers, _ = alice
    delete(stack, headers, "/api/v1/account")
    assert stack.client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert stack.client.get("/api/v1/documents", headers=headers).status_code == 401
    assert stack.login("alice@example.com").status_code == 401


def test_an_email_can_be_registered_again_after_its_account_is_deleted(alice):
    stack, headers, _ = alice
    delete(stack, headers, "/api/v1/account")
    again = stack.register("alice@example.com")
    assert stack.client.get("/api/v1/documents", headers=again).json() == []


def test_deleting_the_account_of_someone_with_no_data_works(stack):
    headers = stack.register()
    assert delete(stack, headers, "/api/v1/account").status_code == 204
    with stack.db() as db:
        assert db.query(User).count() == 0


def test_a_user_with_a_document_still_being_processed_can_delete_their_account(stack):
    headers = stack.register()
    stack.upload(headers)
    with stack.db() as db:
        db.query(Document).update({"processing_status": "processing"})
        db.commit()
    assert delete(stack, headers, "/api/v1/account").status_code == 204
    assert totals(stack)["documents"] == 0


def test_the_deletion_log_lines_carry_counts_only(alice, caplog):
    stack, headers, _ = alice
    with caplog.at_level(logging.DEBUG):
        delete(stack, headers, "/api/v1/account")
    everything = app_log_text(caplog.records)
    for secret in ("alice@example.com", "alice-one.pdf", "HbA1c", "7.2", PASSWORD):
        assert secret not in everything, secret
    done = next(r for r in caplog.records if r.message == "user_data_deleted")
    assert (done.documents, done.events, done.account_removed) == (2, 2, True)


def test_the_trace_rows_go_with_the_data(alice):
    stack, headers, user_id = alice
    with stack.db() as db:
        assert db.execute(text("SELECT count(*) FROM ai_traces")).scalar() == 1
    delete(stack, headers, "/api/v1/account/data")
    with stack.db() as db:
        assert db.execute(text("SELECT count(*) FROM ai_traces")).scalar() == 0
