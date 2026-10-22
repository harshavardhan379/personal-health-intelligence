"""Extraction scoring and event matching (evaluation/matching.py, score_extraction.py).

Hand-built scenarios, one per rule in framework section 3, then properties over the real
dataset: a perfect pipeline scores 1.0, and the score cannot depend on listing order.
"""

import random
from datetime import date

import pytest

from evaluation import loader
from evaluation.contracts import PredictedEvent
from evaluation.matching import dates_agree, match_events
from evaluation.model import Event
from evaluation.score_extraction import score_extraction, summarize_extraction
from evaluation.writer import write_dataset


def truth(event_id, event_type, when, precision="day", **data):
    return Event(event_id=event_id, event_type=event_type, event_date=when,
                 date_precision=precision, data=data)


def pred(event_type, when, precision="day", **data):
    return PredictedEvent(event_type, when, precision if when else "none", data)


def hba1c(value=7.2, unit="%"):
    return {"test_name": "HbA1c", "value": value, "unit": unit}


D = date(2026, 4, 10)


# --- the match rule ---


def test_an_exact_extraction_is_a_true_positive():
    c = score_extraction([pred("lab", D, **hba1c())], [truth("e1", "lab", D, **hba1c())])
    assert (c.tp, c.fp, c.fn) == (1, 0, 0)


def test_a_missing_event_is_a_false_negative_and_a_spurious_one_a_false_positive():
    t = [truth("e1", "lab", D, **hba1c())]
    c = score_extraction([pred("medication", D, medication="Metformin", dose="500mg")], t)
    assert (c.tp, c.fp, c.fn) == (0, 1, 1)
    assert c.by_type["lab"].fn == 1 and c.by_type["medication"].fp == 1


def test_the_same_fact_extracted_twice_is_one_match_and_one_false_positive():
    """One-to-one: duplicates are penalised (3.1)."""
    twice = [pred("lab", D, **hba1c()), pred("lab", D, **hba1c())]
    c = score_extraction(twice, [truth("e1", "lab", D, **hba1c())])
    assert (c.tp, c.fp, c.fn) == (1, 1, 0)


def test_a_wrong_value_is_still_a_match_but_counts_against_numeric_accuracy():
    c = score_extraction([pred("lab", D, **hba1c(value=9.9))],
                         [truth("e1", "lab", D, **hba1c())])
    assert c.tp == 1 and (c.numeric_total, c.numeric_correct) == (1, 0)


def test_a_wrong_date_is_not_a_match():
    c = score_extraction([pred("lab", date(2026, 4, 11), **hba1c())],
                         [truth("e1", "lab", D, **hba1c())])
    assert (c.tp, c.fp, c.fn) == (0, 1, 1)
    assert (c.date_recall_total, c.date_recall_found) == (1, 0)  # found the fact, wrong date


def test_names_are_matched_through_the_alias_table():
    for name in ("Hb A1c", "HBA1C", " hba1c ", "A1c"):
        c = score_extraction([pred("lab", D, test_name=name, value=7.2, unit="%")],
                             [truth("e1", "lab", D, **hba1c())])
        assert c.tp == 1, name
    c = score_extraction([pred("medication", D, medication="Glucophage", dose="500mg")],
                         [truth("e1", "medication", D, medication="Metformin", dose="500mg")])
    assert c.tp == 1


def test_the_event_type_must_be_equal_even_if_the_key_text_is_the_same():
    c = score_extraction([pred("symptom", D, symptom="HbA1c")],
                         [truth("e1", "lab", D, **hba1c())])
    assert (c.tp, c.fp, c.fn) == (0, 1, 1)


# --- date precision (decision 7) ---


def test_dates_agree_at_the_coarser_precision():
    month = truth("m", "lab", date(2026, 4, 1), "month", **hba1c())
    assert dates_agree(month, pred("lab", date(2026, 4, 28), **hba1c()))
    assert not dates_agree(month, pred("lab", date(2026, 5, 1), **hba1c()))
    day = truth("d", "lab", D, **hba1c())
    assert dates_agree(day, pred("lab", date(2026, 4, 1), "month", **hba1c()))  # coarser wins
    assert not dates_agree(day, pred("lab", date(2026, 5, 1), "month", **hba1c()))
    year = truth("y", "lab", date(2026, 1, 1), "year", **hba1c())
    assert dates_agree(year, pred("lab", date(2026, 12, 31), **hba1c()))


def test_an_undated_truth_matches_on_type_and_key_alone():
    undated = truth("u", "lab", None, "none", **hba1c())
    assert dates_agree(undated, pred("lab", None, **hba1c()))
    assert dates_agree(undated, pred("lab", D, **hba1c()))


