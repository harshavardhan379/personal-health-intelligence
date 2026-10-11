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

Duplicates (decision 17): a fact appears ONCE in the ground truth however many times the
documents repeat it, because the framework scores a second extraction of the same fact as a
false positive. Repeats are recorded as `duplicate_of` links on pending events and
documents, and counted in tags["duplicates_injected"].

Contradictions (decision 17): both conflicting facts stay in the ground truth, cross-linked
through `conflicts_with`. Neither is declared true: a correct system surfaces the
disagreement.
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
INFECTION_SYMPTOMS = ("fever", "cough", "sore throat")

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
ACUTE_LABS = ("Hemoglobin", "Creatinine", "Glucose")
COURSE_DAYS = 7  # the short antibiotic course in the acute archetype


# --- plan data structures -----------------------------------------------------------


@dataclass(eq=False)
class PendingEvent:
    event_type: str
    event_date: date
    data: dict
    # A repeat of a fact already in the truth. It is rendered into its document but is not
    # a separate ground-truth event; at finalisation event_id becomes the original's.
    duplicate_of: Optional["PendingEvent"] = None
    conflicts_with: list = field(default_factory=list)
    event_id: str = ""


@dataclass(eq=False)
class PlannedDoc:
    doc_type: str
    doc_date: date
    pending: list = field(default_factory=list)
    index: int = 0  # assigned at finalisation, 1-based, by (date, type)
    event_ids: list = field(default_factory=list)  # every fact the document contains
    duplicate_of: Optional["PlannedDoc"] = None  # a re-upload of another document


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
                    "duplicate_of": self.doc_id(d.duplicate_of) if d.duplicate_of else None,
                }
                for d in self.docs
            ],
        }


def _base_tags() -> dict:
    return {
        "primary_lab": None, "trend": {}, "dose_changes": [], "medications_added": [],
        "medication_courses": [], "hospital_stays": [], "has_contradiction": False,
        "contradictions": [], "missing_info": [], "duplicates_injected": 0,
        "duplicate_documents": [],
    }


class _Builder:
    def __init__(self, n: int) -> None:
        self.n = n
        self.docs: list = []
        self.conflicts: list = []  # (kind, [pending, pending])

    def doc(self, doc_type: str, doc_date: date) -> PlannedDoc:
        planned = PlannedDoc(doc_type=doc_type, doc_date=doc_date)
        self.docs.append(planned)
        return planned

    def link_conflict(self, kind: str, a: PendingEvent, b: PendingEvent) -> None:
        a.conflicts_with.append(b)
        b.conflicts_with.append(a)
        self.conflicts.append((kind, [a, b]))

    def reupload(self, original: PlannedDoc) -> PlannedDoc:
        """The same document uploaded again: identical content, so every line repeats a fact."""
        copy = self.doc(original.doc_type, original.doc_date)
        copy.duplicate_of = original
        for p in original.pending:
            root = p.duplicate_of or p
            copy.pending.append(PendingEvent(p.event_type, p.event_date, dict(p.data),
                                             duplicate_of=root))
        return copy

    def finalise(self, archetype: str, tags: dict) -> PatientPlan:
        docs = sorted(
            self.docs,
            key=lambda d: (d.doc_date, TYPE_ORDER[d.doc_type], d.duplicate_of is not None),
        )
        counter = 0
        for index, doc in enumerate(docs, start=1):
            doc.index = index
            for p in doc.pending:
                if p.duplicate_of is None:
                    counter += 1
                    p.event_id = event_id(self.n, counter)
        for doc in docs:
            for p in doc.pending:
                if p.duplicate_of is not None:
                    p.event_id = p.duplicate_of.event_id

        events = []
        for doc in docs:
            for p in doc.pending:
                doc.event_ids.append(p.event_id)
                if p.duplicate_of is None:
                    events.append(Event(
                        event_id=p.event_id, event_type=p.event_type, event_date=p.event_date,
                        date_precision="day", data=p.data,
                        conflicts_with=[c.event_id for c in p.conflicts_with],
                    ))

        tags["duplicates_injected"] = sum(
            1 for d in docs for p in d.pending if p.duplicate_of is not None)
        tags["duplicate_documents"] = [document_id(self.n, d.index)
                                       for d in docs if d.duplicate_of is not None]
        tags["has_contradiction"] = bool(self.conflicts)
        tags["contradictions"] = [
            {"kind": kind, "date": members[0].event_date.isoformat(),
             "event_ids": [m.event_id for m in members]}
            for kind, members in self.conflicts
        ]
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


