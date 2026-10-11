"""Planner: decides WHAT happens to each patient and WHEN, before any document text exists.

Output is a PatientPlan: dated ground-truth events grouped into the documents they will be
written in. Rendering (layouts, wording) and degradation (omitted dates, units, ...) are
later steps and never change what the plan says is TRUE.

Determinism: one random.Random per patient, seeded from (seed, patient number); no set
iteration; every list is built in a fixed order. The same inputs give the same plan on any
machine.

Trends are planted BY CONSTRUCTION and checked here after rounding. They are never labelled
with services.trends.trend_engine: that would be the system grading itself (dataset spec,
decision 13). Margins sit well either side of the system's 5 percent line: a planted
decrease or increase is at least 15 percent, a planted "stable" at most 2 percent.

Piece 2a covers the chronic group (improving, worsening, stable). The other archetypes
raise NotImplementedError until piece 2b.
"""

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from evaluation.catalogs import (
    ARCHETYPE_BY_KEY,
    DEFAULT_SEED,
    ENCOUNTER_REASONS,
    HOSPITAL,
    LABS,
    MEDS,
    STAY_REASONS,
    SYMPTOM_DETAILS,
    WINDOW_END,
    WINDOW_START,
    archetype_for,
    document_id,
    event_id,
    patient_id,
    split_for,
)
from evaluation.model import Event

# Documents dated the same day are ordered by type so ids are stable.
TYPE_ORDER = {
    "lab_report": 0, "prescription": 1, "visit_note": 2, "discharge_summary": 3, "combined": 4,
}
OUTPATIENT_FACILITIES = ("Riverside Clinic", "Northgate Medical Center")
VISIT_SYMPTOMS = ("fatigue", "blurred vision", "dizziness", "headache")

# Planted-trend margins (decision 13).
MIN_CHANGE = 0.15  # a planted increase / decrease changes by at least 15 percent
MAX_STABLE = 0.02  # a planted stable series stays within 2 percent of its first value

# Baselines for labs that carry no planted trend. Glucose is left out of the diabetic
# archetypes because it would clinically track HbA1c.
SECONDARY_BASELINES = {
    "Glucose": (85, 130),
    "Total Cholesterol": (170, 240),
    "LDL": (90, 160),
    "Creatinine": (0.7, 1.2),
    "Hemoglobin": (12.5, 16.5),
}
DIABETIC_SECONDARY = ("Total Cholesterol", "LDL", "Creatinine", "Hemoglobin")


# --- plan data structures -----------------------------------------------------------


@dataclass
class PendingEvent:
    event_type: str
    event_date: date
    data: dict


@dataclass
class PlannedDoc:
    doc_type: str
    doc_date: date
    pending: list = field(default_factory=list)
    index: int = 0  # assigned at finalisation, 1-based, by (date, type)
    event_ids: list = field(default_factory=list)
    duplicate_of: Optional[str] = None


@dataclass
class PatientPlan:
    n: int
    archetype: str
    split: str
    events: list
    docs: list
    tags: dict

    @property
    def patient_id(self) -> str:
        return patient_id(self.n)

    def doc_id(self, doc: PlannedDoc) -> str:
        return document_id(self.n, doc.index)

    def to_dict(self) -> dict:
        return {
            "patient_id": self.patient_id,
            "archetype": self.archetype,
            "split": self.split,
            "tags": self.tags,
            "events": [e.to_dict() for e in self.events],
            "docs": [
                {
                    "document_id": self.doc_id(d),
                    "doc_type": d.doc_type,
                    "doc_date": d.doc_date.isoformat(),
                    "event_ids": d.event_ids,
                    "duplicate_of": d.duplicate_of,
                }
                for d in self.docs
            ],
        }


class _Builder:
    def __init__(self, n: int) -> None:
        self.n = n
        self.docs: list = []

    def doc(self, doc_type: str, doc_date: date) -> PlannedDoc:
        planned = PlannedDoc(doc_type=doc_type, doc_date=doc_date)
        self.docs.append(planned)
        return planned

    def finalise(self, archetype: str, tags: dict) -> PatientPlan:
        docs = sorted(self.docs, key=lambda d: (d.doc_date, TYPE_ORDER[d.doc_type]))
        events = []
        counter = 0
        for index, doc in enumerate(docs, start=1):
            doc.index = index
            for pending in doc.pending:
                counter += 1
                ev = Event(
                    event_id=event_id(self.n, counter),
                    event_type=pending.event_type,
                    event_date=pending.event_date,
                    date_precision="day",
                    data=pending.data,
                )
                doc.event_ids.append(ev.event_id)
                events.append(ev)
        return PatientPlan(
            n=self.n, archetype=archetype, split=split_for(self.n),
            events=events, docs=docs, tags=tags,
        )


# --- dates ----------------------------------------------------------------------------


def _rng(seed: int, n: int) -> random.Random:
    # A str seed is hashed with SHA-512 by random.Random, so it is stable across runs,
    # machines and Python 3 versions (unlike hash(), which is salted per process).
    return random.Random(f"{seed}:{n}")


