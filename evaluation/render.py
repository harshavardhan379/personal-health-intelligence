"""Renderer and degradation (generator pieces 3 and 4).

Turns a PatientPlan (what is TRUE) into document text (what the documents SAY), applying the
approved noise profile, and returns the ground truth as it now stands: the planned facts,
with each fact's source (document, page, exact line) and a record of what was degraded.

Rules that keep the truth honest:
  * Degradation changes only what is PRINTED. A fact whose unit, dose or name variant was
    degraded keeps its true value in the truth. A date is the one exception: if a line prints
    no date and the document gives no way to recover it, the truth records the event as
    undated (date_precision "none"), because the documents no longer say when it happened.
  * A re-uploaded document is a byte-for-byte repeat of its original's text.
  * A repeated line (a carried-forward result) is rendered with the SAME degradation
    decisions as the fact it repeats, and is not a separate ground-truth event.
  * Every random draw uses one Random seeded from (seed, patient number); draws are made in
    a fixed order, and every roll is drawn even when it does not apply, so changing one rule
    cannot reshuffle unrelated choices.
  * Text is ASCII with single spaces only, so a quote can always be found verbatim in the
    text a PDF parser extracts.
"""

import random
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from evaluation.catalogs import DEFAULT_SEED, HOSPITAL, LABS, MEDS, NOISE, document_id
from evaluation.model import KEY_FIELD, DocumentMeta, Event, EventSource, PatientTruth
from evaluation.planner import OUTPATIENT_FACILITIES, PatientPlan, PendingEvent, PlannedDoc

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")
MONTH_ABBR = tuple(m[:3] for m in MONTHS)

TITLES = {
    "lab_report": "Laboratory Report - Lab Result (Reference Range)",
    "prescription": "Prescription - Rx (Prescribed Medications)",
    "visit_note": "Visit Note - Progress Note",
    "discharge_summary": "Discharge Summary - Hospital Course",
    "combined": "Clinic Report - Lab Results, Prescriptions and Visit Note",
}
DATE_LABELS = {
    "lab_report": "Report date", "prescription": "Date prescribed", "visit_note": "Visit date",
    "discharge_summary": "Discharge date", "combined": "Report date",
}
# Table layout: section order and headers.
TABLE_SECTIONS = (
    ("lab", "LAB RESULTS", "Test | Result | Unit | Reference | Date"),
    ("medication", "MEDICATIONS", "Medication | Dose | Frequency | Duration | Date"),
    ("encounter", "ENCOUNTERS", "Type | Facility | Reason | Date"),
    ("symptom", "SYMPTOMS", "Symptom | Detail | Date"),
)


def date_text(when: Optional[date], precision: str, style: str) -> str:
    """The date as printed in `style`; month precision prints the month only; empty if undated."""
    if when is None or precision == "none":
        return ""
    if precision == "month":
        if style == "iso":
            return f"{when.year:04d}-{when.month:02d}"
        if style == "day_mon_year":
            return f"{MONTH_ABBR[when.month - 1]} {when.year}"
        return f"{MONTHS[when.month - 1]} {when.year}"
    if style == "iso":
        return when.isoformat()
    if style == "day_mon_year":
        return f"{when.day} {MONTH_ABBR[when.month - 1]} {when.year}"
    return f"{MONTHS[when.month - 1]} {when.day}, {when.year}"


# --- per-event degradation decisions ----------------------------------------------------


@dataclass
class _EventRender:
    printed_name: str
    show_unit: bool
    show_dose: bool
    date_mode: str  # "full" | "month" | "omitted"
    truth_date: Optional[date]
    truth_precision: str
    degradations: list = field(default_factory=list)


def _draw_event(rng: random.Random, p: PendingEvent, doc: PlannedDoc,
                header_present: bool) -> _EventRender:
    # All four rolls are always drawn, in this order, applicable or not.
    roll_date, roll_name, roll_unit, roll_dose = (rng.random() for _ in range(4))
    name = str(p.data[KEY_FIELD[p.event_type]])
    printed, degradations = name, []

    if p.event_type in ("lab", "medication") and roll_name < NOISE.name_variant:
        aliases = sorted((LABS if p.event_type == "lab" else MEDS)[name].aliases)
        printed = aliases[int(roll_name / NOISE.name_variant * len(aliases))]
        degradations.append("name_variant")
    show_unit = not (p.event_type == "lab" and roll_unit < NOISE.unit_omitted)
    if not show_unit:
        degradations.append("unit_omitted")
    show_dose = not (p.event_type == "medication" and roll_dose < NOISE.dose_omitted)
    if not show_dose:
        degradations.append("dose_omitted")

    truth_date, precision, mode = p.event_date, "day", "full"
    if roll_date < NOISE.date_omission_rate:
        mode = "omitted"
        # The header date can stand in only if the document has one AND it is this event's
        # own date (a hospital admission line is not dated by the discharge-date header).
        if header_present and p.event_date == doc.doc_date:
            degradations.append("date_omitted_from_line")
        else:
            truth_date, precision = None, "none"
            degradations.append("undated")
    elif roll_date < NOISE.date_omission_rate + NOISE.month_only:
        mode, precision = "month", "month"
        truth_date = p.event_date.replace(day=1)
        degradations.append("date_month_only")
    return _EventRender(printed, show_unit, show_dose, mode, truth_date, precision, degradations)