def single_reading(rng, lab: str) -> float:
    low, high = SECONDARY_BASELINES[lab]
    return float(round(rng.uniform(low, high), LABS[lab].decimals))


# --- event helpers ------------------------------------------------------------------------


def _lab_event(lab: str, value: float, when: date) -> PendingEvent:
    spec = LABS[lab]
    return PendingEvent("lab", when, {
        "test_name": lab, "value": value, "unit": spec.unit, "reference_range": spec.ref_range,
    })


def _med_event(med: str, dose_index: int, when: date, **extra) -> PendingEvent:
    spec = MEDS[med]
    data = {"medication": med, "dose": spec.doses[dose_index], "frequency": spec.frequency}
    data.update(extra)
    return PendingEvent("medication", when, data)


def _symptom_event(rng, symptom: str, when: date) -> PendingEvent:
    return PendingEvent("symptom", when, {
        "symptom": symptom, "detail": rng.choice(SYMPTOM_DETAILS),
    })


def _visit_event(rng, when: date, kind: str = "Follow-up Visit") -> PendingEvent:
    return PendingEvent("encounter", when, {
        "encounter_type": kind, "provider_or_facility": rng.choice(OUTPATIENT_FACILITIES),
        "reason": rng.choice(ENCOUNTER_REASONS),
    })


def _write_prescription(b: _Builder, when: date, state: dict, **extra_by_med) -> PlannedDoc:
    """A prescription lists EVERY medication the patient is on, at its current dose."""
    doc = b.doc("prescription", when)
    for med in sorted(state):
        doc.pending.append(_med_event(med, state[med], when, **extra_by_med.get(med, {})))
    return doc


def _meds_at(prescriptions: list, when: date) -> dict:
    """The medication state in force on `when`: the last entry dated on or before it."""
    state: dict = {}
    for rx_date, rx_state in prescriptions:
        if rx_date <= when:
            state = rx_state
    return dict(state)


def _add_visit_symptoms(rng, doc: PlannedDoc, when: date, weights: list) -> None:
    count = rng.choices([0, 1, 2], weights)[0]
    for symptom in sorted(rng.sample(VISIT_SYMPTOMS, count)):
        doc.pending.append(_symptom_event(rng, symptom, when))


def _lab_report(b: _Builder, when: date, readings: dict) -> PlannedDoc:
    doc = b.doc("lab_report", when)
    for lab in sorted(readings, key=lambda name: (name != "HbA1c", name)):
        doc.pending.append(_lab_event(lab, readings[lab], when))
    return doc


def plan_stay(b: _Builder, rng, stay_no: int, admission: date, prescriptions: list,
              symptom_pool: tuple, symptoms: int = 1, weekday_discharge: bool = False,
              course_end: Optional[date] = None, reason: Optional[str] = None) -> tuple:
    """A hospital stay (decision 11): TWO encounter events, linked by a shared stay_id that
    is not part of matching, written in one discharge summary dated at discharge. The
    medication list is whatever prescription is in force at discharge, and is empty if a
    short course (course_end) has already finished. Returns (stay info, discharge doc)."""
    stay_id = f"{patient_id(b.n)}-s{stay_no:02d}"
    if weekday_discharge:
        lengths = [k for k in range(3, 10) if (admission + timedelta(days=k)).weekday() < 5]
        discharge = admission + timedelta(days=rng.choice(lengths))
    else:
        discharge = admission + timedelta(days=rng.randint(3, 9))
    reason = reason or rng.choice(STAY_REASONS)

    def encounter(kind: str, when: date) -> PendingEvent:
        return PendingEvent("encounter", when, {
            "encounter_type": kind, "provider_or_facility": HOSPITAL,
            "reason": reason, "stay_id": stay_id,
        })

    doc = b.doc("discharge_summary", discharge)
    doc.pending.append(encounter("Hospital Admission", admission))
    for symptom in sorted(rng.sample(symptom_pool, symptoms)):
        doc.pending.append(_symptom_event(rng, symptom, admission))
    doc.pending.append(encounter("Hospital Discharge", discharge))
    if course_end is None or discharge <= course_end:
        state = _meds_at(prescriptions, discharge)
        for med in sorted(state):
            doc.pending.append(_med_event(med, state[med], discharge))
    info = {"stay_id": stay_id, "admission": admission.isoformat(),
            "discharge": discharge.isoformat(), "reason": reason}
    return info, doc


