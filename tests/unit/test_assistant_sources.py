"""The evidence the assistant endpoint returns (YGH-122): numbering, filenames only from the
user's own documents, and nothing shown behind a blocked answer.

Uses an in-memory SQLite database for the two tables involved (no Postgres, no LLM)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.schemas.evidence import Evidence
from packages.schemas.models import Document, User
from packages.schemas.verification import ClaimVerification, VerifiedAnswer
from services.api.app.core.deps import get_current_user
from services.api.main import app
from services.verification import verify
from services.verification.sources import describe_sources


@pytest.fixture
def db():
    # one shared connection: the test client runs the app on another thread
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    User.__table__.create(engine)
    Document.__table__.create(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


def make_user(db, email):
    user = User(id=uuid.uuid4(), email=email, hashed_password="x")
    db.add(user)
    db.commit()
    return user


def make_document(db, user, filename):
    doc = Document(id=uuid.uuid4(), user_id=user.id, filename=filename,
                   mime_type="application/pdf", storage_path="x", processing_status="processed")
    db.add(doc)
    db.commit()
    return doc


def evidence(document_id, text="HbA1c: 7.2 %", page=1, event_ids=("e1",), source="structured"):
    return Evidence(document_id=str(document_id), page=page, text=text,
                    event_ids=list(event_ids), relevance_score=1.0, source=source)


# --- describe_sources ------------------------------------------------------------------
def test_sources_are_numbered_from_one_in_the_order_the_model_saw_them(db):
    me = make_user(db, "me@example.com")
    lab, rx = make_document(db, me, "lab.pdf"), make_document(db, me, "rx.pdf")
    sources = describe_sources(db, me.id, [evidence(rx.id, "Metformin 500mg", 2, ("e2",)),
                                           evidence(lab.id, "HbA1c: 7.2 %", 1, ("e1", "e3"))])
    assert [(s.number, s.filename, s.page, s.text) for s in sources] == [
        (1, "rx.pdf", 2, "Metformin 500mg"), (2, "lab.pdf", 1, "HbA1c: 7.2 %")]
    assert sources[1].event_ids == ["e1", "e3"] and sources[0].source == "structured"


def test_a_filename_is_never_taken_from_another_users_document(db):
    me, other = make_user(db, "me@example.com"), make_user(db, "other@example.com")
    theirs = make_document(db, other, "their-secret-report.pdf")
    mine = make_document(db, me, "mine.pdf")
    sources = describe_sources(db, me.id, [evidence(theirs.id), evidence(mine.id)])
    assert sources[0].filename is None and sources[1].filename == "mine.pdf"
    assert "their-secret-report" not in repr(sources)


def test_a_document_id_that_is_not_an_id_or_does_not_exist_gets_no_filename(db):
    me = make_user(db, "me@example.com")
    sources = describe_sources(db, me.id, [evidence("not-a-uuid"), evidence(uuid.uuid4())])
    assert [s.filename for s in sources] == [None, None] and len(sources) == 2


def test_no_evidence_gives_an_empty_list_without_touching_the_database():
    assert describe_sources(None, uuid.uuid4(), []) == []


# --- run_verified_pipeline ---------------------------------------------------------------
def test_the_pipeline_returns_the_evidence_and_generate_verified_answer_is_unchanged(monkeypatch):
    from packages.schemas.answer import Answer, Claim
    ev = [evidence(uuid.uuid4())]
    answer = Answer(answer="Your HbA1c was 7.2 %. [1]", claims=[Claim(text="HbA1c is 7.2%",
                                                                      citation_indices=[1])],
                    evidence_ids=["e1"], uncertainty=None, intent="LOOKUP")
    monkeypatch.setattr(verify, "generate_answer_with_context",
                        lambda db, user, q: (answer, type("C", (), {"evidence": ev})()))
    user = uuid.uuid4()
    verified, returned = verify.run_verified_pipeline(None, user, "What was my HbA1c in May 2023?")
    assert returned == ev and verified.evidence == []  # evidence is attached only by the route
    assert verify.generate_verified_answer(None, user, "What was my HbA1c in May 2023?") == verified


def test_a_question_blocked_before_retrieval_returns_no_evidence(monkeypatch):
    monkeypatch.setattr(verify, "generate_answer_with_context",
                        lambda *a: pytest.fail("a blocked question must not reach retrieval"))
    verified, returned = verify.run_verified_pipeline(None, uuid.uuid4(),
                                                      "Should I stop taking my metformin?")
    assert returned == [] and verified.safety_action in ("ABSTAIN", "REDIRECT_TO_PROFESSIONAL")


# --- the route ---------------------------------------------------------------------------
@pytest.fixture
def client(db):
    me = make_user(db, "me@example.com")
    doc = make_document(db, me, "lab.pdf")
    from packages.schemas.db import get_db
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: me
    yield TestClient(app), me, doc
    app.dependency_overrides.clear()


def answer_with(action, evidence_list):
    verified = VerifiedAnswer(
        answer="An answer [1].",
        claim_verifications=[ClaimVerification(claim_text="c", label="SUPPORTED",
                                               citation_indices=[1])],
        evidence_ids=["e1"], intent="LOOKUP", fully_verified=True, safety_category="X",
        safety_action=action)
    return verified, evidence_list


def test_the_response_has_the_numbered_evidence_alongside_the_existing_fields(client, monkeypatch):
    test_client, me, doc = client
    monkeypatch.setattr("services.api.app.api.v1.assistant.run_verified_pipeline",
                        lambda db, user, q: answer_with("ALLOW", [evidence(doc.id, page=3)]))
    body = test_client.post("/api/v1/assistant/ask", json={"question": "What was my HbA1c?"}).json()
    assert body["answer"] == "An answer [1]." and body["fully_verified"] is True
    assert body["claim_verifications"][0]["citation_indices"] == [1]
    assert body["evidence"] == [{"number": 1, "text": "HbA1c: 7.2 %", "document_id": str(doc.id),
                                 "filename": "lab.pdf", "page": 3, "event_ids": ["e1"],
                                 "source": "structured"}]


def test_an_answer_with_an_uncertainty_note_still_shows_its_evidence(client, monkeypatch):
    test_client, _, doc = client
    monkeypatch.setattr("services.api.app.api.v1.assistant.run_verified_pipeline",
                        lambda db, user, q: answer_with("ALLOW_WITH_UNCERTAINTY",
                                                        [evidence(doc.id)]))
    body = test_client.post("/api/v1/assistant/ask", json={"question": "q"}).json()
    assert len(body["evidence"]) == 1


def test_nothing_behind_a_blocked_or_replaced_answer_is_shown(client, monkeypatch):
    test_client, _, doc = client
    for action in ("CLARIFY", "ABSTAIN", "REDIRECT_TO_PROFESSIONAL"):
        monkeypatch.setattr("services.api.app.api.v1.assistant.run_verified_pipeline",
                            lambda db, user, q, a=action: answer_with(a, [evidence(doc.id)]))
        body = test_client.post("/api/v1/assistant/ask", json={"question": "q"}).json()
        assert body["evidence"] == [], action


def test_the_route_requires_a_signed_in_user():
    app.dependency_overrides.clear()
    response = TestClient(app).post("/api/v1/assistant/ask", json={"question": "q"})
    assert response.status_code in (401, 403)
