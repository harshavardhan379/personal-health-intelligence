"""The full system (YGH-71): the product's pipeline run on benchmark patients."""

import json
import re
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from evaluation.contracts import AskedQuestion, OracleEvent, PatientRecord, PredictedEvent
from evaluation.loader import patient_record
from evaluation.report import dig
from evaluation.runner import RunConfig, run_benchmark
from evaluation.systems import REGISTRY, full, get_system
from evaluation.systems.full import (
    ABSENCE_EXPLANATION,
    FullSystem,
    ProviderUnavailable,
    check_benchmark_url,
    ensure_benchmark_database,
    run_product_pipeline,
    store_patient,
    to_system_answer,
)
from evaluation.writer import write_dataset
from packages.schemas.answer import Answer, Claim
from packages.schemas.evidence import Evidence
from packages.schemas.models import Embedding, HealthEvent, Provenance
from packages.schemas.verification import ClaimVerification, VerifiedAnswer
from services.reasoning.providers import LLMProviderError
from services.verification.match import match_claim_to_evidence
from services.verification.verify import generate_verified_answer

TEST_URL = "postgresql+psycopg://phi:phi_dev_password@localhost:5433/phi_test_bench"


# --- the product's shapes, built by hand --------------------------------------------------
def evidence(event_ids, text_="2023-05-17 HbA1c 9.8 %"):
    return Evidence(document_id="d", page=1, text=text_, event_ids=event_ids,
                    relevance_score=1.0, source="structured")


def verified(claims, safety_action="ALLOW", answer="An answer."):
    return VerifiedAnswer(
        answer=answer, claim_verifications=claims, evidence_ids=[], intent="LOOKUP",
        fully_verified=all(c.label == "SUPPORTED" for c in claims), safety_category="X",
        safety_action=safety_action)


def claim(text_, label="SUPPORTED", cites=(1,), explanation=None):
    return ClaimVerification(claim_text=text_, label=label, citation_indices=list(cites),
                             explanation=explanation)


ABSENT = ABSENCE_EXPLANATION + ", and it does not appear in the retrieved evidence."


# --- conversion: pure logic ---------------------------------------------------------------
def test_claims_cite_the_benchmark_ids_of_the_evidence_they_point_at():
    ev = [evidence(["u1"]), evidence(["u2", "u3"]), evidence(["u4"])]
    answer = to_system_answer(
        verified([claim("a", cites=(2,)), claim("b", cites=(1, 2)), claim("c", cites=())]),
        ev, {"u1": "p1-e001", "u2": "p1-e002", "u3": "p1-e003", "u4": "p1-e004"})
    assert [c.evidence_ids for c in answer.claims] == [
        ["p1-e002", "p1-e003"], ["p1-e001", "p1-e002", "p1-e003"], []]
    assert [e.event_ids for e in answer.evidence] == [["p1-e001"], ["p1-e002", "p1-e003"],
                                                       ["p1-e004"]]
    assert answer.evidence[0].text == "2023-05-17 HbA1c 9.8 %"


def test_a_citation_pointing_outside_the_evidence_is_ignored_and_an_unknown_id_is_marked():
    answer = to_system_answer(verified([claim("a", cites=(0, 5, 1))]), [evidence(["zzz"])], {})
    assert answer.claims[0].evidence_ids == ["unknown:zzz"]


def test_verifier_labels_and_the_safety_action_are_carried_over():
    answer = to_system_answer(
        verified([claim("a"), claim("b", label="UNSUPPORTED")], safety_action="ALLOW"),
        [evidence(["u1"])], {"u1": "e1"})
    assert answer.claim_labels == ["SUPPORTED", "UNSUPPORTED"]
    assert answer.safety_action == "ALLOW" and not answer.abstained


def test_each_blocking_safety_action_is_an_abstention():
    for action in ("CLARIFY", "ABSTAIN", "REDIRECT_TO_PROFESSIONAL"):
        assert to_system_answer(verified([], safety_action=action), [], {}).abstained, action
        # also when the guard replaced an answer that had retrieved evidence
        assert to_system_answer(verified([], safety_action=action), [evidence(["u"])],
                                {"u": "e"}).abstained, action
    assert not to_system_answer(verified([claim("a")], safety_action="ALLOW_WITH_UNCERTAINTY"),
                                [evidence(["u"])], {"u": "e"}).abstained


def test_nothing_retrieved_is_an_abstention_even_if_the_model_said_something():
    assert to_system_answer(verified([claim("a")]), [], {}).abstained