# --- archetypes 1-3: the chronic group ----------------------------------------------------


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
        _lab_report(b, when, {lab: series[lab][i] for lab in labs})

    tags = _base_tags()
    tags["primary_lab"] = primary
    tags["trend"] = {lab: ("stable" if lab != primary else direction) for lab in labs}

    # Medications. A dose change always falls on a report date (never between reports).
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
        visit = b.doc("visit_note", dates[-1])
        visit.pending.append(_visit_event(rng, dates[-1]))
        weights = [5, 3, 1] if key == "improving_chronic" else [1, 3, 4]
        _add_visit_symptoms(rng, visit, dates[-1], weights)

    # A worsening patient is also admitted once, between the 2nd and 3rd report.
    if "discharge_summary" in arch.docs:
        admission = next_weekday(dates[1] + timedelta(days=rng.randint(5, 12)))
        info, _ = plan_stay(b, rng, 1, admission, prescriptions, VISIT_SYMPTOMS)
        tags["hospital_stays"].append(info)
    return tags


# --- archetype 4: contradiction ----------------------------------------------------------


def _plan_contradiction(b: _Builder, rng, key: str) -> dict:
    """The discharge summary and an outpatient prescription written the SAME DAY list
    different doses of the same drug. Both facts are kept, marked as conflicting."""
    arch = ARCHETYPE_BY_KEY[key]
    t0, t1 = report_dates(rng, arch.gap_days, 2)

    hba1c = plant_series(rng, "HbA1c", "stable", rng.uniform(6.8, 7.8), 2)
    other = rng.choice(["Total Cholesterol", "LDL", "Creatinine", "Hemoglobin"])
    other_series = plant_series(rng, other, "stable", rng.uniform(*SECONDARY_BASELINES[other]), 2)
    for i, when in enumerate((t0, t1)):
        _lab_report(b, when, {"HbA1c": hba1c[i], other: other_series[i]})

    tags = _base_tags()
    tags["primary_lab"] = "HbA1c"
    tags["trend"] = {"HbA1c": "stable", other: "stable"}

    dose_in_force = rng.choice([0, 1])
    _write_prescription(b, t0, {"Metformin": dose_in_force})
    prescriptions = [(t0, {"Metformin": dose_in_force})]

    # Admitted soon after t0 and discharged on a weekday, before the second lab report.
    admission = next_weekday(t0 + timedelta(days=rng.randint(5, 12)))
    info, summary = plan_stay(b, rng, 1, admission, prescriptions, VISIT_SYMPTOMS,
                              weekday_discharge=True)
    tags["hospital_stays"].append(info)

    # An outpatient prescription dated the same day as discharge, at a different dose.
    other_dose = 1 if dose_in_force == 0 else rng.choice([0, 2])  # always a different rung
    rx = _write_prescription(b, summary.doc_date, {"Metformin": other_dose})
    discharge_line = next(p for p in summary.pending if p.event_type == "medication")
    b.link_conflict("dose", discharge_line, rx.pending[0])
    return tags


# --- archetype 5: missing information ------------------------------------------------------


