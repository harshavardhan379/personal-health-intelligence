"""AI tracing (YGH-92): what one assistant call did, kept as metadata only.

Runs the real route, safety gate, verifier and trace writer in an in-memory SQLite database; only
retrieval and the model are stand-ins."""

import json
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.observability import metrics
from packages.schemas.db import get_db
from packages.schemas.evidence import Evidence
from packages.schemas.models import AITrace, Document, HealthEvent, Provenance, User
from services.api.app.core import tracing
from services.api.app.core.deps import get_current_user
from services.api.main import app
from services.reasoning.context_builder import AssistantContext
from services.reasoning.generate import PROMPT_VERSION
from services.reasoning.providers import LLMProviderError
from tests.helpers import app_log_text

QUESTION = "What is my latest HbA1c result, please?"
ANSWER_TEXT = "Your HbA1c was 7.2 percent."
EVIDENCE_TEXT = "2026-01-10: HbA1c: 7.2 %"


class FakeProvider:
    name = "ollama"
    model = "llama3.2:3b"

    def __init__(self, reply=None, error=None):
        self.reply, self.error = reply, error

    def generate_with_usage(self, messages):
        if self.error:
            raise self.error
        return json.dumps(self.reply), {"input_tokens": 321, "output_tokens": 45}

    def generate(self, messages):
        return self.generate_with_usage(messages)[0]


GOOD_REPLY = {
    "answer": ANSWER_TEXT,
    "claims": [{"text": "HbA1c was 7.2%", "citations": [1]}],
    "uncertainty": None,
}


def evidence_item(event_id="ev-1"):
    return Evidence(document_id="d", page=1, text=EVIDENCE_TEXT, event_ids=[event_id],
                    relevance_score=1.0, source="temporal")


