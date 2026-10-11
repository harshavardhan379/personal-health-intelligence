"""Planner, chronic group (evaluation/planner.py, piece 2a).

Every planted property is recomputed here from the plan's events, independently of the
planner, so a planner bug cannot also hide in its own test. Trends are checked with
plain arithmetic, never with services.trends.trend_engine.
"""

import json
from collections import Counter
from datetime import date

import pytest

from evaluation.catalogs import (
    ARCHETYPE_BY_KEY,
    MEDS,
    N_PATIENTS,
    WINDOW_END,
    WINDOW_START,
    archetype_for,
)
from evaluation.planner import plan_patient

# The spec's margins (dataset spec, decision 13), written out here on purpose rather than
# imported from the planner: a test that borrows the planner's constants cannot catch a
# planner whose constants are wrong.
MIN_CHANGE = 0.15
MAX_STABLE = 0.02

CHRONIC = ("improving_chronic", "worsening_chronic", "stable")
CHRONIC_PATIENTS = [n for n in range(1, N_PATIENTS + 1) if archetype_for(n).key in CHRONIC]


def _series(plan, lab):
    rows = sorted(
        (e.event_date, e.data["value"]) for e in plan.events
        if e.event_type == "lab" and e.data["test_name"] == lab
    )
    return [v for _, v in rows]


def _docs(plan, doc_type):
    return [d for d in plan.docs if d.doc_type == doc_type]


def _events_of(plan, doc):
    by_id = {e.event_id: e for e in plan.events}
    return [by_id[i] for i in doc.event_ids]


# --- determinism ---------------------------------------------------------------------


def test_same_inputs_give_an_identical_plan():
    first = json.dumps(plan_patient(1).to_dict(), sort_keys=True)
    second = json.dumps(plan_patient(1).to_dict(), sort_keys=True)
    assert first == second


def test_a_different_seed_or_patient_gives_a_different_plan():
    base = json.dumps(plan_patient(1).to_dict(), sort_keys=True)
    assert base != json.dumps(plan_patient(1, seed=7).to_dict(), sort_keys=True)
    assert base != json.dumps(plan_patient(9).to_dict(), sort_keys=True)  # also archetype 1


# --- structure ------------------------------------------------------------------------


@pytest.mark.parametrize("n", CHRONIC_PATIENTS)
def test_documents_match_the_approved_mix(n):
    plan = plan_patient(n)
    assert Counter(d.doc_type for d in plan.docs) == Counter(ARCHETYPE_BY_KEY[plan.archetype].docs)


@pytest.mark.parametrize("n", CHRONIC_PATIENTS)
def test_every_event_is_in_exactly_one_document_and_ids_are_sequential(n):
    plan = plan_patient(n)
    in_docs = [i for d in plan.docs for i in d.event_ids]
    assert sorted(in_docs) == sorted(e.event_id for e in plan.events)
    assert len(in_docs) == len(set(in_docs))
    expected = [f"p{n:03d}-e{i:03d}" for i in range(1, len(plan.events) + 1)]
    assert [e.event_id for e in plan.events] == expected
    assert [d.index for d in plan.docs] == list(range(1, len(plan.docs) + 1))


@pytest.mark.parametrize("n", CHRONIC_PATIENTS)
def test_event_dates_match_their_document_except_inside_a_discharge_summary(n):
    plan = plan_patient(n)
    for doc in plan.docs:
        for event in _events_of(plan, doc):
            if doc.doc_type == "discharge_summary":
                assert event.event_date <= doc.doc_date
            else:
                assert event.event_date == doc.doc_date
            assert event.date_precision == "day"


@pytest.mark.parametrize("n", CHRONIC_PATIENTS)
def test_dates_are_inside_the_fixed_window_and_reports_fall_on_weekdays(n):
    plan = plan_patient(n)
    for doc in plan.docs:
        assert WINDOW_START <= doc.doc_date <= WINDOW_END
        if doc.doc_type != "discharge_summary":
            assert doc.doc_date.weekday() < 5, doc.doc_type


@pytest.mark.parametrize("n", CHRONIC_PATIENTS)
def test_lab_report_gaps_are_within_the_approved_range(n):
    plan = plan_patient(n)
    low, high = ARCHETYPE_BY_KEY[plan.archetype].gap_days
    dates = [d.doc_date for d in _docs(plan, "lab_report")]
    for earlier, later in zip(dates, dates[1:]):
        # the weekday shift can add up to 2 days to a drawn gap
        assert low <= (later - earlier).days <= high + 2


# --- planted trends, recomputed independently -------------------------------------------


@pytest.mark.parametrize("n", CHRONIC_PATIENTS)
def test_the_planted_primary_trend_holds_after_rounding(n):
    plan = plan_patient(n)
    lab = plan.tags["primary_lab"]
    values = _series(plan, lab)
    first, last = values[0], values[-1]
    change = (last - first) / first
    direction = plan.tags["trend"][lab]

    if plan.archetype == "improving_chronic":
        assert (lab, direction) == ("HbA1c", "decreasing")
        assert all(a > b for a, b in zip(values, values[1:]))
        assert change <= -MIN_CHANGE
    elif plan.archetype == "worsening_chronic":
        assert (lab, direction) == ("HbA1c", "increasing")
        assert all(a < b for a, b in zip(values, values[1:]))
        assert change >= MIN_CHANGE
    else:
        assert (lab, direction) == ("Systolic BP", "stable")
        assert all(abs(v - first) / first <= MAX_STABLE for v in values)


