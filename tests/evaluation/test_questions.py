"""The question set (evaluation/questions.py).

Two kinds of check. (1) Self-consistency, applied to EVERY generated question: an "oracle
answer" built from its expected facts must satisfy it, and its forbidden facts must not
match that oracle; gold evidence must exist. (2) Template checks that recompute what a
question should say directly from the truth file, independently of the generator.
"""

import json
import re
from collections import Counter
from datetime import date

import pytest

from evaluation.catalogs import LABS, N_PATIENTS, archetype_for
from evaluation.facts import Fact, fact_matches
from evaluation.model import KEY_FIELD
from evaluation.questions import (
    CATEGORIES,
    QUESTION_SET_VERSION,
    SAFETY_CATEGORIES,
    generate_questions,
)
from evaluation.writer import write_dataset
from tests.safety.cases import CLASSIFIER_CASES, SAFE_PROBES, UNSAFE_PROBES


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    """Truth files plus their questions, from a freshly generated dataset."""
    out = tmp_path_factory.mktemp("q") / "v1"
    write_dataset(out)
    items = []
    for path in sorted((out / "truth").glob("p*.json")):
        truth = json.loads(path.read_text(encoding="utf-8"))
        qfile = json.loads((out / "questions" / path.name).read_text(encoding="utf-8"))
        items.append((truth, qfile))
    return items


def _all_qa(corpus):
    return [(truth, q) for truth, qfile in corpus for q in qfile["qa"]]


def _facts(raw):
    return [Fact.from_dict(f) for f in raw]


def _event(truth, event_id):
    return next(e for e in truth["events"] if e["event_id"] == event_id)


def _name(event):
    return str(event["data"][KEY_FIELD[event["event_type"]]])


# =====================================================================================
# Self-consistency of every question
# =====================================================================================


def test_every_question_passes_its_own_oracle_answer(corpus):
    for _, q in _all_qa(corpus):
        expected, forbidden = _facts(q["expected_facts"]), _facts(q["forbidden_facts"])
        oracle = ". ".join(f.example() for f in expected) or "I could not find that."
        assert all(fact_matches(f, oracle) for f in expected), q["question_id"]
        assert not any(fact_matches(f, oracle) for f in forbidden), (q["question_id"], oracle)


def test_every_forbidden_fact_is_detected_when_a_system_commits_it(corpus):
    for _, q in _all_qa(corpus):
        for fact in _facts(q["forbidden_facts"]):
            assert fact_matches(fact, fact.example()), (q["question_id"], fact.label)


def test_answerability_and_expected_facts_agree(corpus):
    for _, q in _all_qa(corpus):
        assert q["answerable"] == bool(q["expected_facts"]), q["question_id"]
        if not q["answerable"]:
            assert q["forbidden_facts"], q["question_id"]  # an unanswerable question can fail
            assert q["gold_evidence_ids"] == [], q["question_id"]  # nothing to retrieve


def test_gold_evidence_exists_and_answerable_questions_have_some(corpus):
    for truth, q in _all_qa(corpus):
        ids = {e["event_id"] for e in truth["events"]}
        assert set(q["gold_evidence_ids"]) <= ids, q["question_id"]
        if q["answerable"]:
            assert q["gold_evidence_ids"], q["question_id"]


def test_ids_are_unique_well_formed_and_texts_do_not_repeat_within_a_patient(corpus):
    seen = set()
    for truth, qfile in corpus:
        texts = [q["text"] for q in qfile["qa"]]
        assert len(texts) == len(set(texts))
        for i, q in enumerate(qfile["qa"], start=1):
            assert q["question_id"] == f"{truth['patient_id']}-q{i:03d}"
            assert q["question_id"] not in seen
            seen.add(q["question_id"])
            assert q["patient_id"] == truth["patient_id"] and q["split"] == truth["split"]
            assert q["category"] in CATEGORIES
            assert "{" not in q["text"] and q["text"].endswith(("?", "."))
    assert qfile["question_set_version"] == QUESTION_SET_VERSION


def test_generation_is_deterministic_and_seed_sensitive(corpus):
    truth = corpus[0][0]
    assert json.dumps(generate_questions(truth)) == json.dumps(generate_questions(truth))
    assert json.dumps(generate_questions(truth)) != json.dumps(generate_questions(truth, seed=7))


# =====================================================================================
# Coverage
# =====================================================================================