def test_only_verified_absence_claims_make_an_abstention():
    ev, ids = [evidence(["u"])], {"u": "e"}
    only_absence = verified([claim("No cholesterol is reported.", explanation=ABSENT, cites=())])
    assert to_system_answer(only_absence, ev, ids).abstained

    mixed = verified([claim("No cholesterol is reported.", explanation=ABSENT, cites=()),
                      claim("HbA1c was 9.8.")])
    assert not to_system_answer(mixed, ev, ids).abstained

    contradicted = verified([claim("No HbA1c is reported.", label="CONTRADICTED",
                                   explanation="Claim says this is not documented, but the "
                                               "retrieved evidence does mention it.")])
    assert not to_system_answer(contradicted, ev, ids).abstained

    assert not to_system_answer(verified([claim("HbA1c was 9.8.")]), ev, ids).abstained


def test_the_absence_wording_matches_what_the_products_verifier_really_says():
    result = match_claim_to_evidence(
        Claim(text="Cholesterol level is not reported in the evidence.", citation_indices=[]), [])
    assert result.label == "SUPPORTED" and result.explanation.startswith(ABSENCE_EXPLANATION)


# --- the pipeline is the product's own ----------------------------------------------------
def _stub_generation(monkeypatch, answer_text):
    """Both the product's function and ours call this stand-in instead of the database + model."""
    ev = [evidence(["u1"])]
    answer = Answer(answer=answer_text,
                    claims=[Claim(text=answer_text, citation_indices=[1])],
                    evidence_ids=["u1"], uncertainty=None, intent="LOOKUP")
    context = SimpleNamespace(evidence=ev)

    def stand_in(db, user_id, question):
        return answer, context

    monkeypatch.setattr("services.verification.verify.generate_answer_with_context", stand_in)
    monkeypatch.setattr(full, "generate_answer_with_context", stand_in)


@pytest.mark.parametrize("question, answer_text", [
    ("What was my HbA1c in May 2023?", "Your HbA1c was 9.8 % on 2023-05-17."),   # allowed
    ("Should I stop taking my metformin?", "Stop it."),                           # blocked first
    ("What was my HbA1c in May 2023?", "You have diabetes and should take 20 units."),  # guard
    ("hi", "x"),                                                                  # too short
])
def test_the_pipeline_gives_exactly_the_products_answer(monkeypatch, question, answer_text):
    _stub_generation(monkeypatch, answer_text)
    user = uuid.uuid4()
    theirs = generate_verified_answer(None, user, question)
    ours, _ = run_product_pipeline(None, user, question)
    assert ours == theirs


def test_a_blocked_question_never_reaches_retrieval_or_the_model(monkeypatch):
    monkeypatch.setattr(full, "generate_answer_with_context",
                        lambda *a: pytest.fail("a blocked question must not be generated"))
    verified_answer, ev = run_product_pipeline(None, uuid.uuid4(),
                                               "Should I stop taking my metformin?")
    assert ev == [] and verified_answer.safety_action in ("ABSTAIN", "REDIRECT_TO_PROFESSIONAL")


def test_an_llm_failure_swallowed_by_the_product_is_raised_so_the_runner_counts_it(monkeypatch):
    def failing(db, user_id, question):
        return (Answer(answer="The assistant is temporarily unavailable.", claims=[],
                       evidence_ids=[], uncertainty="Provider error: ollama down",
                       intent="LOOKUP"), SimpleNamespace(evidence=[]))
    monkeypatch.setattr(full, "generate_answer_with_context", failing)
    with pytest.raises(ProviderUnavailable):
        run_product_pipeline(None, uuid.uuid4(), "What was my HbA1c in May 2023?")


# --- the benchmark database is never the product's ---------------------------------------
def test_only_databases_named_bench_are_accepted():
    assert check_benchmark_url(TEST_URL) == "phi_test_bench"
    for bad in ("postgresql+psycopg://u:p@localhost:5433/phi",
                "postgresql+psycopg://u:p@localhost:5433/phi_bench_backup",
                "postgresql+psycopg://u:p@localhost:5433/",
                'postgresql+psycopg://u:p@localhost:5433/x"; DROP DATABASE phi; --_bench'):
        with pytest.raises(ValueError):
            check_benchmark_url(bad)


def test_the_system_refuses_the_product_database_before_touching_anything():
    with pytest.raises(ValueError):
        FullSystem(database_url="postgresql+psycopg://phi:phi_dev_password@localhost:5433/phi")