def test_a_dated_truth_never_matches_an_extraction_without_a_date():
    c = score_extraction([pred("lab", None, **hba1c())], [truth("e1", "lab", D, **hba1c())])
    assert (c.tp, c.fp, c.fn) == (0, 1, 1)


def test_undated_events_are_their_own_group_and_an_invented_date_is_counted():
    undated = truth("u", "lab", None, "none", **hba1c())
    honest = score_extraction([pred("lab", None, **hba1c())], [undated])
    assert (honest.undated_total, honest.undated_matched, honest.undated_invented) == (1, 1, 0)
    invented = score_extraction([pred("lab", D, **hba1c())], [undated])
    assert (invented.undated_total, invented.undated_matched,
            invented.undated_invented) == (1, 1, 1)
    missed = score_extraction([], [undated])
    assert (missed.undated_total, missed.undated_matched) == (1, 0)
    assert missed.date_recall_total == 0  # an undated event is not part of date recall


# --- the assignment is maximum, not greedy ---


def test_the_assignment_is_maximum_so_listing_order_cannot_cost_a_match():
    """P1 fits both truths, P2 only the month-level one. Greedy (T1 takes P1) leaves T2
    unmatched; the maximum matching pairs T1-P2 and T2-P1."""
    t1 = truth("t1", "lab", date(2026, 4, 1), "month", **hba1c())
    t2 = truth("t2", "lab", D, **hba1c())
    p1 = pred("lab", D, **hba1c())
    p2 = pred("lab", date(2026, 4, 20), **hba1c())
    for truths in ([t1, t2], [t2, t1]):
        for preds in ([p1, p2], [p2, p1]):
            pairs, missed, spurious = match_events(truths, preds)
            assert len(pairs) == 2 and not missed and not spurious


def test_an_exact_date_is_preferred_when_either_pairing_is_possible():
    t = [truth("t", "lab", D, **hba1c())]
    exact, coarse = pred("lab", D, **hba1c()), pred("lab", date(2026, 4, 1), "month", **hba1c())
    pairs, _, spurious = match_events(t, [coarse, exact])
    assert pairs[0][1] is exact and spurious == [coarse]


def test_when_the_rule_cannot_tell_two_truths_apart_the_agreeing_values_are_paired():
    """A planted contradiction: the same drug, the same day, two doses. A perfect pipeline
    must not lose dose accuracy to an arbitrary swap."""
    a = {"medication": "Metformin", "dose": "500mg"}
    b = {"medication": "Metformin", "dose": "850mg"}
    truths = [truth("t1", "medication", D, **a), truth("t2", "medication", D, **b)]
    for preds in ([pred("medication", D, **a), pred("medication", D, **b)],
                  [pred("medication", D, **b), pred("medication", D, **a)]):
        for ordered in (truths, truths[::-1]):
            c = score_extraction(preds, ordered)
            assert (c.tp, c.dose_total, c.dose_correct) == (2, 2, 2)


def test_two_undated_readings_of_one_lab_are_paired_by_value_too():
    t = [truth("u1", "lab", None, "none", **hba1c(7.0)),
         truth("u2", "lab", None, "none", **hba1c(8.0))]
    p = [pred("lab", None, **hba1c(8.0)), pred("lab", None, **hba1c(7.0))]
    c = score_extraction(p, t)
    assert (c.tp, c.numeric_total, c.numeric_correct) == (2, 2, 2)


def test_value_agreement_never_adds_a_match_the_rule_would_not_allow():
    t = [truth("t1", "lab", D, **hba1c(7.0))]
    wrong_date = pred("lab", date(2026, 5, 1), **hba1c(7.0))  # right value, wrong date
    c = score_extraction([wrong_date], t)
    assert c.tp == 0


# --- field accuracy ---


def test_numeric_accuracy_compares_at_the_printed_precision():
    ok = score_extraction([pred("lab", D, **hba1c(7.2000001))], [truth("e", "lab", D, **hba1c())])
    assert ok.numeric_correct == 1
    cholesterol = {"test_name": "Total Cholesterol", "value": 220.0, "unit": "mg/dL"}
    c = score_extraction([pred("lab", D, **cholesterol)], [truth("e", "lab", D, **cholesterol)])
    assert c.numeric_correct == 1


def test_unit_and_dose_accuracy_ignore_case_and_spacing_only():
    def metformin(dose):
        return {"medication": "Metformin", "dose": dose}

    c = score_extraction(
        [pred("lab", D, **hba1c(unit="MG/DL")), pred("medication", D, **metformin("500 mg"))],
        [truth("a", "lab", D, **hba1c(unit="mg/dL")),
         truth("b", "medication", D, **metformin("500mg"))])
    assert (c.unit_correct, c.dose_correct) == (1, 1)
    c = score_extraction(
        [pred("lab", D, **hba1c(unit="%")), pred("medication", D, **metformin("850mg"))],
        [truth("a", "lab", D, **hba1c(unit="mg/dL")),
         truth("b", "medication", D, **metformin("500mg"))])
    assert (c.unit_correct, c.dose_correct) == (0, 0)