def test_every_category_appears_in_both_splits(corpus):
    for split in ("dev", "test"):
        seen = {q["category"] for truth, q in _all_qa(corpus) if truth["split"] == split}
        assert seen == set(CATEGORIES), (split, set(CATEGORIES) - seen)


def test_the_set_is_a_manageable_size(corpus):
    total = len(_all_qa(corpus))
    assert 500 <= total <= 900
    per_patient = [len(qfile["qa"]) for _, qfile in corpus]
    assert min(per_patient) >= 5


def test_questions_that_need_a_special_patient_only_appear_for_that_archetype(corpus):
    kinds = {"contradiction": {"contradiction"}, "multi_document.dedup_count": {"duplicate_upload"}}
    for truth, q in _all_qa(corpus):
        arch = next(a for a in [truth["archetype"]])
        if q["category"] == "contradiction":
            assert arch in kinds["contradiction"]
        if q["template"] == "multi_document.dedup_count":
            assert arch in kinds["multi_document.dedup_count"]
        if q["template"].startswith("missing_information.") and q["template"] in (
                "missing_information.lab_never_done",
                "missing_information.medication_never_prescribed"):
            assert arch == "missing_information"


# =====================================================================================
# Template checks, recomputed from the truth
# =====================================================================================


def _by_template(corpus, template):
    return [(t, q) for t, q in _all_qa(corpus) if q["template"] == template]


def test_lab_on_date_asks_about_a_day_dated_lab_and_expects_its_exact_value(corpus):
    rows = _by_template(corpus, "retrieval.lab_on_date")
    assert rows
    for truth, q in rows:
        (gold,) = [_event(truth, i) for i in q["gold_evidence_ids"]]
        assert gold["event_type"] == "lab" and gold["date_precision"] == "day"
        assert _name(gold) in q["text"]
        accepted = _facts(q["expected_facts"])[0].accepted
        assert f"{gold['data']['value']:.{LABS[_name(gold)].decimals}f}" in accepted
        # the date in the text is the event's date
        month = re.search(r"(\w+) (\d+), (\d{4})", q["text"])
        assert month and date.fromisoformat(gold["event_date"]).day == int(month.group(2))


def test_a_when_question_is_routed_by_how_much_of_the_date_survives(corpus):
    seen = Counter()
    for truth, q in _all_qa(corpus):
        if q["template"] in ("uncertainty.month_precision", "missing_information.undated"):
            seen[q["template"]] += 1
            if q["template"] == "missing_information.undated":
                assert q["answerable"] is False
                assert [f["kind"] for f in q["forbidden_facts"]] == ["any_date"]
            else:
                (gold,) = [_event(truth, i) for i in q["gold_evidence_ids"]]
                assert gold["date_precision"] == "month"
                assert q["answerable"] is True
                assert [f["kind"] for f in q["forbidden_facts"]] == ["day_in_month"]
    assert seen["uncertainty.month_precision"] > 0 and seen["missing_information.undated"] > 0


def test_no_day_level_answer_is_ever_expected_for_a_degraded_date(corpus):
    for truth, q in _all_qa(corpus):
        for i in q["gold_evidence_ids"]:
            event = _event(truth, i)
            if q["template"].startswith(("temporal.", "retrieval.")) and "date" in " ".join(
                    f["label"] for f in q["expected_facts"]):
                assert event["date_precision"] == "day", q["question_id"]


def test_trend_questions_expect_the_planted_direction_and_forbid_the_opposite(corpus):
    rows = _by_template(corpus, "trend.primary")
    assert rows
    for truth, q in rows:
        lab = truth["tags"]["primary_lab"]
        direction = truth["tags"]["trend"][lab]
        assert lab in q["text"]
        expected = " ".join(_facts(q["expected_facts"])[0].accepted)
        forbidden = _facts(q["forbidden_facts"])
        if direction == "decreasing":
            assert "decreas" in expected and "increasing" in forbidden[0].accepted
        elif direction == "increasing":
            assert "increas" in expected and "decreasing" in forbidden[0].accepted
        else:
            assert "stable" in expected and forbidden == []
        assert len(q["gold_evidence_ids"]) >= 2


def test_stay_length_matches_the_two_encounter_dates(corpus):
    rows = _by_template(corpus, "temporal.stay_length")
    assert rows
    for truth, q in rows:
        a, b = (_event(truth, i) for i in q["gold_evidence_ids"])
        days = abs((date.fromisoformat(a["event_date"]) - date.fromisoformat(b["event_date"])).days)
        assert 3 <= days <= 9
        assert f"{days} days" in _facts(q["expected_facts"])[0].accepted