@pytest.fixture
def world(monkeypatch):
    metrics.registry.reset()
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    for table in (User, Document, HealthEvent, Provenance, AITrace):
        table.__table__.create(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    me = User(id=__import__("uuid").uuid4(), email="me@example.com", hashed_password="x")
    db.add(me)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: me

    state = {"evidence": [evidence_item()], "provider": FakeProvider(GOOD_REPLY)}
    monkeypatch.setattr(
        "services.reasoning.generate.build_context",
        lambda db_, user_id, question: AssistantContext(
            question=question, intent="RETRIEVAL", evidence=state["evidence"]))
    monkeypatch.setattr("services.reasoning.generate.get_llm_provider",
                        lambda: state["provider"])
    yield db, me, state, TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()
    db.close()


def ask(client, question=QUESTION, request_id=None):
    headers = {"X-Request-ID": request_id} if request_id else {}
    return client.post("/api/v1/assistant/ask", json={"question": question}, headers=headers)


def only_trace(db):
    rows = db.query(AITrace).all()
    assert len(rows) == 1, rows
    return rows[0]


def test_an_answered_call_records_everything_worth_knowing_about_it(world):
    db, me, state, client = world
    assert ask(client, request_id="req-trace-1").status_code == 200
    row = only_trace(db)
    assert row.user_id == me.id and row.request_id == "req-trace-1"
    assert (row.provider, row.model, row.prompt_version) == ("ollama", "llama3.2:3b",
                                                            PROMPT_VERSION)
    assert (row.input_tokens, row.output_tokens, row.token_usage) == (321, 45, 366)
    assert row.retrieval_event_ids == ["ev-1"] and row.evidence_count == 1
    assert row.claim_labels == {"SUPPORTED": 1} and row.verification_result == "all_supported"
    assert row.safety_action == "ALLOW" and row.status == "ok"
    assert row.intent == "RETRIEVAL" and row.question_chars == len(QUESTION)
    assert row.llm_ms is not None and row.latency_ms >= row.llm_ms >= 0


def test_nothing_from_the_question_the_answer_or_the_evidence_is_stored(world):
    db, _, _, client = world
    ask(client)
    row = only_trace(db)
    everything = json.dumps({c.name: str(getattr(row, c.name)) for c in AITrace.__table__.columns})
    for secret in (QUESTION, "HbA1c", ANSWER_TEXT, EVIDENCE_TEXT, "7.2"):
        assert secret not in everything, secret
    columns = {c.name for c in AITrace.__table__.columns}
    assert not columns & {"question", "answer", "evidence", "prompt", "quote", "text", "claims"}


def test_the_migrated_table_has_no_column_for_the_question_either(world):
    db, *_ = world
    names = {c["name"] for c in inspect(db.get_bind()).get_columns("ai_traces")}
    assert "question" not in names and "question_chars" in names


def test_a_blocked_question_is_traced_without_retrieval_or_a_model(world):
    db, _, _, client = world
    ask(client, "Should I stop taking my metformin?")
    row = only_trace(db)
    assert row.status == "blocked" and row.safety_action in ("ABSTAIN", "REDIRECT_TO_PROFESSIONAL")
    assert row.provider == "none" and row.model is None and row.llm_ms is None
    assert row.evidence_count is None and row.input_tokens is None


def test_no_evidence_is_its_own_status_and_the_model_is_not_called(world):
    db, _, state, client = world
    state["evidence"] = []
    state["provider"] = FakeProvider(error=AssertionError("the model must not be called"))
    assert ask(client).status_code == 200
    row = only_trace(db)
    assert row.status == "no_evidence" and row.evidence_count == 0 and row.llm_ms is None


def test_a_provider_failure_is_traced_and_the_user_still_gets_a_reply(world):
    db, _, state, client = world
    state["provider"] = FakeProvider(error=LLMProviderError("ollama request failed (secret)"))
    response = ask(client)
    assert response.status_code == 200 and "unavailable" in response.json()["answer"].lower()
    row = only_trace(db)
    assert row.status == "provider_error" and row.llm_ms is not None
    assert row.input_tokens is None


def test_a_crash_is_traced_as_an_error_and_is_a_500(world):
    db, _, state, client = world
    state["provider"] = FakeProvider(error=RuntimeError("boom"))
    assert ask(client).status_code == 500
    assert only_trace(db).status == "error"


def test_a_failure_to_save_the_trace_never_breaks_the_answer(world, caplog):
    db, _, _, client = world
    AITrace.__table__.drop(db.get_bind())  # the write will fail
    with caplog.at_level(logging.WARNING, logger="app.tracing"):
        response = ask(client)
    assert response.status_code == 200 and response.json()["answer"] == ANSWER_TEXT
    record = next(r for r in caplog.records if r.message == "ai_trace_write_failed")
    assert record.error_type == "OperationalError"
    assert db.query(User).count() == 1  # the failed write was rolled back: the session still works


def test_flagged_claims_are_counted_by_label_and_never_logged_in_words(world, caplog):
    db, _, state, client = world
    state["provider"] = FakeProvider({
        "answer": "Your HbA1c was 9.9.",
        "claims": [{"text": "Your HbA1c was 9.9%", "citations": [1]},
                   {"text": "Your diabetes is well controlled", "citations": []}],
        "uncertainty": None})
    with caplog.at_level(logging.WARNING):
        ask(client)
    row = only_trace(db)
    assert row.claim_labels == {"CONTRADICTED": 1, "INSUFFICIENT": 1}
    assert row.verification_result == "flagged"
    flagged = next(r for r in caplog.records if r.message == "verification_gate_flagged")
    assert flagged.flagged_claims == 2
    assert "9.9" not in app_log_text([r for r in caplog.records if r.name == "app.verification"])
    assert "controlled" not in " ".join(str(r.__dict__) for r in caplog.records)


def test_each_request_gets_its_own_trace(world):
    db, _, _, client = world
    ask(client, request_id="req-a")
    ask(client, "Which medications am I taking?", request_id="req-b")
    rows = {r.request_id: r for r in db.query(AITrace).all()}
    assert set(rows) == {"req-a", "req-b"}
    assert rows["req-a"].question_chars != rows["req-b"].question_chars


def test_the_stages_are_timed_for_the_metrics_endpoint(world):
    _, _, _, client = world
    ask(client)
    snapshot = metrics.registry.snapshot()
    for stage in ("retrieval", "llm", "verification"):
        assert snapshot[stage]["count"] == 1, stage


def test_the_one_word_summary_of_how_the_claims_fared():
    assert tracing.verification_summary({}) == "no_claims"
    assert tracing.verification_summary({"SUPPORTED": 3}) == "all_supported"
    assert tracing.verification_summary({"SUPPORTED": 2, "UNSUPPORTED": 1}) == "flagged"


def test_no_trace_means_nothing_is_written(world):
    db, me, *_ = world
    tracing.write_ai_trace(db, me.id, None, 5)
    assert db.query(AITrace).count() == 0


def test_a_finished_request_leaves_no_trace_active_for_whatever_runs_next(world):
    from packages.observability import trace as trace_module
    from services.api.app.api.v1.assistant import AskRequest
    from services.api.app.api.v1.assistant import ask as ask_route

    db, me, *_ = world
    ask_route(AskRequest(question=QUESTION), db, me)  # called directly: same thread, same context
    assert trace_module.current_trace() is None


def test_an_answer_the_output_guard_replaces_shows_no_sources_and_is_traced_as_blocked(world):
    db, _, state, client = world
    state["provider"] = FakeProvider({
        "answer": "You have diabetes and should take 20 units.",
        "claims": [{"text": "You have diabetes", "citations": [1]}],
        "uncertainty": None})
    body = ask(client).json()
    assert body["safety_action"] in ("ABSTAIN", "REDIRECT_TO_PROFESSIONAL")
    assert body["evidence"] == [] and "diabetes" not in body["answer"].lower()
    row = only_trace(db)
    # the question was allowed, so the first decision was ALLOW; the guard's decision is what counts
    assert row.status == "blocked" and row.safety_action == body["safety_action"]
    assert row.evidence_count == 1  # retrieval did run; the answer was what got replaced


def test_the_prompt_version_is_tied_to_the_prompt_text():
    """Change the prompt and this fails: bump PROMPT_VERSION in services/reasoning/generate.py
    (traces record it, so a change in behaviour can be tied to the prompt) and update both here."""
    import hashlib

    from services.reasoning.generate import _SYSTEM_PROMPT

    assert hashlib.sha256(_SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:16] == "a069d3783a97c047"
    assert PROMPT_VERSION == "answer-v2"
