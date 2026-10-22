"""Retrieval and timeline scoring (evaluation/score_retrieval.py, score_timeline.py)."""

import random
from datetime import date

import pytest

from evaluation import loader
from evaluation.contracts import EvidenceItem, PredictedEvent
from evaluation.score_retrieval import score_retrieval, summarize_retrieval
from evaluation.score_timeline import (
    TimelineCounts,
    residual_duplicates,
    score_timeline,
    summarize_timeline,
)
from evaluation.writer import write_dataset
from tests.evaluation.test_score_extraction import hba1c, pred, truth

QUOTES = {"g1": "HbA1c: 7.2 % (ref 4.0-5.6) 2026-01-10", "g2": "Rx: Metformin 500mg twice daily"}


def item(*event_ids, text=""):
    return EvidenceItem(list(event_ids), text)


# --- retrieval ---


def test_a_perfect_ranking_scores_one_everywhere():
    c = score_retrieval([item("g1"), item("g2"), item("x")], {"g1", "g2"}, QUOTES)
    s = summarize_retrieval(c)
    assert s["recall_at_k"][3].value == 1.0 and s["recall_at_k"][5].value == 1.0
    assert s["mrr"].value == 1.0
    assert s["precision_at_k"][3].value == pytest.approx(2 / 3)


def test_recall_at_k_depends_on_where_the_gold_sits():
    ranking = [item("a"), item("b"), item("c"), item("g1")]  # gold at rank 4
    s = summarize_retrieval(score_retrieval(ranking, {"g1"}, QUOTES))
    assert s["recall_at_k"][3].value == 0.0 and s["recall_at_k"][5].value == 1.0
    assert s["mrr"].value == pytest.approx(0.25)


def test_no_relevant_item_gives_reciprocal_rank_zero():
    s = summarize_retrieval(score_retrieval([item("a"), item("b")], {"g1"}, QUOTES))
    assert s["mrr"].value == 0.0 and s["recall_at_k"][5].value == 0.0


def test_precision_divides_by_k_even_when_fewer_items_came_back():
    """Returning too little must not be rewarded (framework 4)."""
    s = summarize_retrieval(score_retrieval([item("g1")], {"g1"}, QUOTES))
    precision = s["precision_at_k"][5]
    assert (precision.successes, precision.n) == (1, 5)


def test_a_question_with_no_gold_evidence_is_left_out():
    assert score_retrieval([item("a")], set(), QUOTES) is None


def test_a_raw_chunk_is_relevant_when_it_contains_a_gold_source_quote():
    chunk = EvidenceItem([], "Report\nHbA1c:   7.2 %  (ref 4.0-5.6) 2026-01-10\nmore text")
    s = summarize_retrieval(score_retrieval([chunk], {"g1"}, QUOTES))
    assert s["recall_at_k"][3].value == 1.0 and s["mrr"].value == 1.0
    unrelated = EvidenceItem([], "Completely different page")
    assert summarize_retrieval(score_retrieval([unrelated], {"g1"}, QUOTES))["mrr"].value == 0.0


def test_an_item_with_event_ids_is_judged_by_ids_not_by_its_text():
    lying_text = EvidenceItem(["other"], QUOTES["g1"])
    assert summarize_retrieval(score_retrieval([lying_text], {"g1"}, QUOTES))["mrr"].value == 0.0


def test_recall_counts_each_gold_event_once_however_many_items_hit_it():
    ranking = [item("g1"), item("g1"), item("g1")]
    s = summarize_retrieval(score_retrieval(ranking, {"g1", "g2"}, QUOTES))
    assert (s["recall_at_k"][3].successes, s["recall_at_k"][3].n) == (1, 2)
    assert s["precision_at_k"][3].value == 1.0  # three relevant items of three


def test_retrieval_counts_pool_across_questions_before_dividing():
    a = score_retrieval([item("g1")], {"g1"}, QUOTES)
    b = score_retrieval([item("x")], {"g1", "g2", "g3"}, QUOTES)
    s = summarize_retrieval(a + b)
    assert (s["recall_at_k"][5].successes, s["recall_at_k"][5].n) == (1, 4)
    assert s["mrr"].value == pytest.approx(0.5) and s["questions"] == 2


# --- timeline: event accuracy and ordering ---


def lab(day, value=7.2):
    return pred("lab", date(2026, 1, day), **hba1c(value))


def lab_truth(event_id, day, value=7.2):
    return truth(event_id, "lab", date(2026, 1, day), **hba1c(value))


def test_a_correctly_ordered_timeline_scores_full_ordering_accuracy():
    t = [lab_truth("a", 1, 7.0), lab_truth("b", 5, 7.1), lab_truth("c", 9, 7.2)]
    c = score_timeline([lab(1, 7.0), lab(5, 7.1), lab(9, 7.2)], t, 0)
    assert (c.ordering_pairs, c.ordering_correct) == (3, 3)
    assert c.events.tp == 3 and c.events.fp == 0


def test_a_reversed_timeline_scores_zero_ordering_accuracy():
    t = [lab_truth("a", 1, 7.0), lab_truth("b", 5, 7.1), lab_truth("c", 9, 7.2)]
    c = score_timeline([lab(9, 7.2), lab(5, 7.1), lab(1, 7.0)], t, 0)
    assert (c.ordering_pairs, c.ordering_correct) == (3, 0)


def test_one_swapped_pair_costs_exactly_that_pair():
    t = [lab_truth("a", 1, 7.0), lab_truth("b", 5, 7.1), lab_truth("c", 9, 7.2)]
    c = score_timeline([lab(5, 7.1), lab(1, 7.0), lab(9, 7.2)], t, 0)
    assert (c.ordering_pairs, c.ordering_correct) == (3, 2)