def test_contradiction_questions_expect_both_doses_and_the_events_conflict(corpus):
    rows = [(t, q) for t, q in _all_qa(corpus) if q["category"] == "contradiction"]
    assert len(rows) >= 8
    for truth, q in rows:
        a, b = (_event(truth, i) for i in q["gold_evidence_ids"])
        assert b["event_id"] in a["conflicts_with"]
        doses = {a["data"]["dose"], b["data"]["dose"]}
        assert len(doses) == 2
        facts = _facts(q["expected_facts"])
        assert {f.accepted[0] for f in facts} == doses


def test_missing_information_questions_ask_about_things_truly_absent(corpus):
    checked = Counter()
    for truth, q in _all_qa(corpus):
        if q["template"] == "missing_information.lab_never_done":
            lab = next(f.accepted[0] for f in _facts(q["forbidden_facts"]))
            assert not any(e["event_type"] == "lab" and _name(e) == lab for e in truth["events"])
            checked["lab"] += 1
        if q["template"] == "missing_information.medication_never_prescribed":
            med = next(f.accepted[0] for f in _facts(q["forbidden_facts"]))
            assert not any(e["event_type"] == "medication" and _name(e) == med
                           for e in truth["events"])
            checked["med"] += 1
        if q["template"] == "missing_information.no_reading_on_date":
            lab = next(f.accepted[0] for f in _facts(q["forbidden_facts"]))
            assert all(e["date_precision"] == "day"
                       for e in truth["events"] if e["event_type"] == "lab" and _name(e) == lab)
            checked["date"] += 1
    assert all(checked[k] > 0 for k in ("lab", "med", "date"))


def test_dedup_questions_expect_one_and_forbid_two(corpus):
    rows = _by_template(corpus, "multi_document.dedup_count")
    assert rows
    for _, q in rows:
        assert fact_matches(_facts(q["expected_facts"])[0], "You have one result.")
        assert fact_matches(_facts(q["forbidden_facts"])[0], "You have two results.")
        assert not fact_matches(_facts(q["forbidden_facts"])[0], "You have one result.")


def test_evidence_questions_require_a_citation(corpus):
    rows = [(t, q) for t, q in _all_qa(corpus) if q["category"] == "evidence"]
    assert rows and all(q["requires_citation"] for _, q in rows)
    others = [q for _, q in _all_qa(corpus) if q["category"] != "evidence"]
    assert not any(q["requires_citation"] for q in others)


# =====================================================================================
# The safety set
# =====================================================================================


def _all_safety(corpus):
    return [s for _, qfile in corpus for s in qfile["safety"]]


def test_each_patient_has_two_must_not_answer_and_one_must_answer_question(corpus):
    for truth, qfile in corpus:
        labels = Counter(s["label"] for s in qfile["safety"])
        assert labels == {"must_not_answer": 2, "must_answer": 1}
        assert all(s["split"] == truth["split"] for s in qfile["safety"])


def test_safety_categories_are_balanced_and_valid(corpus):
    counts = Counter(s["category"] for s in _all_safety(corpus) if s["label"] == "must_not_answer")
    assert set(counts) == set(SAFETY_CATEGORIES) - {"PERSONAL_HISTORY"}
    assert set(counts.values()) == {20}  # 100 questions over 5 categories
    assert all(s["category"] == "PERSONAL_HISTORY"
               for s in _all_safety(corpus) if s["label"] == "must_answer")


def test_safety_questions_are_new_wording_not_the_seen_regression_cases(corpus):
    seen = {q.strip().lower() for q, _ in CLASSIFIER_CASES}
    seen |= {q.strip().lower() for q in UNSAFE_PROBES + SAFE_PROBES}
    for s in _all_safety(corpus):
        assert s["text"].strip().lower() not in seen, s["text"]
        assert "{" not in s["text"]


def test_safety_questions_use_the_patients_own_values(corpus):
    personal = 0
    for truth, qfile in corpus:
        names = {_name(e) for e in truth["events"]}
        for s in qfile["safety"]:
            personal += any(n in s["text"] for n in names)
    assert personal > 60


# =====================================================================================
# Archetype sanity
# =====================================================================================


def test_every_patient_gets_questions_whatever_their_archetype(corpus):
    assert len(corpus) == N_PATIENTS
    for truth, qfile in corpus:
        assert qfile["qa"], truth["patient_id"]
        assert archetype_for(int(truth["patient_id"][1:])).key == truth["archetype"]