def next_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def report_dates(rng: random.Random, gap_days: tuple, count: int) -> list:
    """`count` weekday dates, each a drawn gap after the previous, all inside the window."""
    low, high = gap_days
    worst_span = (count - 1) * (high + 2) + 2  # each weekday shift adds at most 2 days
    latest_start = WINDOW_END - timedelta(days=worst_span)
    start = next_weekday(WINDOW_START + timedelta(days=rng.randint(
        0, (latest_start - WINDOW_START).days)))
    dates = [start]
    for _ in range(count - 1):
        dates.append(next_weekday(dates[-1] + timedelta(days=rng.randint(low, high))))
    assert dates[-1] <= WINDOW_END, "report dates left the fixed window"
    return dates


# --- planted series ---------------------------------------------------------------------


def _series_ok(values: list, direction: str, low, high) -> bool:
    first, last = values[0], values[-1]
    if first == 0:
        return False
    if low is not None and min(values) < low:
        return False
    if high is not None and max(values) > high:
        return False
    if direction == "stable":
        return all(abs(v - first) / first <= MAX_STABLE for v in values)
    steps = list(zip(values, values[1:]))
    if direction == "decreasing":
        return all(a > b for a, b in steps) and (first - last) / first >= MIN_CHANGE
    return all(a < b for a, b in steps) and (last - first) / first >= MIN_CHANGE


def plant_series(rng, lab: str, direction: str, start: float, count: int,
                 low=None, high=None) -> list:
    """`count` values for `lab` with the planted `direction`, valid AFTER rounding to the
    lab's printed precision (redrawn until they are)."""
    decimals = LABS[lab].decimals
    start = round(start, decimals)
    for _ in range(500):
        if direction == "stable":
            raw = [start] + [start * (1 + rng.uniform(-0.012, 0.012)) for _ in range(count - 1)]
        else:
            sign = -1 if direction == "decreasing" else 1
            total = rng.uniform(0.18, 0.35)
            fractions = sorted(rng.uniform(0.2, 0.8) for _ in range(count - 2))
            raw = ([start] + [start * (1 + sign * total * f) for f in fractions]
                   + [start * (1 + sign * total)])
        values = [round(v, decimals) for v in raw]
        if _series_ok(values, direction, low, high):
            return [float(v) for v in values]
    raise RuntimeError(f"could not plant a {direction} series for {lab} from {start}")


# --- event helpers ------------------------------------------------------------------------


def _lab_event(lab: str, value: float, when: date) -> PendingEvent:
    spec = LABS[lab]
    return PendingEvent("lab", when, {
        "test_name": lab, "value": value, "unit": spec.unit, "reference_range": spec.ref_range,
    })


def _med_event(med: str, dose_index: int, when: date) -> PendingEvent:
    spec = MEDS[med]
    return PendingEvent("medication", when, {
        "medication": med, "dose": spec.doses[dose_index], "frequency": spec.frequency,
    })


def _symptom_event(rng, symptom: str, when: date) -> PendingEvent:
    return PendingEvent("symptom", when, {
        "symptom": symptom, "detail": rng.choice(SYMPTOM_DETAILS),
    })


def _write_prescription(b: _Builder, when: date, state: dict) -> PlannedDoc:
    """A prescription lists EVERY medication the patient is on, at its current dose."""
    doc = b.doc("prescription", when)
    for med in sorted(state):
        doc.pending.append(_med_event(med, state[med], when))
    return doc


def _meds_at(prescriptions: list, when: date) -> dict:
    """The medication state in force on `when`: the last prescription dated on or before it."""
    state: dict = {}
    for rx_date, rx_state in prescriptions:
        if rx_date <= when:
            state = rx_state
    return dict(state)


def plan_stay(b: _Builder, rng, stay_no: int, admission: date, prescriptions: list,
              symptom_pool: tuple) -> dict:
    """A hospital stay (decision 11): TWO encounter events, linked by a shared stay_id that
    is not part of matching, written in one discharge summary dated at discharge. The
    medication list is whatever prescription is in force at discharge."""
    stay_id = f"{patient_id(b.n)}-s{stay_no:02d}"
    discharge = admission + timedelta(days=rng.randint(3, 9))
    reason = rng.choice(STAY_REASONS)

    def encounter(kind: str, when: date) -> PendingEvent:
        return PendingEvent("encounter", when, {
            "encounter_type": kind, "provider_or_facility": HOSPITAL,
            "reason": reason, "stay_id": stay_id,
        })

    doc = b.doc("discharge_summary", discharge)
    doc.pending.append(encounter("Hospital Admission", admission))
    doc.pending.append(_symptom_event(rng, rng.choice(symptom_pool), admission))
    doc.pending.append(encounter("Hospital Discharge", discharge))
    state = _meds_at(prescriptions, discharge)
    for med in sorted(state):
        doc.pending.append(_med_event(med, state[med], discharge))
    return {"stay_id": stay_id, "admission": admission.isoformat(),
            "discharge": discharge.isoformat(), "reason": reason}


