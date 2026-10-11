"""Renderer and degradation (evaluation/render.py).

The rate tests run over the whole 50-patient dataset with the fixed default seed, so they are
deterministic, not flaky; their tolerances are wide enough to allow sampling noise but tight
enough to catch a rule that is off by a factor of two.
"""

import json
import re
from collections import Counter
from datetime import date

import pytest

from evaluation.catalogs import LABS, N_PATIENTS, NOISE, archetype_for
from evaluation.planner import plan_patient
from evaluation.render import date_text, render_patient


@pytest.fixture(scope="module")
def everything():
    out = []
    for n in range(1, N_PATIENTS + 1):
        plan = plan_patient(n)
        out.append((plan, render_patient(plan)))
    return out


def _events(everything):
    return [e for _, rendered in everything for e in rendered.truth.events]


def _originals(everything):
    return [d for _, rendered in everything for d in rendered.documents
            if d.meta.duplicate_of is None]


# --- dates ---


@pytest.mark.parametrize("style,expected", [
    ("iso", "2026-04-09"), ("day_mon_year", "9 Apr 2026"), ("month_day_year", "April 9, 2026"),
])
def test_day_precision_dates_in_every_style(style, expected):
    assert date_text(date(2026, 4, 9), "day", style) == expected


@pytest.mark.parametrize("style,expected", [
    ("iso", "2026-04"), ("day_mon_year", "Apr 2026"), ("month_day_year", "April 2026"),
])
def test_month_precision_prints_the_month_only(style, expected):
    assert date_text(date(2026, 4, 1), "month", style) == expected


def test_undated_prints_nothing_and_no_style_is_ambiguous():
    assert date_text(None, "none", "iso") == ""
    assert not any("/" in date_text(date(2026, 1, 10), "day", s) for s in NOISE.date_style_shares)


# --- determinism ---


def test_rendering_is_deterministic_and_seed_sensitive():
    plan = plan_patient(2)
    a, b = render_patient(plan), render_patient(plan)
    assert [d.pages for d in a.documents] == [d.pages for d in b.documents]
    assert json.dumps(a.truth.to_dict(), sort_keys=True) == json.dumps(
        b.truth.to_dict(), sort_keys=True)
    other = render_patient(plan, seed=7)
    assert [d.pages for d in a.documents] != [d.pages for d in other.documents]


# --- text hygiene ---


def test_text_is_plain_ascii_with_single_spaces(everything):
    for _, rendered in everything:
        for doc in rendered.documents:
            for page in doc.pages:
                assert page.isascii() and "\t" not in page and "\f" not in page
                assert "  " not in page and not page.endswith("\n")
                assert all(line == line.strip() for line in page.split("\n"))


# --- sources ---


def test_every_truth_event_has_a_source_quote_found_verbatim_on_its_page(everything):
    for _, rendered in everything:
        docs = {d.meta.document_id: d for d in rendered.documents}
        for event in rendered.truth.events:
            source = event.source
            doc = docs[source.document_id]
            assert 1 <= source.page <= doc.meta.pages
            assert source.quote in doc.pages[source.page - 1].split("\n")


def test_a_two_page_document_continues_on_a_second_page_with_a_marker(everything):
    two = [d for d in _originals(everything) if d.meta.pages == 2]
    assert two, "the default dataset should contain two-page documents"
    for doc in two:
        assert doc.pages[1].startswith("(continued)")
        assert not doc.pages[0].startswith("(continued)")


def test_events_on_page_two_cite_page_two(everything):
    for _, rendered in everything:
        docs = {d.meta.document_id: d for d in rendered.documents}
        for event in rendered.truth.events:
            doc = docs[event.source.document_id]
            on_two = event.source.quote in doc.pages[-1].split("\n")[1:] and doc.meta.pages == 2
            assert (event.source.page == 2) == on_two


# --- truth stays honest ---


def test_degradation_never_changes_the_true_data(everything):
    for plan, rendered in everything:
        planned = {e.event_id: e for e in plan.events}
        assert [e.event_id for e in rendered.truth.events] == list(planned)
        for event in rendered.truth.events:
            assert event.data == planned[event.event_id].data
            assert event.event_type == planned[event.event_id].event_type
            assert event.conflicts_with == planned[event.event_id].conflicts_with


