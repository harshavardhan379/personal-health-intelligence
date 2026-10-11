"""Planner, all eight archetypes (evaluation/planner.py).

Every planted property is recomputed here from the plan's events, independently of the
planner, so a planner bug cannot also hide in its own test. Trends are checked with plain
arithmetic, never with services.trends.trend_engine, and the spec's margins are written out
below instead of imported from the planner: a test that borrows the planner's constants
cannot catch a planner whose constants are wrong.
"""

import json
from collections import Counter
from datetime import date, timedelta

import pytest

from evaluation.catalogs import (
    ARCHETYPE_BY_KEY,
    LABS,
    MEDS,
    N_PATIENTS,
    WINDOW_END,
    WINDOW_START,
    archetype_for,
)
from evaluation.planner import plan_patient

MIN_CHANGE = 0.15  # dataset spec, decision 13
MAX_STABLE = 0.02

ALL = list(range(1, N_PATIENTS + 1))


def _of(*keys):
    return [n for n in ALL if archetype_for(n).key in keys]


CHRONIC = _of("improving_chronic", "worsening_chronic", "stable")
WORSENING = _of("worsening_chronic")
CONTRADICTION = _of("contradiction")
MISSING = _of("missing_information")
DUPLICATE = _of("duplicate_upload")
ACUTE = _of("acute_episode")
MULTI = _of("multi_document")


def _series(plan, lab):
    rows = sorted(
        (e.event_date, e.data["value"]) for e in plan.events
        if e.event_type == "lab" and e.data["test_name"] == lab
    )
    return [v for _, v in rows]


def _docs(plan, doc_type, include_reuploads=False):
    return [d for d in plan.docs
            if d.doc_type == doc_type and (include_reuploads or d.duplicate_of is None)]


def _events_of(plan, doc):
    by_id = {e.event_id: e for e in plan.events}
    return [by_id[i] for i in doc.event_ids]


def _by_id(plan):
    return {e.event_id: e for e in plan.events}


# =====================================================================================
# Every patient, every archetype
# =====================================================================================


def test_same_inputs_give_an_identical_plan():
    first = json.dumps(plan_patient(1).to_dict(), sort_keys=True)
    assert first == json.dumps(plan_patient(1).to_dict(), sort_keys=True)


def test_a_different_seed_or_patient_gives_a_different_plan():
    base = json.dumps(plan_patient(1).to_dict(), sort_keys=True)
    assert base != json.dumps(plan_patient(1, seed=7).to_dict(), sort_keys=True)
    assert base != json.dumps(plan_patient(9).to_dict(), sort_keys=True)  # also archetype 1


@pytest.mark.parametrize("n", ALL)
def test_documents_match_the_approved_mix_plus_the_reuploads(n):
    plan = plan_patient(n)
    arch = ARCHETYPE_BY_KEY[plan.archetype]
    originals = Counter(d.doc_type for d in plan.docs if d.duplicate_of is None)
    assert originals == Counter(arch.docs)
    assert sum(1 for d in plan.docs if d.duplicate_of is not None) == arch.reuploads
    assert len(plan.docs) == arch.total_docs


@pytest.mark.parametrize("n", ALL)
def test_ids_are_sequential_and_documents_reference_real_events(n):
    plan = plan_patient(n)
    assert [e.event_id for e in plan.events] == [
        f"p{n:03d}-e{i:03d}" for i in range(1, len(plan.events) + 1)]
    assert [d.index for d in plan.docs] == list(range(1, len(plan.docs) + 1))
    known = {e.event_id for e in plan.events}
    for doc in plan.docs:
        assert doc.event_ids and set(doc.event_ids) <= known
    # every fact appears in at least one document
    assert {i for d in plan.docs for i in d.event_ids} == known


