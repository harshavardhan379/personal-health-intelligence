"""Fact validation (YGH-45) and the shared user-scoped queries (YGH-14, YGH-39).

Validation is the last gate before an extracted fact is stored: every rule has a case that breaks
it. The query helpers are where "only this user's, only confirmed" is written once; they are tested
with two users so the isolation is a checked property, not a convention."""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.schemas.extraction import ExtractedFact
from packages.schemas.models import Document, HealthEvent, User
from packages.schemas.queries import (
    CONFIRMED_STATUSES,
    confirmed_events_query,
    user_documents_query,
    user_owns_document,
)
from services.extraction.validate import ValidationError, validate_fact, validate_facts

DAY = date(2026, 1, 10)


def fact(event_type="lab", data=None, day=DAY, quote="HbA1c: 7.2 % 2026-01-10"):
    defaults = {
        "lab": {"test_name": "HbA1c", "value": 7.2, "unit": "%"},
        "medication": {"medication": "Metformin", "dose": "500mg"},
        "encounter": {"encounter_type": "Annual physical"},
        "symptom": {"symptom": "headache"},
    }
    return ExtractedFact(event_type=event_type, event_date=day,
                         data=defaults.get(event_type, {}) if data is None else data,
                         confidence=0.9, source_page=1, quote=quote)


def rejected(**kwargs):
    with pytest.raises(ValidationError) as info:
        validate_fact(fact(**kwargs))
    return str(info.value)


@pytest.mark.parametrize("event_type", ["lab", "medication", "encounter", "symptom"])
def test_a_well_formed_fact_of_each_type_passes(event_type):
    validate_fact(fact(event_type))


def test_dates_must_be_plausible():
    assert "Implausible" in rejected(day=date(1899, 12, 31))
    assert "Implausible" in rejected(day=date.today() + timedelta(days=2))
    validate_fact(fact(day=date(1900, 1, 1)))
    validate_fact(fact(day=date.today() + timedelta(days=1)))  # a day of slack for time zones


@pytest.mark.parametrize("data,message", [
    ({"test_name": "HbA1c", "unit": "%"}, "numeric value"),
    ({"test_name": "HbA1c", "value": "7.2", "unit": "%"}, "numeric value"),
    ({"test_name": "HbA1c", "value": 7.2}, "unit"),
    ({"test_name": "HbA1c", "value": 7.2, "unit": ""}, "unit"),
    ({"value": 7.2, "unit": "%"}, "test name"),
])
def test_a_lab_result_needs_a_number_a_unit_and_a_name(data, message):
    assert message in rejected(event_type="lab", data=data)


def test_a_lab_value_of_zero_is_a_value():
    validate_fact(fact("lab", {"test_name": "Glucose", "value": 0, "unit": "mg/dL"}))


@pytest.mark.parametrize("event_type,data,message", [
    ("medication", {"dose": "500mg"}, "medication name"),
    ("encounter", {"provider_or_facility": "Clinic"}, "type"),
    ("symptom", {"detail": "mild"}, "symptom name"),
    ("imaging", {"x": 1}, "Unknown event_type"),
])
def test_the_other_types_need_their_defining_field(event_type, data, message):
    assert message in rejected(event_type=event_type, data=data)


def test_a_fact_with_no_quote_has_no_provenance_and_is_refused():
    assert "no source quote" in rejected(quote="   ")


def test_invalid_facts_are_dropped_and_valid_ones_kept_in_order():
    good_one, good_two = fact("lab"), fact("medication")
    bad = fact("lab", {"test_name": "HbA1c", "unit": "%"})
    assert validate_facts([good_one, bad, good_two]) == [good_one, good_two]
    assert validate_facts([]) == []


# --- the shared queries ----------------------------------------------------------------------
@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    for table in (User, Document, HealthEvent):
        table.__table__.create(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


def world(db):
    alice, bob = (User(id=uuid.uuid4(), email=f"{n}@example.com", hashed_password="x")
                  for n in ("alice", "bob"))
    db.add_all([alice, bob])
    docs = {}
    for owner in (alice, bob):
        doc = Document(id=uuid.uuid4(), user_id=owner.id, filename="f.pdf",
                       mime_type="application/pdf", storage_path="p")
        db.add(doc)
        docs[owner.email] = doc
    db.flush()
    for owner in (alice, bob):
        for status in ("confirmed", "edited", "pending", "rejected"):
            db.add(HealthEvent(user_id=owner.id, document_id=docs[owner.email].id,
                               event_type="lab", event_date=DAY, data={}, review_status=status))
    db.commit()
    return alice, bob, docs


def test_confirmed_events_are_this_users_and_only_reviewed_ones(db):
    alice, bob, _ = world(db)
    rows = db.scalars(confirmed_events_query(db, alice.id)).all()
    assert {r.review_status for r in rows} == set(CONFIRMED_STATUSES) and len(rows) == 2
    assert {r.user_id for r in rows} == {alice.id}
    assert db.scalars(confirmed_events_query(db, uuid.uuid4())).all() == []


def test_a_user_sees_only_their_own_documents(db):
    alice, bob, docs = world(db)
    ids = [d.id for d in db.scalars(user_documents_query(db, alice.id))]
    assert ids == [docs["alice@example.com"].id]


def test_ownership_is_checked_against_the_user_not_just_the_document(db):
    alice, bob, docs = world(db)
    mine, theirs = docs["alice@example.com"], docs["bob@example.com"]
    assert user_owns_document(db, alice.id, mine.id) is True
    assert user_owns_document(db, alice.id, theirs.id) is False
    assert user_owns_document(db, alice.id, uuid.uuid4()) is False