@pytest.mark.parametrize("n", CHRONIC_PATIENTS)
def test_every_other_lab_is_stable_so_only_the_primary_carries_a_trend(n):
    plan = plan_patient(n)
    primary = plan.tags["primary_lab"]
    others = [lab for lab in plan.tags["trend"] if lab != primary]
    assert 1 <= len(others) <= 3
    for lab in others:
        values = _series(plan, lab)
        assert len(values) == 3
        assert plan.tags["trend"][lab] == "stable"
        assert all(abs(v - values[0]) / values[0] <= MAX_STABLE for v in values), lab


@pytest.mark.parametrize("n", CHRONIC_PATIENTS)
def test_planted_trends_keep_clear_of_the_systems_five_percent_boundary(n):
    """Margins on either side of trend_engine's 5 percent line (dataset spec, decision 13)."""
    plan = plan_patient(n)
    for lab, direction in plan.tags["trend"].items():
        values = _series(plan, lab)
        change = abs((values[-1] - values[0]) / values[0])
        if direction == "stable":
            assert change <= MAX_STABLE
        else:
            assert change >= MIN_CHANGE


@pytest.mark.parametrize("seed", range(40))
def test_planting_never_fails_for_any_chronic_patient_under_many_seeds(seed):
    for n in CHRONIC_PATIENTS:
        plan_patient(n, seed=seed)  # raises RuntimeError if rounding defeats a planted trend


# --- medications -------------------------------------------------------------------------


@pytest.mark.parametrize("n", CHRONIC_PATIENTS)
def test_dose_changes_fall_on_a_report_date_and_move_one_ladder_step(n):
    plan = plan_patient(n)
    lab_dates = {d.doc_date for d in _docs(plan, "lab_report")}
    for change in plan.tags["dose_changes"]:
        when = date.fromisoformat(change["date"])
        assert when in lab_dates
        ladder = MEDS[change["medication"]].doses
        assert ladder.index(change["to"]) - ladder.index(change["from"]) == 1


@pytest.mark.parametrize("n", CHRONIC_PATIENTS)
def test_each_prescription_lists_every_medication_currently_taken(n):
    plan = plan_patient(n)
    rx = sorted(_docs(plan, "prescription"), key=lambda d: d.doc_date)
    names = [{e.data["medication"] for e in _events_of(plan, d)} for d in rx]
    for earlier, later in zip(names, names[1:]):
        assert earlier <= later  # nothing is silently dropped from one prescription to the next
    if plan.archetype == "worsening_chronic":
        assert len(names[-1]) == 2 and "Metformin" in names[-1]
        assert plan.tags["medications_added"][0]["medication"] in names[-1]


@pytest.mark.parametrize("n", CHRONIC_PATIENTS)
def test_a_prescription_is_never_dated_after_the_visit_it_belongs_to(n):
    plan = plan_patient(n)
    visits = _docs(plan, "visit_note")
    if visits:
        assert max(d.doc_date for d in _docs(plan, "prescription")) <= visits[0].doc_date


# --- hospital stay (worsening archetype) ---------------------------------------------------


def _worsening():
    return [n for n in CHRONIC_PATIENTS if archetype_for(n).key == "worsening_chronic"]


@pytest.mark.parametrize("n", _worsening())
def test_a_stay_is_two_linked_encounter_events_of_three_to_nine_days(n):
    plan = plan_patient(n)
    (stay,) = plan.tags["hospital_stays"]
    (summary,) = _docs(plan, "discharge_summary")
    encounters = [e for e in _events_of(plan, summary) if e.event_type == "encounter"]
    kinds = {e.data["encounter_type"]: e for e in encounters}
    assert set(kinds) == {"Hospital Admission", "Hospital Discharge"}
    assert {e.data["stay_id"] for e in encounters} == {stay["stay_id"]}
    days = (kinds["Hospital Discharge"].event_date - kinds["Hospital Admission"].event_date).days
    assert 3 <= days <= 9
    assert summary.doc_date == kinds["Hospital Discharge"].event_date
    assert kinds["Hospital Admission"].event_date.weekday() < 5


@pytest.mark.parametrize("n", _worsening())
def test_the_stay_falls_between_the_second_and_third_lab_report(n):
    plan = plan_patient(n)
    labs = sorted(d.doc_date for d in _docs(plan, "lab_report"))
    (summary,) = _docs(plan, "discharge_summary")
    admission = min(e.event_date for e in _events_of(plan, summary))
    assert labs[1] < admission and summary.doc_date < labs[2]


@pytest.mark.parametrize("n", _worsening())
def test_discharge_medications_are_the_prescription_in_force_at_discharge(n):
    plan = plan_patient(n)
    (summary,) = _docs(plan, "discharge_summary")
    in_force = [d for d in _docs(plan, "prescription") if d.doc_date <= summary.doc_date]
    latest = max(in_force, key=lambda d: d.doc_date)
    expected = {(e.data["medication"], e.data["dose"]) for e in _events_of(plan, latest)}
    got = {(e.data["medication"], e.data["dose"])
           for e in _events_of(plan, summary) if e.event_type == "medication"}
    assert got == expected


# --- scope ----------------------------------------------------------------------------------


def test_archetypes_outside_the_chronic_group_are_not_planned_yet():
    other = next(n for n in range(1, N_PATIENTS + 1) if archetype_for(n).key not in CHRONIC)
    with pytest.raises(NotImplementedError):
        plan_patient(other)