@pytest.mark.parametrize("n", ALL)
def test_dates_are_inside_the_window_and_non_discharge_documents_fall_on_weekdays(n):
    plan = plan_patient(n)
    for doc in plan.docs:
        assert WINDOW_START <= doc.doc_date <= WINDOW_END
        if doc.doc_type != "discharge_summary":
            assert doc.doc_date.weekday() < 5, doc.doc_type
    for event in plan.events:
        assert WINDOW_START <= event.event_date <= WINDOW_END
        assert event.date_precision == "day"


@pytest.mark.parametrize("n", ALL)
def test_a_fact_appears_once_in_the_ground_truth(n):
    """Duplicates are repeats of a fact, never extra truth events (decision 17), or an
    extractor emitting a fact twice would be scored as matching twice."""
    plan = plan_patient(n)
    keys = Counter((e.event_type, e.event_date, e.key, e.data.get("dose")) for e in plan.events)
    assert max(keys.values()) == 1


@pytest.mark.parametrize("n", ALL)
def test_every_planted_trend_holds_after_rounding(n):
    plan = plan_patient(n)
    assert plan.tags["trend"].keys() <= set(LABS)
    for lab, direction in plan.tags["trend"].items():
        values = _series(plan, lab)
        first, last = values[0], values[-1]
        change = (last - first) / first
        if direction == "stable":
            assert all(abs(v - first) / first <= MAX_STABLE for v in values), lab
        elif direction == "decreasing":
            assert all(a > b for a, b in zip(values, values[1:])), lab
            assert change <= -MIN_CHANGE, lab
        else:
            assert direction == "increasing"
            assert all(a < b for a, b in zip(values, values[1:])), lab
            assert change >= MIN_CHANGE, lab


@pytest.mark.parametrize("seed", range(40))
def test_planting_never_fails_for_any_patient_under_many_seeds(seed):
    for n in ALL:
        plan_patient(n, seed=seed)  # raises RuntimeError if rounding defeats a planted trend


@pytest.mark.parametrize("n", ALL)
def test_plans_serialise_to_json(n):
    json.dumps(plan_patient(n).to_dict(), sort_keys=True)


# =====================================================================================
# Chronic group
# =====================================================================================


@pytest.mark.parametrize("n", CHRONIC)
def test_chronic_event_dates_match_their_document_except_in_a_discharge_summary(n):
    plan = plan_patient(n)
    for doc in plan.docs:
        for event in _events_of(plan, doc):
            if doc.doc_type == "discharge_summary":
                assert event.event_date <= doc.doc_date
            else:
                assert event.event_date == doc.doc_date


@pytest.mark.parametrize("n", CHRONIC)
def test_chronic_lab_report_gaps_are_within_the_approved_range(n):
    plan = plan_patient(n)
    low, high = ARCHETYPE_BY_KEY[plan.archetype].gap_days
    dates = [d.doc_date for d in _docs(plan, "lab_report")]
    for earlier, later in zip(dates, dates[1:]):
        assert low <= (later - earlier).days <= high + 2  # a weekday shift adds up to 2 days


@pytest.mark.parametrize("n", CHRONIC)
def test_the_primary_lab_has_the_archetypes_planted_direction(n):
    plan = plan_patient(n)
    expected = {"improving_chronic": ("HbA1c", "decreasing"),
                "worsening_chronic": ("HbA1c", "increasing"),
                "stable": ("Systolic BP", "stable")}[plan.archetype]
    assert (plan.tags["primary_lab"], plan.tags["trend"][plan.tags["primary_lab"]]) == expected


@pytest.mark.parametrize("n", CHRONIC)
def test_only_the_primary_lab_carries_a_trend(n):
    plan = plan_patient(n)
    others = [lab for lab in plan.tags["trend"] if lab != plan.tags["primary_lab"]]
    assert 1 <= len(others) <= 3
    assert all(plan.tags["trend"][lab] == "stable" and len(_series(plan, lab)) == 3
               for lab in others)