def test_date_degradations_are_recorded_consistently_in_the_truth(everything):
    for plan, rendered in everything:
        planned = {e.event_id: e for e in plan.events}
        header = {d.meta.document_id: d.meta.header_date_present for d in rendered.documents}
        doc_dates = {d.meta.document_id: d.meta.document_date for d in rendered.documents}
        for event in rendered.truth.events:
            true_date = planned[event.event_id].event_date
            kinds = set(event.degradations) & {
                "date_month_only", "date_omitted_from_line", "undated"}
            assert len(kinds) <= 1
            if "undated" in kinds:
                assert (event.date_precision, event.event_date) == ("none", None)
                source_doc = event.source.document_id
                # undated only when no header date could stand in for it
                assert (not header[source_doc]) or true_date != doc_dates[source_doc]
            elif "date_month_only" in kinds:
                assert event.date_precision == "month"
                assert event.event_date == true_date.replace(day=1)
            elif "date_omitted_from_line" in kinds:
                assert event.date_precision == "day" and event.event_date == true_date
                source_doc = event.source.document_id
                assert header[source_doc] and true_date == doc_dates[source_doc]
            else:
                assert event.date_precision == "day" and event.event_date == true_date


def test_only_labs_lose_units_only_medications_lose_doses_and_names_vary_where_aliased(everything):
    for event in _events(everything):
        if "unit_omitted" in event.degradations:
            assert event.event_type == "lab"
        if "dose_omitted" in event.degradations:
            assert event.event_type == "medication"
        if "name_variant" in event.degradations:
            assert event.event_type in ("lab", "medication")


# --- layouts: an undegraded fact reads exactly as specified ---------------------------------------


def _clean_lab_line(everything, layout):
    for _, rendered in everything:
        docs = {d.meta.document_id: d.meta for d in rendered.documents}
        for event in rendered.truth.events:
            meta = docs[event.source.document_id]
            if (event.event_type == "lab" and meta.layout == layout and not event.degradations
                    and meta.date_style == "iso"):
                return event
    raise AssertionError(f"no clean lab in layout {layout}")


def test_key_value_line_is_exactly_what_the_extractor_expects(everything):
    e = _clean_lab_line(everything, "key_value")
    x, spec = e.data, LABS[e.data["test_name"]]
    assert e.source.quote == (
        f"{x['test_name']}: {x['value']:.{spec.decimals}f} {x['unit']} "
        f"(ref {x['reference_range']}) {e.event_date.isoformat()}")


def test_table_row_uses_pipes_and_dashes_for_blanks(everything):
    e = _clean_lab_line(everything, "table")
    x, spec = e.data, LABS[e.data["test_name"]]
    assert e.source.quote == (
        f"{x['test_name']} | {x['value']:.{spec.decimals}f} | {x['unit']} | "
        f"{x['reference_range']} | {e.event_date.isoformat()}")


def test_prose_line_is_a_sentence(everything):
    e = _clean_lab_line(everything, "prose")
    x, spec = e.data, LABS[e.data["test_name"]]
    assert e.source.quote == (
        f"The {x['test_name']} result was {x['value']:.{spec.decimals}f} {x['unit']} "
        f"(reference range {x['reference_range']}) on {e.event_date.isoformat()}.")


def test_a_degraded_unit_and_date_are_really_missing_from_the_text(everything):
    seen = {"unit": False, "date": False}
    for _, rendered in everything:
        docs = {d.meta.document_id: d.meta for d in rendered.documents}
        for e in rendered.truth.events:
            if e.event_type != "lab" or docs[e.source.document_id].layout != "key_value":
                continue
            if "unit_omitted" in e.degradations:
                assert e.data["unit"] not in e.source.quote
                seen["unit"] = True
            if "date_omitted_from_line" in e.degradations or "undated" in e.degradations:
                assert not re.search(r"\d{4}|\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)",
                                     e.source.quote)
                seen["date"] = True
    assert all(seen.values())