def test_registered_with_both_modes_and_needs_no_database_to_construct():
    assert "full" in REGISTRY
    system = get_system("full")
    assert system.modes == ("ORACLE", "EXTRACTED") and system.cites_events is True
    assert system.model_name.startswith("ollama:")


# --- against a real Postgres ------------------------------------------------------------
@pytest.fixture(scope="module")
def engine():
    try:
        return ensure_benchmark_database(TEST_URL)
    except Exception as exc:  # no Postgres here: the DB-backed tests are skipped
        pytest.skip(f"benchmark Postgres unavailable: {exc.__class__.__name__}")


@pytest.fixture
def db(engine):
    from sqlalchemy.orm import sessionmaker
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    yield session
    session.close()


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("full") / "v1"
    write_dataset(out)
    return out


class ScriptedProvider:
    name = "scripted"

    def __init__(self, payload):
        self.payload = payload

    def generate(self, messages):
        if isinstance(self.payload, Exception):
            raise self.payload
        return json.dumps(self.payload)


def script(monkeypatch, payload):
    monkeypatch.setattr("services.reasoning.generate.get_llm_provider",
                        lambda: ScriptedProvider(payload))


def oracle_event(event_id, event_type, day, data, precision="day"):
    return OracleEvent(event_id, event_type, day, precision, data, "doc-1", 2, f"quote {event_id}")


def test_events_are_stored_confirmed_with_provenance_and_embeddings_and_reload_cleanly(db):
    rows = [("a1", SimpleNamespace(event_type="lab", event_date=date(2024, 3, 2),
                                   date_precision="day", data={"test_name": "HbA1c", "value": 7.1,
                                                               "unit": "%"},
                                   document_key="d1", page=2, quote="HbA1c: 7.1 %",
                                   document_index=0)),
            ("a2", SimpleNamespace(event_type="symptom", event_date=None, date_precision="none",
                                   data={"symptom": "fatigue"}, document_key="d1", page=1,
                                   quote="fatigue", document_index=0))]
    for _ in range(2):  # the second load replaces the first
        user_id, id_map, not_storable = store_patient(db, "ptest", "ORACLE", rows)
        assert not_storable == 1 and list(id_map.values()) == ["a1"]
        events = db.query(HealthEvent).filter(HealthEvent.user_id == user_id).all()
        assert [e.review_status for e in events] == ["confirmed"]
        assert db.query(Provenance).filter(Provenance.health_event_id == events[0].id).one() \
            .quote == "HbA1c: 7.1 %"
        assert db.query(Embedding).filter(Embedding.user_id == user_id).count() == 1
    assert db.execute(text("SELECT count(*) FROM users WHERE email LIKE 'ptest.%'")).scalar() == 1


def test_oracle_answers_cite_truth_ids_and_a_blocked_question_abstains(db, dataset, monkeypatch):
    record = patient_record(dataset, "p001", "ORACLE")
    system = FullSystem(database_url=TEST_URL)
    script(monkeypatch, {"answer": "Your HbA1c was 9.8 % on 2023-05-17 [1].",
                         "claims": [{"text": "HbA1c was 9.8 % on 2023-05-17", "citations": [1]}],
                         "uncertainty": None})
    answer = system.answer(record, AskedQuestion("q", "What was my HbA1c on 17 May 2023?"),
                           "ORACLE")
    assert answer.evidence and not answer.abstained
    assert all(re.fullmatch(r"p001-e\d+", i) for e in answer.evidence for i in e.event_ids)
    assert answer.claims[0].evidence_ids and answer.claim_labels

    blocked = system.answer(record, AskedQuestion("q2", "Should I stop taking my metformin?"),
                            "ORACLE")
    assert blocked.abstained and blocked.evidence == [] and blocked.safety_action
    assert system.diagnostics["blocked_by_safety"] == 1 and system.diagnostics["answered"] == 1


def test_undated_events_are_counted_not_stored(db, dataset):
    undated = [e for e in patient_record(dataset, "p001", "ORACLE").oracle_events
               if e.event_date is None]
    record = PatientRecord("pu", (), (
        oracle_event("pu-e1", "lab", date(2024, 1, 1),
                     {"test_name": "HbA1c", "value": 7.0, "unit": "%"}),
        oracle_event("pu-e2", "lab", None, {"test_name": "Hb", "value": 14, "unit": "g/dL"},
                     "none")))
    system = FullSystem(database_url=TEST_URL)
    system._prepare(db, record, "ORACLE")
    assert system.diagnostics["events_stored_oracle"] == 1
    assert system.diagnostics["events_not_storable"] == 1
    assert undated is not None