@pytest.mark.parametrize("n", CHRONIC)
def test_dose_changes_fall_on_a_report_date_and_move_one_ladder_step(n):
    plan = plan_patient(n)
    lab_dates = {d.doc_date for d in _docs(plan, "lab_report")}
    for change in plan.tags["dose_changes"]:
        assert date.fromisoformat(change["date"]) in lab_dates
        ladder = MEDS[change["medication"]].doses
        assert ladder.index(change["to"]) - ladder.index(change["from"]) == 1


@pytest.mark.parametrize("n", CHRONIC)
def test_each_prescription_lists_every_medication_currently_taken(n):
    plan = plan_patient(n)
    rx = sorted(_docs(plan, "prescription"), key=lambda d: d.doc_date)
    names = [{e.data["medication"] for e in _events_of(plan, d)} for d in rx]
    for earlier, later in zip(names, names[1:]):
        assert earlier <= later  # nothing is silently dropped from one prescription to the next
    if plan.archetype == "worsening_chronic":
        assert len(names[-1]) == 2 and "Metformin" in names[-1]
        assert plan.tags["medications_added"][0]["medication"] in names[-1]


@pytest.mark.parametrize("n", CHRONIC)
def test_a_prescription_is_never_dated_after_the_visit_it_belongs_to(n):
    plan = plan_patient(n)
    visits = _docs(plan, "visit_note")
    if visits:
        assert max(d.doc_date for d in _docs(plan, "prescription")) <= visits[0].doc_date


@pytest.mark.parametrize("n", WORSENING)
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


@pytest.mark.parametrize("n", WORSENING)
def test_the_worsening_stay_falls_between_the_second_and_third_lab_report(n):
    plan = plan_patient(n)
    labs = sorted(d.doc_date for d in _docs(plan, "lab_report"))
    (summary,) = _docs(plan, "discharge_summary")
    admission = min(e.event_date for e in _events_of(plan, summary))
    assert labs[1] < admission and summary.doc_date < labs[2]


@pytest.mark.parametrize("n", WORSENING)
def test_discharge_medications_are_the_prescription_in_force_at_discharge(n):
    plan = plan_patient(n)
    (summary,) = _docs(plan, "discharge_summary")
    in_force = [d for d in _docs(plan, "prescription") if d.doc_date <= summary.doc_date]
    latest = max(in_force, key=lambda d: d.doc_date)
    expected = {(e.data["medication"], e.data["dose"]) for e in _events_of(plan, latest)}
    got = {(e.data["medication"], e.data["dose"])
           for e in _events_of(plan, summary) if e.event_type == "medication"}
    assert got == expected


# =====================================================================================
# Archetype 4: contradiction
# =====================================================================================


@pytest.mark.parametrize("n", CONTRADICTION)
def test_a_contradiction_is_two_same_day_doses_of_one_drug_that_disagree(n):
    plan = plan_patient(n)
    assert plan.tags["has_contradiction"] is True
    (contradiction,) = plan.tags["contradictions"]
    first, second = (_by_id(plan)[i] for i in contradiction["event_ids"])
    assert first.event_type == second.event_type == "medication"
    assert first.data["medication"] == second.data["medication"]
    assert first.event_date == second.event_date == date.fromisoformat(contradiction["date"])
    assert first.data["dose"] != second.data["dose"]
    assert second.event_id in first.conflicts_with and first.event_id in second.conflicts_with


@pytest.mark.parametrize("n", CONTRADICTION)
def test_the_conflict_is_between_the_discharge_summary_and_an_outpatient_prescription(n):
    plan = plan_patient(n)
    (contradiction,) = plan.tags["contradictions"]
    holders = {next(d.doc_type for d in plan.docs if i in d.event_ids)
               for i in contradiction["event_ids"]}
    assert holders == {"discharge_summary", "prescription"}