# --- headers ---


def test_header_date_follows_the_patient_flag_and_the_documents_style(everything):
    for _, rendered in everything:
        flag = rendered.truth.tags["no_header_dates"]
        for doc in rendered.documents:
            first_page = doc.pages[0]
            assert doc.meta.header_date_present == (not flag)
            has_label = any(first_page.split("\n")[3].startswith(label) for label in (
                "Report date:", "Date prescribed:", "Visit date:", "Discharge date:")
            ) if len(first_page.split("\n")) > 3 else False
            assert has_label == (not flag)
            if not flag:
                expected = date_text(doc.meta.document_date, "day", doc.meta.date_style)
                assert first_page.split("\n")[3].endswith(expected)


def test_re_uploads_are_identical_text_and_point_at_their_original(everything):
    count = 0
    for _, rendered in everything:
        docs = {d.meta.document_id: d for d in rendered.documents}
        for doc in rendered.documents:
            if doc.meta.duplicate_of:
                count += 1
                original = docs[doc.meta.duplicate_of]
                assert doc.pages == original.pages
                assert (doc.meta.layout, doc.meta.date_style, doc.meta.pages) == (
                    original.meta.layout, original.meta.date_style, original.meta.pages)
                assert doc.meta.filename != original.meta.filename
    assert count == sum(1 for n in range(1, N_PATIENTS + 1)
                        if archetype_for(n).key == "duplicate_upload")


def test_a_carried_forward_line_keeps_the_original_facts_degradations(everything):
    checked = 0
    for plan, rendered in everything:
        if plan.archetype != "duplicate_upload":
            continue
        docs = {d.meta.document_id: d for d in rendered.documents}
        for doc in plan.docs:
            for p in doc.pending:
                if p.duplicate_of is None or doc.duplicate_of is not None:
                    continue
                truth = next(e for e in rendered.truth.events if e.event_id == p.event_id)
                line = next(line for line in docs[plan.doc_id(doc)].pages[0].split("\n")
                            + (docs[plan.doc_id(doc)].pages[1].split("\n")
                               if len(docs[plan.doc_id(doc)].pages) > 1 else [])
                            if f"{p.data['value']:.{LABS[p.data['test_name']].decimals}f}" in line
                            and ("Total Cholesterol" in line or "Cholesterol" in line))
                assert (p.data["unit"] in line) == ("unit_omitted" not in truth.degradations)
                checked += 1
    assert checked >= 5


# --- rates ---


def test_degradation_rates_are_near_the_approved_rates(everything):
    events = _events(everything)
    labs = [e for e in events if e.event_type == "lab"]
    named = [e for e in events if e.event_type in ("lab", "medication")]
    deg = Counter(d for e in events for d in e.degradations)
    n = len(events)
    assert abs(deg["date_month_only"] / n - NOISE.month_only) < 0.03
    omitted = (deg["date_omitted_from_line"] + deg["undated"]) / n
    assert abs(omitted - NOISE.date_omission_rate) < 0.04
    assert abs(sum(1 for e in labs if "unit_omitted" in e.degradations) / len(labs)
               - NOISE.unit_omitted) < 0.04
    assert abs(sum(1 for e in named if "name_variant" in e.degradations) / len(named)
               - NOISE.name_variant) < 0.04
    assert deg["undated"] > 0 and deg["date_omitted_from_line"] > 0


def test_layout_and_date_style_mix_is_near_the_profile(everything):
    docs = _originals(everything)
    layouts = Counter(d.meta.layout for d in docs)
    styles = Counter(d.meta.date_style for d in docs)
    for name, share in NOISE.layout_shares.items():
        assert abs(layouts[name] / len(docs) - share) < 0.12, name
    for name, share in NOISE.date_style_shares.items():
        assert abs(styles[name] / len(docs) - share) < 0.12, name


def test_some_patients_have_no_header_dates_but_most_do(everything):
    flags = [r.truth.tags["no_header_dates"] for _, r in everything]
    assert 0.04 < sum(flags) / len(flags) < 0.35