def _plan_missing(b: _Builder, rng, key: str) -> dict:
    """A sparse record. The tags list what was NEVER documented, so questions about it
    must be answered 'not in the records'."""
    arch = ARCHETYPE_BY_KEY[key]
    t0, t1 = report_dates(rng, arch.gap_days, 2)

    present = sorted(rng.sample(ACUTE_LABS, 2))
    _lab_report(b, t0, {lab: single_reading(rng, lab) for lab in present})

    med = rng.choice(["Atorvastatin", "Lisinopril"])
    _write_prescription(b, t0, {med: rng.choice([0, 1])})

    visit = b.doc("visit_note", t1)
    visit.pending.append(_visit_event(rng, t1, rng.choice(["Annual Physical", "Follow-up Visit"])))
    _add_visit_symptoms(rng, visit, t1, [1, 3, 1])

    tags = _base_tags()
    absent_labs = sorted(set(LABS) - set(present))
    absent_meds = sorted(set(MEDS) - {med})
    for lab in sorted(rng.sample(absent_labs, 3)):
        tags["missing_info"].append({"kind": "lab", "name": lab})
    for name in sorted(rng.sample(absent_meds, 2)):
        tags["missing_info"].append({"kind": "medication", "name": name})
    return tags


# --- archetype 6: duplicate upload ----------------------------------------------------------


def _plan_duplicate(b: _Builder, rng, key: str) -> dict:
    """Two kinds of repeat: a lab result carried forward onto the next report, and one whole
    document uploaded twice. Neither adds a ground-truth event; both are counted."""
    arch = ARCHETYPE_BY_KEY[key]
    t0, t1 = report_dates(rng, arch.gap_days, 2)

    primary = "Total Cholesterol"
    chol = plant_series(rng, primary, "decreasing", rng.uniform(215, 260), 2, low=140)
    other = rng.choice(["Creatinine", "Hemoglobin", "Glucose"])
    other_series = plant_series(rng, other, "stable", rng.uniform(*SECONDARY_BASELINES[other]), 2)
    report1 = _lab_report(b, t0, {primary: chol[0], other: other_series[0]})
    report2 = _lab_report(b, t1, {primary: chol[1], other: other_series[1]})

    # Carried forward: report 2 prints report 1's cholesterol again, with its original date.
    original = next(p for p in report1.pending if p.data["test_name"] == primary)
    report2.pending.append(PendingEvent("lab", original.event_date, dict(original.data),
                                        duplicate_of=original))

    rx = _write_prescription(b, t0, {"Atorvastatin": rng.choice([1, 2])})

    # One whole document uploaded a second time.
    b.reupload(rng.choice([report1, report2, rx]))

    tags = _base_tags()
    tags["primary_lab"] = primary
    tags["trend"] = {primary: "decreasing", other: "stable"}
    return tags


# --- archetype 7: acute episode -------------------------------------------------------------


def _plan_acute(b: _Builder, rng, key: str) -> dict:
    """Labs and a 7-day antibiotic course on day 0, an admission within 2-9 days, and a
    discharge summary. The discharge medication list is empty if the course had ended."""
    arch = ARCHETYPE_BY_KEY[key]
    (t0,) = report_dates(rng, arch.gap_days, 1)

    labs = sorted(rng.sample(ACUTE_LABS, rng.randint(2, 3)))
    _lab_report(b, t0, {lab: single_reading(rng, lab) for lab in labs})

    state = {"Amoxicillin": 0}
    _write_prescription(b, t0, state, Amoxicillin={"course_days": COURSE_DAYS})
    course_end = t0 + timedelta(days=COURSE_DAYS)

    admission = next_weekday(t0 + timedelta(days=rng.randint(*arch.gap_days)))
    info, _ = plan_stay(b, rng, 1, admission, [(t0, state)], INFECTION_SYMPTOMS,
                        symptoms=rng.randint(1, 2), course_end=course_end,
                        reason="acute infection")

    tags = _base_tags()
    tags["hospital_stays"].append(info)
    tags["medication_courses"].append({
        "medication": "Amoxicillin", "start": t0.isoformat(), "end": course_end.isoformat(),
        "course_days": COURSE_DAYS})
    return tags