def test_date_recall_is_not_one_to_one():
    """Two truths on the same date, one extraction: only one match, but the single
    extraction does carry an agreeing date for both facts of that type and key."""
    t = [truth("a", "lab", D, **hba1c()), truth("b", "lab", D, **hba1c(6.0))]
    c = score_extraction([pred("lab", D, **hba1c())], t)
    assert c.tp == 1 and c.fn == 1 and (c.date_recall_total, c.date_recall_found) == (2, 2)


# --- summaries ---


def test_counts_add_and_the_summary_pools_before_dividing():
    a = score_extraction([pred("lab", D, **hba1c())], [truth("e", "lab", D, **hba1c())])
    b = score_extraction([], [truth("f", "lab", D, **hba1c())] * 3)
    summary = summarize_extraction(a + b)
    recall = summary["overall"]["recall"]
    assert (recall.successes, recall.n) == (1, 4)  # pooled, not the mean of 1.0 and 0.0
    assert summary["by_type"]["lab"]["recall"].n == 4
    assert summary["by_type"]["symptom"]["recall"].n == 0  # nothing to find: undefined


def test_nothing_extracted_gives_undefined_precision_and_zero_f1():
    summary = summarize_extraction(score_extraction([], [truth("e", "lab", D, **hba1c())]))
    assert summary["overall"]["precision"].value is None
    assert summary["overall"]["f1"] == 0.0
    assert summary["overall"]["recall"].value == 0.0


def test_recall_dated_only_excludes_the_undated_group():
    t = [truth("a", "lab", D, **hba1c()), truth("b", "lab", None, "none", **hba1c(6.0))]
    summary = summarize_extraction(score_extraction([pred("lab", D, **hba1c())], t))
    overall, dated = summary["overall"]["recall"], summary["recall_dated_only"]
    undated = summary["undated_recall"]
    assert (overall.successes, overall.n) == (1, 2)
    assert (dated.successes, dated.n) == (1, 1)
    assert (undated.successes, undated.n) == (0, 1)


# --- over the real dataset ---


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("sx") / "v1"
    write_dataset(out)
    return out


def _perfect(events):
    return [PredictedEvent(e.event_type, e.event_date, e.date_precision, dict(e.data))
            for e in events]


def test_a_perfect_pipeline_scores_one_on_every_metric(dataset):
    total = None
    for pid in loader.patient_ids(dataset):
        events = loader.events_from_truth(loader.load_truth(dataset, pid))
        c = score_extraction(_perfect(events), events)
        assert (c.fp, c.fn) == (0, 0), pid
        total = c if total is None else total + c
    summary = summarize_extraction(total)
    for rate in (summary["overall"]["precision"], summary["overall"]["recall"],
                 summary["numeric_accuracy"], summary["unit_accuracy"],
                 summary["dose_accuracy"], summary["date_recall"], summary["undated_recall"]):
        assert rate.value == 1.0 and rate.n > 0
    assert summary["invented_date_rate"].value == 0.0 and summary["overall"]["f1"] == 1.0
    assert total.undated_total > 0  # the dataset really contains undated events


def test_the_score_does_not_depend_on_the_order_events_are_listed(dataset):
    rng = random.Random(1)
    for pid in loader.patient_ids(dataset)[:20]:
        events = loader.events_from_truth(loader.load_truth(dataset, pid))
        # an imperfect pipeline: drop a few, duplicate a few
        noisy = _perfect(events)
        noisy = [e for i, e in enumerate(noisy) if i % 5 != 0] + noisy[:2]
        reference = score_extraction(noisy, events)
        for _ in range(3):
            a, b = list(noisy), list(events)
            rng.shuffle(a)
            rng.shuffle(b)
            shuffled = score_extraction(a, b)
            assert (shuffled.tp, shuffled.fp, shuffled.fn) == (
                reference.tp, reference.fp, reference.fn), pid
            assert shuffled.numeric_correct == reference.numeric_correct
            assert shuffled.date_recall_found == reference.date_recall_found


def test_a_pipeline_that_extracts_nothing_scores_zero_recall(dataset):
    total = None
    for pid in loader.patient_ids(dataset)[:10]:
        events = loader.events_from_truth(loader.load_truth(dataset, pid))
        c = score_extraction([], events)
        total = c if total is None else total + c
    summary = summarize_extraction(total)
    assert summary["overall"]["recall"].value == 0.0
    assert summary["overall"]["precision"].value is None
