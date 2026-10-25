"""Four defects found by using the assistant on the live stack (M15 follow-up):

1. "What is my latest HbA1c?" got the whole history oldest-first, and a small model read the first
   item (the January value) while the April value was at position 7.
2. The verifier called that wrong claim "Supported", because 7.2% is in the cited evidence.
3. The model's uncertainty text "None noted" was shown as a "What is uncertain" box.
4. A correct "I couldn't find your cholesterol result" was labelled "Not supported".
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.schemas.answer import Answer, Claim
from packages.schemas.evidence import Evidence
from packages.schemas.models import Document, HealthEvent, Provenance, User
from services.reasoning.context_builder import build_context
from services.reasoning.generate import _clean_uncertainty
from services.reasoning.intent_classifier import classify_intent
from services.retrieval.temporal import search_latest, topic_words, wants_latest
from services.verification.match import match_claim_to_evidence
from services.verification.verify import verify_answer


# --- a small database --------------------------------------------------------------------
@pytest.fixture
def world():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    for table in (User, Document, HealthEvent, Provenance):
        table.__table__.create(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    me, other = (User(id=uuid.uuid4(), email=f"{n}@example.com", hashed_password="x")
                 for n in ("me", "other"))
    db.add_all([me, other])
    db.commit()
    docs = {}
    for user in (me, other):
        docs[user.id] = Document(id=uuid.uuid4(), user_id=user.id, filename="r.pdf",
                                 mime_type="application/pdf", storage_path="x",
                                 processing_status="processed")
        db.add(docs[user.id])
    db.commit()
    yield db, me, other, docs
    db.close()


def add(db, user, docs, etype, day, data, status="confirmed"):
    event = HealthEvent(id=uuid.uuid4(), user_id=user.id, document_id=docs[user.id].id,
                        event_type=etype, event_date=day, event_date_precision="day",
                        data=data, confidence=0.9, review_status=status)
    db.add(event)
    db.flush()
    db.add(Provenance(health_event_id=event.id, document_id=docs[user.id].id, page_number=1,
                      quote="q"))
    db.commit()
    return event


def hba1c(value):
    return {"test_name": "HbA1c", "value": value, "unit": "%"}


@pytest.fixture
def history(world):
    db, me, other, docs = world
    add(db, me, docs, "lab", date(2026, 1, 10), hba1c(7.2))
    add(db, me, docs, "symptom", date(2026, 1, 10), {"symptom": "headache"})
    add(db, me, docs, "medication", date(2026, 1, 10),
        {"medication": "Metformin", "dose": "500mg"})
    add(db, me, docs, "lab", date(2026, 4, 10), hba1c(6.5))
    add(db, me, docs, "medication", date(2026, 4, 10),
        {"medication": "Metformin", "dose": "1000mg"})
    add(db, me, docs, "encounter", date(2026, 4, 10), {"encounter_type": "Follow-up Visit"})
    return db, me, other, docs


# --- 1. retrieval ------------------------------------------------------------------------
def test_a_latest_question_gets_only_the_matching_facts_newest_first(history):
    db, me, *_ = history
    evidence = search_latest(db, me.id, "What is my latest HbA1c?")
    assert [e.text for e in evidence] == ["2026-04-10: HbA1c: 6.5 %", "2026-01-10: HbA1c: 7.2 %"]


def test_the_newest_fact_is_first_whatever_order_they_were_added_in(world):
    db, me, _, docs = world
    for day, value in ((date(2026, 3, 1), 8.0), (date(2026, 9, 1), 5.1), (date(2026, 6, 1), 6.0)):
        add(db, me, docs, "lab", day, hba1c(value))
    texts = [e.text for e in search_latest(db, me.id, "my latest HbA1c")]
    assert texts == ["2026-09-01: HbA1c: 5.1 %", "2026-06-01: HbA1c: 6.0 %",
                     "2026-03-01: HbA1c: 8.0 %"]


def test_a_topic_nothing_matches_gives_no_evidence_so_the_assistant_abstains(history):
    db, me, *_ = history
    assert search_latest(db, me.id, "What was my latest cholesterol result?") == []


def test_a_question_naming_no_topic_gets_the_newest_facts_overall(history):
    db, me, *_ = history
    evidence = search_latest(db, me.id, "What is my latest result?", limit=3)
    assert len(evidence) == 3 and all(e.text.startswith("2026-04-10") for e in evidence)


def test_only_confirmed_facts_of_this_user_are_used(history):
    db, me, other, docs = history
    add(db, me, docs, "lab", date(2026, 6, 1), hba1c(5.0), status="pending")
    add(db, me, docs, "lab", date(2026, 7, 1), hba1c(5.5), status="rejected")
    add(db, other, docs, "lab", date(2026, 8, 1), hba1c(9.9))
    texts = [e.text for e in search_latest(db, me.id, "What is my latest HbA1c?")]
    assert texts[0] == "2026-04-10: HbA1c: 6.5 %" and len(texts) == 2


def test_the_limit_keeps_the_newest_not_the_oldest(world):
    db, me, _, docs = world
    for month in range(1, 13):
        add(db, me, docs, "lab", date(2026, month, 1), hba1c(float(month)))
    texts = [e.text for e in search_latest(db, me.id, "latest HbA1c", limit=3)]
    assert texts == ["2026-12-01: HbA1c: 12.0 %", "2026-11-01: HbA1c: 11.0 %",
                     "2026-10-01: HbA1c: 10.0 %"]


def test_evidence_carries_the_event_id_and_the_source_page(history):
    db, me, *_ = history
    first = search_latest(db, me.id, "latest HbA1c")[0]
    assert len(first.event_ids) == 1 and first.page == 1 and first.source == "temporal"


@pytest.mark.parametrize("question", [
    "What is my latest HbA1c?", "What was my most recent HbA1c result?",
    "Show my newest lab result", "When was the last time I had a fever?"])
def test_these_are_latest_questions(question):
    assert wants_latest(question)


@pytest.mark.parametrize("question", [
    "When did I first report a fever?", "What changed since January?",
    "When did I start Metformin?", "What is the history of my HbA1c?",
    "What changed in my recent reports?", "What is my HbA1c?",
    "What is my latest HbA1c since January?", "What were my first and latest HbA1c?"])
def test_these_are_not_latest_questions(question):
    assert not wants_latest(question)


def test_the_topic_is_what_is_asked_about_not_how_it_is_asked():
    assert topic_words("What is my latest HbA1c?") == ["hba1c"]
    assert topic_words("What was my most recent Metformin dose?") == ["metformin", "dose"]
    assert topic_words("What is my latest result?") == []


def test_most_recent_and_newest_now_route_to_the_dated_path():
    for question in ("What was my most recent HbA1c result?", "Show my newest result"):
        assert classify_intent(question) == "TEMPORAL", question


def test_the_context_builder_uses_the_latest_path_for_latest_and_history_for_first(history):
    db, me, *_ = history
    latest = build_context(db, me.id, "What is my latest HbA1c?")
    assert latest.intent == "TEMPORAL"
    assert [e.text for e in latest.evidence][0] == "2026-04-10: HbA1c: 6.5 %"
    assert len(latest.evidence) == 2
    first = build_context(db, me.id, "When was headache first documented?")
    assert first.evidence[0].text.startswith("2026-01-10")  # oldest first, the whole history
    assert len(first.evidence) == 6


# --- 2. the verifier and recency ---------------------------------------------------------
def ev(text):
    return Evidence(document_id="d", page=1, text=text, event_ids=["e"], relevance_score=1.0,
                    source="temporal")


JAN = ev("2026-01-10: HbA1c: 7.2 %")
APR = ev("2026-04-10: HbA1c: 6.5 %")


def check(text, cites, evidence):
    return match_claim_to_evidence(Claim(text=text, citation_indices=cites), evidence)


def test_calling_an_older_fact_the_latest_is_contradicted_and_names_the_newer_date():
    result = check("The most recent HbA1c reading is 7.2%", [1], [JAN, APR])
    assert result.label == "CONTRADICTED" and "2026-04-10" in result.explanation


def test_calling_the_newest_fact_the_latest_is_supported():
    assert check("Your latest HbA1c is 6.5%", [2], [JAN, APR]).label == "SUPPORTED"
    assert check("Your latest HbA1c is 6.5%", [1], [APR, JAN]).label == "SUPPORTED"


def test_every_wording_of_recency_is_checked():
    for claim in ("Your latest HbA1c is 7.2%", "The newest HbA1c is 7.2%",
                  "Your current HbA1c is 7.2%", "The most recent HbA1c was 7.2%"):
        assert check(claim, [1], [JAN, APR]).label == "CONTRADICTED", claim


def test_a_newer_fact_about_something_else_does_not_make_the_claim_stale():
    other = ev("2026-04-10: Metformin 1000mg")
    assert check("Your latest HbA1c is 7.2%", [1], [JAN, other]).label == "SUPPORTED"


def test_evidence_without_dates_gets_the_ordinary_check():
    plain = [ev("HbA1c: 7.2 %"), ev("HbA1c: 6.5 %")]
    assert check("Your latest HbA1c is 7.2%", [1], plain).label == "SUPPORTED"


def test_the_structured_evidence_format_with_the_date_at_the_end_works_too():
    jan = ev("HbA1c: 7.2 % (ref 4.0-5.6) 2026-01-10")
    apr = ev("HbA1c: 6.5 % (ref 4.0-5.6) 2026-04-10")
    assert check("Your latest HbA1c is 7.2%", [1], [jan, apr]).label == "CONTRADICTED"


def test_last_year_is_not_a_recency_claim():
    result = check("Your HbA1c was 7.2% last year", [1], [JAN, APR])
    assert "more recent" not in (result.explanation or "")


def test_a_claim_that_cites_nothing_is_still_insufficient():
    assert check("Your latest HbA1c is 6.5%", [], [JAN, APR]).label == "INSUFFICIENT"


def test_the_whole_gate_flags_the_wrong_latest_value_the_live_run_produced():
    answer = Answer(answer="Your latest HbA1c is 7.2%.",
                    claims=[Claim(text="The most recent HbA1c reading is 7.2%",
                                  citation_indices=[1])],
                    evidence_ids=["e"], uncertainty=None, intent="TEMPORAL")
    verified = verify_answer(answer, [JAN, APR])
    assert not verified.fully_verified
    assert verified.claim_verifications[0].label == "CONTRADICTED"
    assert "not fully verified" in verified.uncertainty.lower()


# --- 4. "couldn't find" is an absence claim ---------------------------------------------
def test_a_true_could_not_find_is_supported_as_an_absence():
    result = check("I couldn't find your latest cholesterol result in the provided evidence.",
                   [4], [JAN, APR])
    assert result.label == "SUPPORTED"
    assert result.explanation.startswith("Claim states the topic is absent")


@pytest.mark.parametrize("phrase", [
    "I couldn't find", "I could not find", "I can't find", "I cannot find", "I am unable to find",
    "I was not able to find", "I did not find", "I didn't find"])
def test_every_wording_of_not_finding_is_recognised(phrase):
    result = check(f"{phrase} your cholesterol result.", [], [JAN, APR])
    assert result.label == "SUPPORTED" and "absent" in result.explanation, phrase


def test_a_false_could_not_find_is_contradicted_when_the_topic_is_in_the_evidence():
    result = check("I couldn't find your HbA1c in the records.", [], [JAN, APR])
    assert result.label == "CONTRADICTED"


def test_ordinary_claims_with_did_not_are_not_mistaken_for_absence_claims():
    for claim in ("Your HbA1c did not change", "You did not stop Metformin",
                  "You didn't miss a dose"):
        result = check(claim, [1], [JAN, ev("Metformin 500mg twice daily")])
        assert "absent" not in (result.explanation or ""), claim


# --- 3. an uncertainty that says nothing ------------------------------------------------
@pytest.mark.parametrize("empty", [
    None, "", "null", "None", "none.", "N/A", "n/a", "nil", "None noted", "None noted.",
    "no uncertainty", "No uncertainty.", "No uncertainties", "Nothing to report", "None identified",
    "Not applicable", "No significant uncertainty", "  none noted  ", '"None noted"'])
def test_these_mean_nothing_is_uncertain(empty):
    assert _clean_uncertainty(empty) is None


@pytest.mark.parametrize("real", [
    "Only two results are on file.", "None of the dates are confirmed.",
    "No HbA1c result is on file for 2023.", "Not all medications are listed.",
    "The trend is based on two points."])
def test_real_uncertainty_is_kept_as_written(real):
    assert _clean_uncertainty(real) == real