def test_overlapping_and_undated_events_are_not_ordered():
    month = truth("m", "lab", date(2026, 1, 1), "month", **hba1c(7.0))
    day_in_month = truth("d", "lab", date(2026, 1, 20), **hba1c(7.1))
    undated = truth("u", "lab", None, "none", **hba1c(7.2))
    later = truth("l", "lab", date(2026, 3, 1), **hba1c(7.3))
    timeline = [pred("lab", date(2026, 1, 1), "month", **hba1c(7.0)), lab(20, 7.1),
                pred("lab", None, **hba1c(7.2)), pred("lab", date(2026, 3, 1), **hba1c(7.3))]
    c = score_timeline(timeline, [month, day_in_month, undated, later], 0)
    # month~day overlap and the undated one is excluded; month<later and day<later remain
    assert c.ordering_pairs == 2 and c.ordering_correct == 2


def test_ordering_follows_the_listed_position_not_the_dates_the_system_printed():
    t = [lab_truth("a", 1, 7.0), lab_truth("b", 5, 7.1)]
    c = score_timeline([lab(5, 7.1), lab(1, 7.0)], t, 0)
    assert (c.ordering_pairs, c.ordering_correct) == (1, 0)


# --- timeline: duplicates ---


def test_duplicates_in_the_timeline_are_counted():
    clean = [lab(1), lab(5, 7.1)]
    assert residual_duplicates(clean) == 0
    assert residual_duplicates(clean + [lab(1)]) == 1
    assert residual_duplicates([lab(1), lab(1), lab(1)]) == 2


def test_removal_rate_uses_injected_and_residual_duplicates_per_patient():
    t = [lab_truth("a", 1)]
    removed_all = score_timeline([lab(1)], t, injected_duplicates=2)
    assert (removed_all.injected_duplicates, removed_all.removed_duplicates) == (2, 2)
    removed_none = score_timeline([lab(1), lab(1), lab(1)], t, injected_duplicates=2)
    assert removed_none.removed_duplicates == 0 and removed_none.residual_duplicates == 2


def test_one_patients_leftover_duplicates_cannot_cancel_anothers_removals():
    t = [lab_truth("a", 1)]
    good = score_timeline([lab(1)], t, injected_duplicates=2)  # removed both
    bad = score_timeline([lab(1)] * 6, t, injected_duplicates=1)  # left 5 residual
    s = summarize_timeline(good + bad)
    assert (s["duplicate_removal_rate"].successes, s["duplicate_removal_rate"].n) == (2, 3)


def test_removal_rate_is_undefined_when_nothing_was_injected():
    s = summarize_timeline(score_timeline([lab(1)], [lab_truth("a", 1)], 0))
    assert s["duplicate_removal_rate"].value is None
    assert s["residual_duplicate_rate"].value == 0.0


def test_a_duplicate_extraction_also_costs_event_accuracy():
    c = score_timeline([lab(1), lab(1)], [lab_truth("a", 1)], 1)
    assert (c.events.tp, c.events.fp) == (1, 1)


# --- over the real dataset ---


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("rt") / "v1"
    write_dataset(out)
    return out


def _as_timeline(events):
    """A perfect timeline: every fact once, dated events in date order, undated last."""
    ordered = sorted(events, key=lambda e: (e.event_date is None, e.event_date or date.min,
                                            e.event_id))
    return [PredictedEvent(e.event_type, e.event_date, e.date_precision, dict(e.data))
            for e in ordered]


def test_a_perfect_timeline_is_perfectly_ordered_and_duplicate_free(dataset):
    total = TimelineCounts()
    for pid in loader.patient_ids(dataset):
        truth_json = loader.load_truth(dataset, pid)
        events = loader.events_from_truth(truth_json)
        injected = truth_json["tags"]["duplicates_injected"]
        c = score_timeline(_as_timeline(events), events, injected)
        assert c.events.fp == 0 and c.events.fn == 0 and c.residual_duplicates == 0, pid
        assert c.ordering_correct == c.ordering_pairs
        total = total + c
    s = summarize_timeline(total)
    assert s["ordering_accuracy"].value == 1.0 and s["ordering_accuracy"].n > 500
    assert s["duplicate_removal_rate"].value == 1.0 and s["duplicate_removal_rate"].n > 0
    assert s["residual_duplicate_rate"].value == 0.0


def test_a_shuffled_timeline_scores_near_chance_on_ordering(dataset):
    rng = random.Random(3)
    right = pairs = 0
    for pid in loader.patient_ids(dataset):
        events = loader.events_from_truth(loader.load_truth(dataset, pid))
        shuffled = _as_timeline(events)
        rng.shuffle(shuffled)
        c = score_timeline(shuffled, events, 0)
        right, pairs = right + c.ordering_correct, pairs + c.ordering_pairs
    assert 0.4 < right / pairs < 0.6


def test_perfect_retrieval_on_the_real_questions_scores_full_recall(dataset):
    found = gold = 0
    for pid in loader.patient_ids(dataset):
        truth_json = loader.load_truth(dataset, pid)
        quotes = {e["event_id"]: e["source"]["quote"] for e in truth_json["events"]}
        for q in loader.load_questions(dataset, pid)["qa"]:
            if not q["gold_evidence_ids"]:
                assert score_retrieval([], set(q["gold_evidence_ids"]), quotes) is None
                continue
            ranking = [item(g) for g in q["gold_evidence_ids"]]
            c = score_retrieval(ranking, set(q["gold_evidence_ids"]), quotes, ks=(10,))
            found, gold = found + c.found[10], gold + c.gold_total
            assert c.reciprocal_ranks.value == 1.0
    assert found == gold and gold > 500