# --- archetype 8: multi-document -------------------------------------------------------------


def _combined(b: _Builder, rng, when: date, readings: dict, state: dict,
              symptom_weights: list) -> PlannedDoc:
    """A combined clinic report: labs, medications, a visit and symptoms in one document
    (always at least one symptom, so it really holds all four kinds of fact)."""
    doc = b.doc("combined", when)
    for lab in sorted(readings, key=lambda name: (name != "HbA1c", name)):
        doc.pending.append(_lab_event(lab, readings[lab], when))
    for med in sorted(state):
        doc.pending.append(_med_event(med, state[med], when))
    doc.pending.append(_visit_event(rng, when))
    _add_visit_symptoms(rng, doc, when, symptom_weights)
    return doc


def _plan_multi(b: _Builder, rng, key: str) -> dict:
    """Seven documents over five dates, mixing diabetes and lipids. Facts about one patient
    are spread across lab reports, a prescription, combined reports, a visit and a stay."""
    arch = ARCHETYPE_BY_KEY[key]
    d0, d1, d2, d3, d4 = report_dates(rng, arch.gap_days, 5)

    hba1c = plant_series(rng, "HbA1c", "decreasing", rng.uniform(8.0, 9.8), 4, low=5.8)
    ldl = plant_series(rng, "LDL", "decreasing", rng.uniform(135, 165), 3, low=60)
    chol = plant_series(rng, "Total Cholesterol", "stable", rng.uniform(190, 240), 2)
    creat = plant_series(rng, "Creatinine", "stable", rng.uniform(0.7, 1.2), 2)

    state0 = {"Metformin": 1, "Atorvastatin": 1}
    state4 = {"Metformin": 1, "Atorvastatin": 2}
    prescriptions = [(d0, state0), (d4, state4)]

    _lab_report(b, d0, {"HbA1c": hba1c[0], "LDL": ldl[0], "Total Cholesterol": chol[0]})
    _write_prescription(b, d0, state0)
    _combined(b, rng, d1, {"HbA1c": hba1c[1], "Creatinine": creat[0]}, state0, [0, 3, 1])
    _lab_report(b, d2, {"HbA1c": hba1c[2], "LDL": ldl[1], "Total Cholesterol": chol[1]})

    admission = next_weekday(d2 + timedelta(days=rng.randint(5, 12)))
    info, _ = plan_stay(b, rng, 1, admission, prescriptions, VISIT_SYMPTOMS)

    visit = b.doc("visit_note", d3)
    visit.pending.append(_visit_event(rng, d3, "Specialist Consultation"))
    _add_visit_symptoms(rng, visit, d3, [0, 1, 0])
    _combined(b, rng, d4, {"HbA1c": hba1c[3], "LDL": ldl[2], "Creatinine": creat[1]},
              state4, [0, 3, 1])

    tags = _base_tags()
    tags["primary_lab"] = "HbA1c"
    tags["trend"] = {"HbA1c": "decreasing", "LDL": "decreasing",
                     "Total Cholesterol": "stable", "Creatinine": "stable"}
    tags["dose_changes"].append({"medication": "Atorvastatin", "date": d4.isoformat(),
                                 "from": MEDS["Atorvastatin"].doses[1],
                                 "to": MEDS["Atorvastatin"].doses[2]})
    tags["hospital_stays"].append(info)
    return tags


_PLANNERS = {
    "improving_chronic": _plan_chronic,
    "worsening_chronic": _plan_chronic,
    "stable": _plan_chronic,
    "contradiction": _plan_contradiction,
    "missing_information": _plan_missing,
    "duplicate_upload": _plan_duplicate,
    "acute_episode": _plan_acute,
    "multi_document": _plan_multi,
}


def plan_patient(n: int, seed: int = DEFAULT_SEED) -> PatientPlan:
    arch = archetype_for(n)
    rng = _rng(seed, n)
    builder = _Builder(n)
    tags = _PLANNERS[arch.key](builder, rng, arch.key)
    return builder.finalise(arch.key, tags)
