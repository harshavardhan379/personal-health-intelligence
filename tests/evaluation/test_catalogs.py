"""The catalogs match the approved dataset spec, and fit the real extractors.

The extractor tests matter most: the spec claims the catalog "fits the current extractors",
and this is the check that it does, using the real extractor code rather than a copy.
"""

from collections import Counter
from datetime import date

import pytest

from evaluation.catalogs import (
    ARCHETYPES,
    ENCOUNTER_TYPES,
    FACILITIES,
    LABS,
    MEDS,
    N_DEV,
    N_PATIENTS,
    NOISE,
    SYMPTOMS,
    WINDOW_END,
    WINDOW_START,
    alias_table,
    archetype_for,
    canonical_name,
    document_id,
    event_id,
    patient_id,
    split_for,
)
from services.document_processing.normalize import NormalizedPage
from services.extraction import (
    encounter_extractor,
    lab_extractor,
    medication_extractor,
    symptom_extractor,
)


def _page(line: str):
    return [NormalizedPage(page=1, text=line, is_ocr=False)]


# --- spec conformance ------------------------------------------------------------------


def test_documents_per_archetype_match_the_approved_mix():
    totals = {a.key: a.total_docs for a in ARCHETYPES}
    assert totals == {
        "improving_chronic": 6, "worsening_chronic": 7, "stable": 4, "contradiction": 5,
        "missing_information": 3, "duplicate_upload": 4, "acute_episode": 3,
        "multi_document": 7,
    }


def test_dataset_size_and_discharge_share_match_the_spec():
    counts = Counter(archetype_for(n).key for n in range(1, N_PATIENTS + 1))
    total = sum(counts[a.key] * a.total_docs for a in ARCHETYPES)
    discharge = sum(counts[a.key] * a.docs.get("discharge_summary", 0) for a in ARCHETYPES)
    assert total == 247
    assert discharge == 25


def test_round_robin_gives_every_archetype_in_both_splits_and_2plus_in_test():
    dev = Counter(archetype_for(n).key for n in range(1, N_PATIENTS + 1) if split_for(n) == "dev")
    test = Counter(archetype_for(n).key for n in range(1, N_PATIENTS + 1) if split_for(n) == "test")
    assert set(dev) == {a.key for a in ARCHETYPES}
    assert set(test) == {a.key for a in ARCHETYPES}
    assert min(test.values()) >= 2


def test_split_is_thirty_dev_twenty_test_by_id():
    splits = [split_for(n) for n in range(1, N_PATIENTS + 1)]
    assert splits.count("dev") == N_DEV == 30
    assert splits.count("test") == 20
    assert split_for(30) == "dev" and split_for(31) == "test"


def test_ids_are_zero_padded_and_deterministic():
    assert patient_id(1) == "p001" and patient_id(50) == "p050"
    assert document_id(7, 3) == "p007-d03"
    assert event_id(12, 45) == "p012-e045"


def test_date_window_is_fixed_and_entirely_in_the_past():
    assert WINDOW_START == date(2023, 1, 1) and WINDOW_END == date(2026, 6, 30)
    assert WINDOW_END < date.today()  # the extraction validator rejects future dates


def test_approved_noise_rates():
    assert (NOISE.date_omitted_from_line, NOISE.month_only, NOISE.undated) == (0.10, 0.05, 0.03)
    assert (NOISE.unit_omitted, NOISE.dose_omitted, NOISE.name_variant) == (0.05, 0.05, 0.10)
    assert sum(NOISE.layout_shares.values()) == pytest.approx(1.0)
    assert sum(NOISE.date_style_shares.values()) == pytest.approx(1.0)
    assert set(NOISE.date_style_shares) == {"iso", "day_mon_year", "month_day_year"}
    assert NOISE.date_omission_rate == pytest.approx(0.13)


# --- aliases ---------------------------------------------------------------------------


def test_no_alias_maps_to_two_different_canonical_names():
    seen = {}
    for canonical, aliases in alias_table().items():
        for name in (canonical, *aliases):
            key = name.strip().lower()
            assert seen.setdefault(key, canonical) == canonical, name


def test_canonical_name_folds_case_whitespace_and_aliases():
    assert canonical_name("HBA1C") == canonical_name("Hb A1c") == canonical_name(" hba1c ")
    assert canonical_name("A1c") == canonical_name("HbA1c")
    assert canonical_name("Lipitor") == canonical_name("atorvastatin")
    assert canonical_name("HbA1c") != canonical_name("LDL")


def test_unknown_names_are_only_trimmed_and_casefolded():
    assert canonical_name("  Vitamin D ") == "vitamin d"


# --- the catalog fits the REAL extractors -----------------------------------------------


@pytest.mark.parametrize("lab", list(LABS.values()), ids=lambda lab: lab.name)
def test_every_lab_and_alias_is_parsed_by_the_real_lab_extractor(lab):
    for name in (lab.name, *lab.aliases):
        line = f"{name}: 7.2 {lab.unit} (ref {lab.ref_range}) 2026-01-10"
        facts = lab_extractor.extract(_page(line))
        assert len(facts) == 1, line
        data = facts[0].data
        assert (data["test_name"], data["unit"], data["value"]) == (name, lab.unit, 7.2)


@pytest.mark.parametrize("med", list(MEDS.values()), ids=lambda med: med.name)
def test_every_medication_dose_and_brand_is_parsed_by_the_real_extractor(med):
    for name in (med.name, *med.aliases):
        for dose in med.doses:
            line = f"Rx: {name} {dose} {med.frequency} 2026-01-10"
            facts = medication_extractor.extract(_page(line))
            assert len(facts) == 1, line
            data = facts[0].data
            assert (data["medication"], data["dose"], data["frequency"]) == (
                name, dose, med.frequency,
            ), line


@pytest.mark.parametrize("etype", ENCOUNTER_TYPES)
@pytest.mark.parametrize("facility", FACILITIES)
def test_every_encounter_type_and_facility_is_parsed(etype, facility):
    line = f"Encounter: {etype} at {facility} reason: routine checkup 2026-01-10"
    facts = encounter_extractor.extract(_page(line))
    assert len(facts) == 1, line
    assert facts[0].data["encounter_type"] == etype
    assert facts[0].data["provider_or_facility"] == facility


@pytest.mark.parametrize("symptom", SYMPTOMS)
def test_every_symptom_is_parsed(symptom):
    facts = symptom_extractor.extract(_page(f"Symptom: {symptom} detail: mild 2026-01-10"))
    assert len(facts) == 1 and facts[0].data["symptom"] == symptom