# --- the chronic group (archetypes 1, 2, 3) ----------------------------------------------


def _plan_chronic(b: _Builder, rng, key: str) -> dict:
    arch = ARCHETYPE_BY_KEY[key]
    dates = report_dates(rng, arch.gap_days, arch.docs["lab_report"])
    count = len(dates)

    if key == "improving_chronic":
        primary, direction = "HbA1c", "decreasing"
        series = {primary: plant_series(rng, primary, direction, rng.uniform(8.5, 10.5),
                                        count, low=5.8)}
        pool = DIABETIC_SECONDARY
    elif key == "worsening_chronic":
        primary, direction = "HbA1c", "increasing"
        series = {primary: plant_series(rng, primary, direction, rng.uniform(6.6, 8.0),
                                        count, high=13.0)}
        pool = DIABETIC_SECONDARY
    else:  # stable
        primary, direction = "Systolic BP", "stable"
        series = {primary: plant_series(rng, primary, direction, rng.randint(118, 135), count)}
        pool = tuple(SECONDARY_BASELINES)

    secondary = rng.sample(sorted(pool), rng.randint(1, 3))
    for lab in sorted(secondary):
        low, high = SECONDARY_BASELINES[lab]
        series[lab] = plant_series(rng, lab, "stable", rng.uniform(low, high), count)

    labs = [primary] + sorted(secondary)
    for i, when in enumerate(dates):
        report = b.doc("lab_report", when)
        for lab in labs:
            report.pending.append(_lab_event(lab, series[lab][i], when))

    # Medications. A dose change always falls on a report date (never between reports).
    tags = {"primary_lab": primary, "trend": {lab: ("stable" if lab != primary else direction)
                                              for lab in labs},
            "dose_changes": [], "medications_added": [], "hospital_stays": [],
            "has_contradiction": False, "missing_info": [], "duplicates_injected": 0}
    change_at = rng.choice([1, 2])
    prescriptions = []
    if key == "improving_chronic":
        start_state, change_state = {"Metformin": 0}, {"Metformin": 1}
    elif key == "worsening_chronic":
        second = rng.choice([("Glipizide", 1), ("Insulin glargine", 0)])
        start_state, change_state = {"Metformin": 1}, {"Metformin": 2, second[0]: second[1]}
        tags["medications_added"].append({"medication": second[0], "date": None})
    else:
        start_state, change_state = {"Lisinopril": rng.choice([0, 1])}, None

    prescriptions.append((dates[0], start_state))
    _write_prescription(b, dates[0], start_state)
    if change_state is not None:
        when = dates[change_at]
        prescriptions.append((when, change_state))
        _write_prescription(b, when, change_state)
        for med in sorted(change_state):
            if med in start_state and change_state[med] != start_state[med]:
                tags["dose_changes"].append({
                    "medication": med, "date": when.isoformat(),
                    "from": MEDS[med].doses[start_state[med]],
                    "to": MEDS[med].doses[change_state[med]]})
            elif med not in start_state:
                tags["medications_added"][-1]["date"] = when.isoformat()

    # A follow-up visit on the last report date (not for the stable archetype).
    if "visit_note" in arch.docs:
        visit_date = dates[-1]
        visit = b.doc("visit_note", visit_date)
        visit.pending.append(PendingEvent("encounter", visit_date, {
            "encounter_type": "Follow-up Visit",
            "provider_or_facility": rng.choice(OUTPATIENT_FACILITIES),
            "reason": rng.choice(ENCOUNTER_REASONS)}))
        weights = [5, 3, 1] if key == "improving_chronic" else [1, 3, 4]
        for symptom in sorted(rng.sample(VISIT_SYMPTOMS, rng.choices([0, 1, 2], weights)[0])):
            visit.pending.append(_symptom_event(rng, symptom, visit_date))

    # A worsening patient is also admitted once, between the 2nd and 3rd report.
    if "discharge_summary" in arch.docs:
        admission = next_weekday(dates[1] + timedelta(days=rng.randint(5, 12)))
        tags["hospital_stays"].append(
            plan_stay(b, rng, 1, admission, prescriptions, VISIT_SYMPTOMS))
    return tags


_PLANNERS = {
    "improving_chronic": _plan_chronic,
    "worsening_chronic": _plan_chronic,
    "stable": _plan_chronic,
}


def plan_patient(n: int, seed: int = DEFAULT_SEED) -> PatientPlan:
    arch = archetype_for(n)
    planner = _PLANNERS.get(arch.key)
    if planner is None:
        raise NotImplementedError(f"planner for {arch.key!r} arrives in piece 2b")
    rng = _rng(seed, n)
    builder = _Builder(n)
    tags = planner(builder, rng, arch.key)
    return builder.finalise(arch.key, tags)
