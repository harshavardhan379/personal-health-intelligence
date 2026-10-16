"""Fact matching (evaluation/facts.py): spellings, boundaries, dates, values near names."""

from datetime import date, timedelta

import pytest

from evaluation.facts import (
    Fact,
    date_spellings,
    dose_spellings,
    duration_spellings,
    fact_matches,
    find_dates,
    month_spellings,
    number_spellings,
)


def token(*accepted):
    return Fact("t", "token", tuple(accepted))


# --- dates ---


@pytest.mark.parametrize("when", [date(2026, 4, 10), date(2023, 1, 1), date(2025, 9, 3),
                                  date(2024, 12, 22), date(2025, 5, 21), date(2026, 6, 30)])
def test_every_date_spelling_parses_back_to_the_same_date(when):
    for spelling in date_spellings(when):
        assert find_dates(f"It was on {spelling}.") == [when], spelling


def test_a_date_inside_a_sentence_is_found_and_slash_dates_are_never_produced():
    assert find_dates("Admitted 3rd of March 2025 and discharged on Mar 9, 2025.") == [
        date(2025, 3, 3), date(2025, 3, 9)]
    assert not any("/" in s for s in date_spellings(date(2026, 1, 10)))


def test_month_year_and_impossible_dates_are_not_full_dates():
    assert find_dates("In April 2026 and 2026-04 and 31 February 2025 and 2025-13-40.") == []


def test_any_date_and_day_in_month():
    any_date = Fact("d", "any_date")
    assert fact_matches(any_date, "on 2026-04-10") and not fact_matches(any_date, "in April 2026")
    in_month = Fact("m", "day_in_month", param="2026-04")
    assert fact_matches(in_month, "It was April 10, 2026.")
    assert not fact_matches(in_month, "It was in April 2026.")
    assert not fact_matches(in_month, "It was 2026-05-10.")


def test_month_spelling_does_not_match_inside_a_full_iso_date():
    month = token(*month_spellings(date(2026, 4, 1)))
    assert fact_matches(month, "Measured in April 2026.")
    assert fact_matches(month, "Measured in 2026-04.")
    assert not fact_matches(month, "Measured on 2026-04-10.")  # that is invented precision


# --- token boundaries ---


@pytest.mark.parametrize("text,expected", [
    ("Your HbA1c was 7.2%.", True), ("It is 7.2.", True), ("Values: 7.2, 6.5", True),
    ("It was 17.2", False), ("It was 7.25", False), ("It was 7.2.5", False),
    ("The code 27.2", False), ("on 2026-07-2", False),
])
def test_numbers_match_as_whole_tokens(text, expected):
    assert fact_matches(token("7.2"), text) is expected


def test_matching_ignores_case_and_extra_whitespace():
    assert fact_matches(token("500 mg"), "Take   500\nMG daily")


def test_stem_matches_anywhere():
    stem = Fact("s", "stem", ("decreas", "declin"))
    assert fact_matches(stem, "It has been Decreasing steadily")
    assert not fact_matches(stem, "It has been rising")


# --- values near names ---


def near(name, param="number"):
    return Fact("v", "value_near_name", (name, "LDL Cholesterol", "LDL-C") if name == "LDL"
                else (name,), param)


@pytest.mark.parametrize("text,expected", [
    ("Your LDL was 130 mg/dL.", True),
    ("LDL cholesterol: 98", True),
    ("I don't have an LDL result.", False),
    ("I don't have an LDL result on April 10, 2026.", False),  # date digits are not a value
    ("No LDL on file. Your HbA1c was 7.2%.", False),  # the number is another sentence's
    ("LDL and HbA1c were 130 and 7.2", False),  # another lab name sits in between
])
def test_a_value_near_a_lab_name(text, expected):
    assert fact_matches(near("LDL"), text) is expected


def test_a_dose_near_a_medication_name():
    metformin = Fact("v", "value_near_name", ("Metformin", "Glucophage"), "dose")
    assert fact_matches(metformin, "You take Metformin 850 mg twice daily.")
    assert fact_matches(metformin, "Glucophage: 1000mg")
    assert not fact_matches(metformin, "I have no record of Metformin.")
    assert not fact_matches(metformin, "Metformin was prescribed on 2026-04-10.")


# --- spellings of values ---


def test_number_dose_and_duration_spellings():
    assert number_spellings(7.2, 1) == ("7.2",)
    assert set(number_spellings(220.0, 0)) == {"220", "220.0"}
    assert set(number_spellings(7.0, 1)) == {"7.0", "7"}
    assert dose_spellings("500mg") == ("500mg", "500 mg")
    assert dose_spellings("10IU") == ("10IU", "10 IU")
    assert {"7 days", "seven days", "7-day", "a week", "one week"} <= set(duration_spellings(7))
    assert "2 weeks" in duration_spellings(14) and "a week" not in duration_spellings(14)


# --- construction and examples ---


def test_bad_facts_are_rejected():
    for bad in (lambda: Fact("x", "token"), lambda: Fact("x", "poem", ("a",)),
                lambda: Fact("x", "day_in_month", param="April"),
                lambda: Fact("x", "value_near_name", ("a",), "colour")):
        with pytest.raises(ValueError):
            bad()


def test_facts_round_trip_through_json_and_their_example_matches_themselves():
    facts = [token("7.2"), Fact("s", "stem", ("up",)), Fact("d", "any_date"),
             Fact("m", "day_in_month", param="2026-04"), near("LDL"),
             Fact("v", "value_near_name", ("Metformin",), "dose")]
    for fact in facts:
        assert Fact.from_dict(fact.to_dict()) == fact
        assert fact_matches(fact, fact.example()), fact


def test_date_range_sweep_never_breaks_the_round_trip():
    when = date(2023, 1, 1)
    for _ in range(0, 1300, 37):
        assert find_dates(date_spellings(when)[1]) == [when]
        when += timedelta(days=37)