def test_a_provider_outage_is_an_error_the_runner_counts_not_an_answer(db, dataset, monkeypatch):
    record = patient_record(dataset, "p001", "ORACLE")
    script(monkeypatch, LLMProviderError("down"))
    with pytest.raises(ProviderUnavailable):
        FullSystem(database_url=TEST_URL).answer(
            record, AskedQuestion("q", "What was my HbA1c on 17 May 2023?"), "ORACLE")


def test_a_benchmark_run_in_both_modes_maps_extracted_ids_to_the_truth(dataset, monkeypatch,
                                                                       engine):
    script(monkeypatch, {"answer": "It was 9.8 [1].",
                         "claims": [{"text": "It was 9.8", "citations": [1]}],
                         "uncertainty": None})
    system = FullSystem(database_url=TEST_URL)
    result = run_benchmark(RunConfig(dataset_dir=dataset, limit_patients=3, score_pipeline=False),
                           [system])
    for mode in ("ORACLE", "EXTRACTED"):
        retrieval = result.metrics["systems"]["full"][mode]["retrieval"]["overall"]
        assert dig(retrieval, ("recall_at_k", "5"))["n"] > 0, mode
    oracle = dig(result.metrics["systems"]["full"]["ORACLE"]["retrieval"]["overall"],
                 ("recall_at_k", "5"))
    extracted = dig(result.metrics["systems"]["full"]["EXTRACTED"]["retrieval"]["overall"],
                    ("recall_at_k", "5"))
    assert 0 < oracle["successes"] and extracted["successes"] < oracle["successes"]
    ids = {i for a in result.answers if a["mode"] == "EXTRACTED"
           for e in a["evidence"] for i in e["event_ids"]}
    assert ids and not any(re.fullmatch(r"x\d+", i) for i in ids)
    assert result.metrics["systems"]["full"]["ORACLE"]["errors"] == 0
    stored = system.diagnostics
    assert stored["events_stored_oracle"] > stored["events_stored_extracted"]


def test_extracted_events_carry_the_pipelines_provenance_into_the_store(db):
    extracted = [PredictedEvent("lab", date(2024, 3, 2), "day",
                                {"test_name": "HbA1c", "value": 7.1, "unit": "%"}, 1, 3,
                                "HbA1c: 7.1 % (extracted provenance test)")]
    system = FullSystem(database_url=TEST_URL, extract=lambda patient: extracted)
    system._prepare(db, PatientRecord("pe", ()), "EXTRACTED")
    event = db.query(HealthEvent).join(Provenance, Provenance.health_event_id == HealthEvent.id) \
        .filter(Provenance.quote.like("%extracted provenance test%")).first()
    assert event is not None
    provenance = db.query(Provenance).filter(Provenance.health_event_id == event.id).one()
    assert provenance.page_number == 3


def test_a_patient_is_stored_once_however_many_questions_are_asked(db, dataset, monkeypatch):
    record = patient_record(dataset, "p002", "ORACLE")
    script(monkeypatch, {"answer": "x", "claims": [], "uncertainty": None})
    system = FullSystem(database_url=TEST_URL)
    for n in range(3):
        system.answer(record, AskedQuestion(f"q{n}", "What was my HbA1c in 2023?"), "ORACLE")
    dated = [e for e in record.oracle_events if e.event_date is not None]
    assert system.diagnostics["events_stored_oracle"] == len(dated)


def test_citation_number_zero_never_wraps_around_to_the_last_evidence_item():
    two = [evidence(["u1"]), evidence(["u2"])]
    answer = to_system_answer(verified([claim("a", cites=(0,))]), two, {"u1": "e1", "u2": "e2"})
    assert answer.claims[0].evidence_ids == []


def test_only_a_supported_claim_can_be_a_verified_absence():
    odd = verified([claim("No cholesterol is reported.", label="UNSUPPORTED",
                          explanation=ABSENT, cites=())])
    assert not to_system_answer(odd, [evidence(["u"])], {"u": "e"}).abstained


def test_a_benchmark_database_built_before_a_column_was_added_is_rebuilt(engine):
    """phi_bench is a scratch copy of the product's tables; when the product gains a column the
    old copy must not be reused (it would fail on the first insert)."""
    from sqlalchemy import text

    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE documents DROP COLUMN failed_stage"))
    rebuilt = ensure_benchmark_database(TEST_URL)
    from sqlalchemy import inspect
    assert "failed_stage" in {c["name"] for c in inspect(rebuilt).get_columns("documents")}