# --- line builders (one fact -> one line / row / sentence) ------------------------------------


def _printed_date(p: PendingEvent, er: _EventRender, style: str) -> str:
    if er.date_mode == "omitted":
        return ""
    return date_text(p.event_date, "month" if er.date_mode == "month" else "day", style)


def _value(x: dict) -> str:
    return f"{x['value']:.{LABS[x['test_name']].decimals}f}"


def _kv_line(p: PendingEvent, er: _EventRender, style: str) -> str:
    x, when = p.data, _printed_date(p, er, style)
    if p.event_type == "lab":
        parts = [f"{er.printed_name}:", _value(x)]
        parts += [x["unit"]] if er.show_unit else []
        parts += [f"(ref {x['reference_range']})"]
    elif p.event_type == "medication":
        parts = ["Rx:", er.printed_name]
        parts += [x["dose"]] if er.show_dose else []
        parts += [x["frequency"]]
    elif p.event_type == "encounter":
        parts = [f"Encounter: {x['encounter_type']} at {x['provider_or_facility']}",
                 f"reason: {x['reason']}"]
    else:
        parts = [f"Symptom: {x['symptom']}", f"detail: {x['detail']}"]
    parts += [when] if when else []
    if p.event_type == "medication" and "course_days" in x:
        parts += [f"for {x['course_days']} days"]
    return " ".join(parts)


def _table_row(p: PendingEvent, er: _EventRender, style: str) -> str:
    x, when = p.data, _printed_date(p, er, style) or "-"
    if p.event_type == "lab":
        cells = [er.printed_name, _value(x), x["unit"] if er.show_unit else "-",
                 x["reference_range"], when]
    elif p.event_type == "medication":
        duration = f"{x['course_days']} days" if "course_days" in x else "-"
        cells = [er.printed_name, x["dose"] if er.show_dose else "-", x["frequency"],
                 duration, when]
    elif p.event_type == "encounter":
        cells = [x["encounter_type"], x["provider_or_facility"], x["reason"], when]
    else:
        cells = [x["symptom"], x["detail"], when]
    return " | ".join(cells)


def _prose_line(p: PendingEvent, er: _EventRender, style: str) -> str:
    x, when = p.data, _printed_date(p, er, style)
    on = f" on {when}" if when else ""
    if p.event_type == "lab":
        unit = f" {x['unit']}" if er.show_unit else ""
        return (f"The {er.printed_name} result was {_value(x)}{unit} "
                f"(reference range {x['reference_range']}){on}.")
    if p.event_type == "medication":
        dose = f" {x['dose']}" if er.show_dose else ""
        course = f" for {x['course_days']} days" if "course_days" in x else ""
        return f"{er.printed_name}{dose} {x['frequency']} was prescribed{on}{course}."
    if p.event_type == "encounter":
        return (f"The patient had a {x['encounter_type']} at {x['provider_or_facility']} "
                f"for {x['reason']}{on}.")
    return f"The patient reported {x['symptom']} ({x['detail']}){on}."


def _build_body(layout: str, style: str, pendings: list, renders: dict) -> tuple:
    """Returns (body lines, {id(pending): index of its line})."""
    lines: list = []
    where: dict = {}

    def er_of(p: PendingEvent) -> _EventRender:
        return renders[id(p.duplicate_of or p)]

    if layout == "key_value" or layout == "prose":
        build = _kv_line if layout == "key_value" else _prose_line
        for p in pendings:
            where[id(p)] = len(lines)
            lines.append(build(p, er_of(p), style))
    else:  # table
        for event_type, heading, columns in TABLE_SECTIONS:
            group = [p for p in pendings if p.event_type == event_type]
            if not group:
                continue
            lines += [heading, columns]
            for p in group:
                where[id(p)] = len(lines)
                lines.append(_table_row(p, er_of(p), style))
    return lines, where