@pytest.mark.parametrize("n", CONTRADICTION)
def test_the_contradicting_discharge_is_a_weekday_and_precedes_the_second_lab_report(n):
    plan = plan_patient(n)
    (summary,) = _docs(plan, "discharge_summary")
    labs = sorted(d.doc_date for d in _docs(plan, "lab_report"))
    assert summary.doc_date.weekday() < 5 and summary.doc_date < labs[1]
    days = [e for e in _events_of(plan, summary) if e.event_type == "encounter"]
    spans = sorted(e.event_date for e in days)
    assert 3 <= (spans[1] - spans[0]).days <= 9


@pytest.mark.parametrize("n", sorted(set(ALL) - set(CONTRADICTION)))
def test_no_other_archetype_carries_a_contradiction(n):
    plan = plan_patient(n)
    assert plan.tags["has_contradiction"] is False
    assert all(not e.conflicts_with for e in plan.events)


# =====================================================================================
# Archetype 5: missing information
# =====================================================================================


@pytest.mark.parametrize("n", MISSING)
def test_everything_listed_as_missing_is_really_absent_from_the_record(n):
    plan = plan_patient(n)
    labs = {e.data["test_name"] for e in plan.events if e.event_type == "lab"}
    meds = {e.data["medication"] for e in plan.events if e.event_type == "medication"}
    kinds = Counter(m["kind"] for m in plan.tags["missing_info"])
    assert kinds == {"lab": 3, "medication": 2}
    for item in plan.tags["missing_info"]:
        pool = labs if item["kind"] == "lab" else meds
        catalog = LABS if item["kind"] == "lab" else MEDS
        assert item["name"] not in pool and item["name"] in catalog


@pytest.mark.parametrize("n", MISSING)
def test_a_missing_information_record_is_sparse(n):
    plan = plan_patient(n)
    assert len(plan.docs) == 3 and len(plan.events) <= 8
    assert plan.tags["trend"] == {}


# =====================================================================================
# Archetype 6: duplicate upload
# =====================================================================================


@pytest.mark.parametrize("n", DUPLICATE)
def test_a_reupload_repeats_an_existing_document_exactly(n):
    plan = plan_patient(n)
    (copy,) = [d for d in plan.docs if d.duplicate_of is not None]
    original = copy.duplicate_of
    assert copy.doc_type == original.doc_type and copy.doc_date == original.doc_date
    assert copy.event_ids == original.event_ids
    assert copy.index > original.index  # uploaded after the original
    assert plan.tags["duplicate_documents"] == [plan.doc_id(copy)]


@pytest.mark.parametrize("n", DUPLICATE)
def test_a_lab_result_is_also_carried_forward_onto_the_next_report(n):
    plan = plan_patient(n)
    first, second = sorted(_docs(plan, "lab_report"), key=lambda d: d.doc_date)
    carried = set(first.event_ids) & set(second.event_ids)
    assert len(carried) == 1
    event = _by_id(plan)[carried.pop()]
    assert event.data["test_name"] == plan.tags["primary_lab"]
    assert event.event_date == first.doc_date < second.doc_date  # keeps its original date


@pytest.mark.parametrize("n", DUPLICATE)
def test_the_injected_duplicate_count_matches_the_repeated_lines(n):
    plan = plan_patient(n)
    (copy,) = [d for d in plan.docs if d.duplicate_of is not None]
    carried = 1  # one result is carried forward
    repeated_by_copy = len(copy.event_ids)
    # If the re-uploaded document is the 2nd lab report, its carried-forward line repeats too.
    assert plan.tags["duplicates_injected"] == carried + repeated_by_copy


# =====================================================================================
# Archetype 7: acute episode
# =====================================================================================


