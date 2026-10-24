"""The human audit helper (evaluation/audit.py)."""

import csv
import json

import pytest

from evaluation import audit, runner
from evaluation.audit import (
    COLUMNS,
    REVIEW_FILE,
    _automatic_text,
    _expected_text,
    prepare,
    summarize,
)
from evaluation.loader import load_questions
from evaluation.report import write_results
from evaluation.runner import RunConfig, run_benchmark
from evaluation.systems.fakes import AbstainSystem, EchoSystem
from evaluation.writer import write_dataset


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("audit") / "v1"
    write_dataset(out)
    return out


@pytest.fixture
def test_run(dataset, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "git_state", lambda: {"commit": "abc1234", "dirty": False})
    result = run_benchmark(
        RunConfig(dataset_dir=dataset, split="test", final=True, limit_patients=4,
                  score_pipeline=False), [AbstainSystem(), EchoSystem()])
    write_results(result, tmp_path / "run")
    return tmp_path / "run"


def read(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def fill(path, verdicts):
    """Write verdicts (a function of the row) into the review file."""
    rows = read(path)
    for row in rows:
        row["reviewer_verdict"], row["reviewer_notes"] = verdicts(row)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


# --- prepare ------------------------------------------------------------------------------
def test_every_sampled_response_appears_with_its_question_answer_and_expectation(
        test_run, dataset):
    path = prepare(test_run, dataset)
    review, sample = read(path), read(test_run / "audit_sample.csv")
    assert list(review[0]) == COLUMNS and len(review) == len(sample) > 0
    assert {(r["system"], r["mode"], r["question_id"]) for r in review} == \
        {(r["system"], r["mode"], r["question_id"]) for r in sample}
    answers = {}
    for line in (test_run / "answers.jsonl").read_text(encoding="utf-8").splitlines():
        a = json.loads(line)
        answers[(a["system"], a["mode"], a["question_id"])] = a
    for row in review:
        shown = answers[(row["system"], row["mode"], row["question_id"])]
        assert row["system_answer"] == shown["answer"] and row["question"] == shown["text"]
        assert row["what_a_correct_answer_looks_like"] and row["automatic_verdict"]
        assert row["reviewer_verdict"] == "" and row["reviewer_notes"] == ""
    assert (test_run / audit.INSTRUCTIONS_FILE).is_file()


def test_flagged_responses_come_first_and_say_why_they_are_there(test_run, dataset):
    review = read(prepare(test_run, dataset))
    reasons = [r["in_sample_because"] for r in review]
    flagged = [x for x in reasons if x.startswith("flagged")]
    assert flagged and reasons[:len(flagged)] == flagged
    assert set(reasons) <= {"flagged by the automatic checks", "random 20% of unflagged"}


def test_a_dev_run_has_no_audit_sample_so_prepare_says_so(dataset, tmp_path):
    result = run_benchmark(RunConfig(dataset_dir=dataset, limit_patients=2, score_pipeline=False),
                           [AbstainSystem()])
    write_results(result, tmp_path / "dev")
    with pytest.raises(FileNotFoundError):
        prepare(tmp_path / "dev", dataset)
    assert audit.main(["prepare", str(tmp_path / "dev"), "--dataset", str(dataset)]) == 2


def test_what_a_correct_answer_looks_like_for_each_kind_of_question(dataset):
    record = load_questions(dataset, "p031")
    answerable = next(q for q in record["qa"] if q["answerable"])
    unanswerable = next(q for q in record["qa"] if not q["answerable"])
    must_not = next(q for q in record["safety"] if q["label"] == "must_not_answer"
                    and q["category"] != "EMERGENCY_REQUEST")
    must = next(q for q in record["safety"] if q["label"] == "must_answer")
    emergency = next(q for pid in ("p031", "p032", "p033", "p034", "p035")
                     for q in load_questions(dataset, pid)["safety"]
                     if q["category"] == "EMERGENCY_REQUEST")

    text = _expected_text(answerable, "qa")
    assert text.startswith("Must state:") and answerable["expected_facts"][0]["label"] in text
    assert "do NOT contain" in _expected_text(unanswerable, "qa")
    assert "decline or redirect" in _expected_text(must_not, "safety")
    assert "should be answered" in _expected_text(must, "safety")
    assert "emergency" in _expected_text(emergency, "safety").lower()


def test_forbidden_facts_are_listed_when_a_question_has_them(dataset):
    record = load_questions(dataset, "p031")
    with_forbidden = next(q for q in record["qa"] if q["answerable"] and q["forbidden_facts"])
    assert "Must NOT state:" in _expected_text(with_forbidden, "qa")


# --- the automatic verdict in words ------------------------------------------------------
def qa_row(**over):
    base = {"kind": "qa", "answerable": "True", "strict": "0", "abstained": "False",
            "abstention_correct": "", "unsupported_claims": "0", "medical_claim": "0"}
    base.update(over)
    return base


def safety_row(**over):
    base = {"kind": "safety", "label": "must_not_answer", "unsafe": "0", "appropriate": "1",
            "refused_must_answer": "0", "medical_claim": "0"}
    base.update(over)
    return base


def test_the_automatic_verdict_is_stated_in_plain_words_for_every_outcome():
    assert "CORRECT" in _automatic_text(qa_row(strict="1"))
    assert "declined a question the records answer" in _automatic_text(qa_row(abstained="True"))
    assert "INCORRECT" in _automatic_text(qa_row())
    unanswerable = qa_row(answerable="False")
    assert "answered a question the records cannot answer" in _automatic_text(unanswerable)
    assert "declined, as it should" in _automatic_text(
        qa_row(answerable="False", abstention_correct="1"))
    assert "2 claim(s)" in _automatic_text(qa_row(unsupported_claims="2"))
    assert "UNSAFE" in _automatic_text(safety_row(unsafe="1", appropriate="0"))
    assert "scored SAFE" in _automatic_text(safety_row())
    assert "NOT scored as appropriate" in _automatic_text(safety_row(appropriate="0"))
    assert "OVER-REFUSAL" in _automatic_text(
        safety_row(label="must_answer", refused_must_answer="1"))
    assert "medical assertion" in _automatic_text(safety_row(medical_claim="1"))


# --- summarize ----------------------------------------------------------------------------
def test_summary_counts_agreements_by_flagged_status_kind_and_system(test_run, dataset):
    path = prepare(test_run, dataset)

    def verdict(row):
        if row["in_sample_because"].startswith("flagged"):
            return "agree", ""
        # disagree with every unflagged safety row, agree with the unflagged QA rows
        return ("disagree", "scorer missed it") if row["kind"] == "safety" else ("agree", "")

    fill(path, verdict)
    summary = summarize(test_run)
    rows = read(path)
    assert summary["rows"] == summary["reviewed"] == len(rows) and summary["still_blank"] == 0
    assert summary["flagged_confirmed_or_not"]["disagree"] == 0
    unflagged_safety = [r for r in rows if r["kind"] == "safety"
                        and not r["in_sample_because"].startswith("flagged")]
    assert summary["unflagged_sample"]["disagree"] == len(unflagged_safety)
    assert summary["by_kind"]["qa"]["disagree"] == 0
    assert summary["by_kind"]["safety"]["disagree"] == len(unflagged_safety)
    assert set(summary["by_system"]) == {"fake:abstain/NONE", "fake:echo/NONE"}
    assert len(summary["disagreements"]) == len(unflagged_safety)
    assert (test_run / audit.SUMMARY_FILE).is_file()


def test_rows_left_blank_are_counted_not_treated_as_agreement(test_run, dataset):
    path = prepare(test_run, dataset)
    fill(path, lambda row: ("agree", "") if row["audit_id"] == "1" else ("", ""))
    summary = summarize(test_run)
    assert summary["reviewed"] == 1 and summary["still_blank"] == summary["rows"] - 1
    assert summary["flagged_confirmed_or_not"]["reviewed"] == 1


def test_a_verdict_that_is_not_agree_or_disagree_is_refused_with_its_row(test_run, dataset):
    path = prepare(test_run, dataset)
    fill(path, lambda row: ("yes" if row["audit_id"] == "2" else "agree", ""))
    with pytest.raises(ValueError, match="row 2"):
        summarize(test_run)
    assert audit.main(["summarize", str(test_run)]) == 2


def test_the_cli_prints_the_numbers_a_report_needs(test_run, dataset, capsys):
    assert audit.main(["prepare", str(test_run), "--dataset", str(dataset)]) == 0
    fill(test_run / REVIEW_FILE, lambda row: ("disagree", "no") if row["audit_id"] == "1"
         else ("agree", ""))
    assert audit.main(["summarize", str(test_run)]) == 0
    out = capsys.readouterr().out
    assert "rows reviewed" in out and "DISAGREE #1" in out and "unflagged sample" in out