# --- documents ---------------------------------------------------------------------------------


@dataclass
class RenderedDocument:
    pages: list  # page texts
    meta: DocumentMeta


@dataclass
class RenderedPatient:
    truth: PatientTruth
    documents: list  # RenderedDocument, in upload (index) order


def _facility(doc: PlannedDoc, roll: float) -> str:
    for p in doc.pending:
        if p.event_type == "encounter":
            return str(p.data["provider_or_facility"])
    if doc.doc_type == "discharge_summary":
        return HOSPITAL
    return OUTPATIENT_FACILITIES[int(roll * len(OUTPATIENT_FACILITIES))]


def _weighted(rng: random.Random, shares: dict) -> str:
    keys = sorted(shares)
    return rng.choices(keys, [shares[k] for k in keys])[0]


def _render_original(plan: PatientPlan, doc: PlannedDoc, rng: random.Random, header_present: bool,
                     renders: dict, sources: dict) -> RenderedDocument:
    layout = _weighted(rng, NOISE.layout_shares)
    style = _weighted(rng, NOISE.date_style_shares)
    two_page_roll, cut_roll, facility_roll = rng.random(), rng.random(), rng.random()

    for p in doc.pending:
        if p.duplicate_of is None:
            renders[id(p)] = _draw_event(rng, p, doc, header_present)

    body, where = _build_body(layout, style, doc.pending, renders)
    header = [TITLES[doc.doc_type], _facility(doc, facility_roll), f"Patient ID: {plan.patient_id}"]
    if header_present:
        header.append(f"{DATE_LABELS[doc.doc_type]}: {date_text(doc.doc_date, 'day', style)}")

    cut = len(body)
    if two_page_roll < NOISE.two_page_share and len(body) >= 2:
        cut = 1 + int(cut_roll * (len(body) - 1))  # at least one body line on each page
    pages = [header + body[:cut]] + ([["(continued)"] + body[cut:]] if cut < len(body) else [])

    doc_id = plan.doc_id(doc)
    for p in doc.pending:
        if p.duplicate_of is None:
            index = where[id(p)]
            page = 1 if index < cut else 2
            sources[id(p)] = EventSource(doc_id, page, body[index])

    meta = DocumentMeta(
        document_id=doc_id, doc_type=doc.doc_type, document_date=doc.doc_date, layout=layout,
        date_style=style, header_date_present=header_present, pages=len(pages),
        filename=f"d{doc.index:02d}.pdf",
    )
    return RenderedDocument(["\n".join(lines) for lines in pages], meta)


def render_patient(plan: PatientPlan, seed: int = DEFAULT_SEED) -> RenderedPatient:
    rng = random.Random(f"{seed}:{plan.n}:render")
    header_present = not (rng.random() < NOISE.no_header_patient_share)

    renders: dict = {}  # id(root pending event) -> _EventRender
    sources: dict = {}  # id(root pending event) -> EventSource
    rendered: dict = {}  # id(PlannedDoc) -> RenderedDocument
    documents = []
    for doc in plan.docs:
        if doc.duplicate_of is None:
            result = _render_original(plan, doc, rng, header_present, renders, sources)
        else:
            original = rendered[id(doc.duplicate_of)]
            meta = DocumentMeta(
                document_id=plan.doc_id(doc), doc_type=original.meta.doc_type,
                document_date=original.meta.document_date, layout=original.meta.layout,
                date_style=original.meta.date_style,
                header_date_present=original.meta.header_date_present,
                pages=original.meta.pages, filename=f"d{doc.index:02d}.pdf",
                duplicate_of=original.meta.document_id,
            )
            result = RenderedDocument(list(original.pages), meta)  # identical text
        rendered[id(doc)] = result
        documents.append(result)

    roots = {p.event_id: p for d in plan.docs for p in d.pending if p.duplicate_of is None}
    events = []
    for planned in plan.events:
        p = roots[planned.event_id]
        er = renders[id(p)]
        events.append(Event(
            event_id=planned.event_id, event_type=planned.event_type,
            event_date=er.truth_date, date_precision=er.truth_precision, data=planned.data,
            source=sources[id(p)], degradations=er.degradations,
            conflicts_with=planned.conflicts_with,
        ))

    tags = dict(plan.tags)
    tags["no_header_dates"] = not header_present
    truth = PatientTruth(
        patient_id=plan.patient_id, split=plan.split, archetype=plan.archetype, tags=tags,
        events=events, documents=[d.meta for d in documents],
    )
    return RenderedPatient(truth=truth, documents=documents)


__all__ = ["RenderedDocument", "RenderedPatient", "date_text", "document_id", "render_patient"]
