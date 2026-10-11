"""Ground-truth data structures reject malformed data at construction (evaluation/model.py)."""

import json
from datetime import date

import pytest

from evaluation.model import DocumentMeta, Event, EventSource, PatientTruth


def _lab(**overrides):
    base = dict(
        event_id="p001-e001", event_type="lab", event_date=date(2026, 1, 10),
        date_precision="day", data={"test_name": "HbA1c", "value": 7.2, "unit": "%"},
    )
    base.update(overrides)
    return Event(**base)


def test_valid_dated_and_undated_events_construct():
    assert _lab().key == "HbA1c"
    undated = _lab(event_date=None, date_precision="none")
    assert undated.event_date is None


def test_undated_event_must_not_carry_a_date_and_vice_versa():
    with pytest.raises(ValueError):
        _lab(event_date=date(2026, 1, 10), date_precision="none")
    with pytest.raises(ValueError):
        _lab(event_date=None, date_precision="day")


def test_month_and_year_precision_dates_are_the_first_of_the_period():
    _lab(event_date=date(2026, 4, 1), date_precision="month")
    _lab(event_date=date(2026, 1, 1), date_precision="year")
    with pytest.raises(ValueError):
        _lab(event_date=date(2026, 4, 10), date_precision="month")
    with pytest.raises(ValueError):
        _lab(event_date=date(2026, 4, 1), date_precision="year")


def test_unknown_type_precision_and_missing_key_field_are_rejected():
    with pytest.raises(ValueError):
        _lab(event_type="vaccine")
    with pytest.raises(ValueError):
        _lab(date_precision="week")
    with pytest.raises(ValueError):
        _lab(data={"value": 7.2})  # no test_name


def test_to_dict_is_json_serialisable_and_deterministic():
    event = _lab(
        source=EventSource("p001-d01", 1, "HbA1c: 7.2 %"),
        degradations=["unit_omitted", "name_variant"],
        conflicts_with=["p001-e009", "p001-e002"],
    )
    first = json.dumps(event.to_dict(), sort_keys=True)
    second = json.dumps(event.to_dict(), sort_keys=True)
    assert first == second
    d = event.to_dict()
    assert d["event_date"] == "2026-01-10"
    assert d["degradations"] == ["name_variant", "unit_omitted"]  # sorted
    assert d["conflicts_with"] == ["p001-e002", "p001-e009"]


def test_undated_event_serialises_with_null_date():
    assert _lab(event_date=None, date_precision="none").to_dict()["event_date"] is None


def test_patient_rejects_duplicate_event_ids_and_bad_split():
    with pytest.raises(ValueError):
        PatientTruth("p001", "dev", "stable", {}, [_lab(), _lab()], [])
    with pytest.raises(ValueError):
        PatientTruth("p001", "validation", "stable", {}, [_lab()], [])


def test_document_meta_validates_type_layout_and_pages():
    ok = dict(
        document_id="p001-d01", doc_type="lab_report", document_date=date(2026, 1, 10),
        layout="key_value", date_style="iso", header_date_present=True, pages=1,
        filename="d01.pdf",
    )
    assert DocumentMeta(**ok).to_dict()["document_date"] == "2026-01-10"
    for bad in ({"doc_type": "x-ray"}, {"layout": "poem"}, {"pages": 0}):
        with pytest.raises(ValueError):
            DocumentMeta(**{**ok, **bad})