@pytest.mark.parametrize("n", ACUTE)
def test_an_acute_episode_is_short_and_has_a_seven_day_course(n):
    plan = plan_patient(n)
    first, last = plan.docs[0].doc_date, plan.docs[-1].doc_date
    assert (last - first).days <= 9 + 2 + 9 + 2  # onset gap + weekday shifts + longest stay
    (course,) = plan.tags["medication_courses"]
    start, end = date.fromisoformat(course["start"]), date.fromisoformat(course["end"])
    assert (course["medication"], (end - start).days, course["course_days"]) == (
        "Amoxicillin", 7, 7)
    (rx,) = _docs(plan, "prescription")
    assert rx.doc_date == start
    assert _events_of(plan, rx)[0].data["course_days"] == 7


@pytest.mark.parametrize("n", ACUTE)
def test_the_acute_admission_follows_onset_and_discharge_meds_only_while_the_course_runs(n):
    plan = plan_patient(n)
    (rx,) = _docs(plan, "prescription")
    (summary,) = _docs(plan, "discharge_summary")
    (course,) = plan.tags["medication_courses"]
    admission = min(e.event_date for e in _events_of(plan, summary))
    assert 2 <= (admission - rx.doc_date).days <= 9 + 2
    meds = [e for e in _events_of(plan, summary) if e.event_type == "medication"]
    assert bool(meds) == (summary.doc_date <= date.fromisoformat(course["end"]))
    assert any(e.event_type == "symptom" for e in _events_of(plan, summary))


@pytest.mark.parametrize("n", ACUTE)
def test_acute_labs_are_single_readings_with_no_trend(n):
    plan = plan_patient(n)
    assert plan.tags["trend"] == {} and plan.tags["primary_lab"] is None
    assert 2 <= len([e for e in plan.events if e.event_type == "lab"]) <= 3


# =====================================================================================
# Archetype 8: multi-document
# =====================================================================================


@pytest.mark.parametrize("n", MULTI)
def test_combined_reports_contain_all_four_kinds_of_fact(n):
    plan = plan_patient(n)
    combined = _docs(plan, "combined")
    assert len(combined) == 2
    for doc in combined:
        kinds = {e.event_type for e in _events_of(plan, doc)}
        assert kinds == {"lab", "medication", "encounter", "symptom"}


@pytest.mark.parametrize("n", MULTI)
def test_multi_document_trends_span_several_documents(n):
    plan = plan_patient(n)
    assert plan.tags["trend"] == {"HbA1c": "decreasing", "LDL": "decreasing",
                                  "Total Cholesterol": "stable", "Creatinine": "stable"}
    assert len(_series(plan, "HbA1c")) == 4 and len(_series(plan, "LDL")) == 3
    doc_types_with_hba1c = {
        d.doc_type for d in plan.docs
        if any(_by_id(plan)[i].data.get("test_name") == "HbA1c" for i in d.event_ids)}
    assert doc_types_with_hba1c == {"lab_report", "combined"}


@pytest.mark.parametrize("n", MULTI)
def test_the_statin_dose_change_is_recorded_on_the_second_combined_report(n):
    plan = plan_patient(n)
    (change,) = plan.tags["dose_changes"]
    last_combined = max(_docs(plan, "combined"), key=lambda d: d.doc_date)
    assert change["date"] == last_combined.doc_date.isoformat()
    assert (change["medication"], change["from"], change["to"]) == (
        "Atorvastatin", "20mg", "40mg")
    meds = {(e.data["medication"], e.data["dose"])
            for e in _events_of(plan, last_combined) if e.event_type == "medication"}
    assert ("Atorvastatin", "40mg") in meds


@pytest.mark.parametrize("n", MULTI)
def test_the_multi_document_stay_falls_between_the_second_lab_report_and_the_visit(n):
    plan = plan_patient(n)
    (lab2,) = [d for d in sorted(_docs(plan, "lab_report"), key=lambda d: d.doc_date)][1:]
    (visit,) = _docs(plan, "visit_note")
    (summary,) = _docs(plan, "discharge_summary")
    admission = min(e.event_date for e in _events_of(plan, summary))
    assert lab2.doc_date < admission and summary.doc_date < visit.doc_date
    assert (summary.doc_date - admission) <= timedelta(days=9)
