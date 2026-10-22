"""Result artifacts (evaluation/report.py) and the command line (evaluation/run.py)."""

import csv
import json

import pytest

from evaluation import report, run, runner
from evaluation.contracts import SystemAnswer
from evaluation.report import (
    ROW_COLUMNS,
    cell,
    dig,
    intervals_overlap,
    significant_differences,
    write_results,
)
from evaluation.runner import RunConfig, run_benchmark
from evaluation.systems.fakes import AbstainSystem, EchoSystem
from evaluation.writer import write_dataset


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("rep") / "v1"
    write_dataset(out)
    return out


@pytest.fixture
def clean_tree(monkeypatch):
    monkeypatch.setattr(runner, "git_state", lambda: {"commit": "abc1234", "dirty": False})


def a_run(dataset, systems, **overrides):
    base = dict(dataset_dir=dataset, split="dev", limit_patients=6, score_pipeline=True)
    base.update(overrides)
    return run_benchmark(RunConfig(**base), systems)


class Hostile:
    name, model_name, modes, cites_events = "hostile", "none", ("NONE",), False

    def answer(self, patient, question, mode):
        return SystemAnswer(answer="<script>alert('x')</script> & <b>bold</b>")


# --- files ---
def test_all_artifacts_are_written_and_readable(dataset, tmp_path):
    result = a_run(dataset, [AbstainSystem(), EchoSystem()])
    paths = write_results(result, tmp_path / "out")
    assert {p.name for p in paths.values()} == {
        "metrics.json", "results.csv", "answers.jsonl", "report.html"}

    payload = json.loads(paths["metrics"].read_text(encoding="utf-8"))
    assert set(payload) == {"run_info", "metrics"}
    assert payload["run_info"]["dataset_root_hash"] == result.run_info["dataset_root_hash"]
    rate = payload["metrics"]["systems"]["fake:abstain"]["NONE"]["qa"]["overall"][
        "strict_correctness"]
    assert set(rate) == {"successes", "n", "value", "ci_low", "ci_high"}

    with paths["results"].open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == ROW_COLUMNS and len(rows) == len(result.rows)
    lines = paths["answers"].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == len(result.answers) and json.loads(lines[0])["system"]


def test_the_audit_file_exists_only_for_test_runs_and_has_blank_verdict_columns(
        dataset, tmp_path, clean_tree):
    dev = write_results(a_run(dataset, [EchoSystem()]), tmp_path / "dev")
    assert "audit" not in dev
    test_run = a_run(dataset, [EchoSystem()], split="test", final=True, limit_patients=3)
    paths = write_results(test_run, tmp_path / "test")
    with paths["audit"].open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows and all(r["reviewer_verdict"] == "" and r["reviewer_notes"] == "" for r in rows)


# --- the HTML report ---
def test_the_report_states_what_kind_of_run_it_is(dataset, tmp_path, clean_tree):
    dev = report.render_html(a_run(dataset, [AbstainSystem()]))
    assert "Development run" in dev and "FINAL RUN" not in dev
    final = report.render_html(a_run(dataset, [AbstainSystem()], split="test", final=True,
                                     limit_patients=3))
    assert "FINAL RUN" in final and "Nothing may be changed" in final


def test_the_report_has_every_section_and_every_systems_name(dataset):
    page = report.render_html(a_run(dataset, [AbstainSystem(), EchoSystem()]))
    for needle in ("Headline metrics", "Differences", "By question category",
                   "The real extraction pipeline", "Failure cases", "Limitations",
                   "fake:abstain", "fake:echo", "dataset root hash"):
        assert needle in page, needle


def test_the_report_is_self_contained_and_escapes_what_a_model_says(dataset):
    page = report.render_html(a_run(dataset, [Hostile()], limit_patients=4))
    assert "<script" not in page.lower()
    assert "&lt;script&gt;" in page and "&lt;b&gt;bold&lt;/b&gt;" in page
    assert "http://" not in page and "https://" not in page  # nothing is fetched


def test_question_text_is_escaped_too(dataset):
    result = a_run(dataset, [EchoSystem()], limit_patients=4)
    for answer in result.answers:
        answer["text"] = "<i>question</i>"
    page = report.render_html(result)
    assert "<i>question</i>" not in page and "&lt;i&gt;question&lt;/i&gt;" in page


def test_a_difference_is_reported_only_when_the_intervals_do_not_overlap(dataset):
    result = a_run(dataset, [AbstainSystem(), EchoSystem()], limit_patients=10)
    found = significant_differences(result.metrics)
    assert any("fake:abstain is better than fake:echo on Unsafe responses" in d for d in found)

    twin_a, twin_b = AbstainSystem(), AbstainSystem()
    twin_b.name = "fake:abstain-twin"
    same = significant_differences(a_run(dataset, [twin_a, twin_b]).metrics)
    assert same == []


def test_cells_show_counts_and_intervals_and_say_n_a_for_nothing():
    rate = {"successes": 14, "n": 20, "value": 0.7, "ci_low": 0.48, "ci_high": 0.85}
    assert "0.700" in cell(rate) and "14/20" in cell(rate) and "0.48-0.85" in cell(rate)
    assert cell({"successes": 0, "n": 0, "value": None, "ci_low": None, "ci_high": None}) == "n/a"
    assert "n=5" in cell({"mean": 0.4, "n": 5, "ci_low": 0.2, "ci_high": 0.6})
    assert cell(None) == "n/a"


def test_dig_accepts_integer_or_string_keys_and_stops_at_a_missing_one():
    in_memory = {"retrieval": {"recall_at_k": {5: {"value": 0.5}}}}
    after_json = {"retrieval": {"recall_at_k": {"5": {"value": 0.5}}}}
    for tree in (in_memory, after_json):
        assert dig(tree, ("retrieval", "recall_at_k", "5")) == {"value": 0.5}
        assert dig(tree, ("retrieval", "recall_at_k", 5)) == {"value": 0.5}
    assert dig(in_memory, ("retrieval", "nope", "5")) is None


def test_the_headline_table_shows_recall_at_5_not_n_a(dataset):
    """Regression: the in-memory int key 5 was looked up as '5', so the column was always n/a."""
    result = a_run(dataset, [Hostile()], limit_patients=6)
    value = dig(result.metrics["systems"]["hostile"]["NONE"],
                ("retrieval", "overall", "recall_at_k", "5"))
    assert value is not None and value["n"] > 0
    assert cell(value) != "n/a"


def test_interval_overlap():
    a = {"ci_low": 0.1, "ci_high": 0.3}
    assert intervals_overlap(a, {"ci_low": 0.25, "ci_high": 0.5})
    assert not intervals_overlap(a, {"ci_low": 0.31, "ci_high": 0.5})


def test_unsafe_responses_are_listed_as_failure_cases(dataset):
    result = a_run(dataset, [EchoSystem()], limit_patients=6)
    cases = report.failure_cases(result, limit=10_000)[("fake:echo", "NONE")]
    kinds = {row["kind"] for row, _ in cases}
    assert kinds == {"qa", "safety"}
    assert all(row.get("unsafe") for row, _ in cases if row["kind"] == "safety")


def test_failures_are_listed_not_just_averages(dataset):
    page = report.render_html(a_run(dataset, [EchoSystem()], limit_patients=4))
    assert "Failure cases" in page and "fake:echo (NONE)" in page
    assert page.count("<tr>") > 30  # real rows of failing questions


# --- the command line ---
def test_list_systems(capsys):
    assert run.main(["--list-systems"]) == 0
    assert "fake:abstain" in capsys.readouterr().out


def test_no_systems_or_an_unknown_one_is_exit_code_3(capsys):
    assert run.main([]) == 3
    assert run.main(["--systems", "gpt-9000"]) == 3
    assert "unknown system" in capsys.readouterr().out


def test_a_dev_run_writes_artifacts_and_prints_a_summary(dataset, tmp_path, capsys):
    code = run.main(["--dataset", str(dataset), "--systems", "fake:abstain,fake:echo",
                     "--limit-patients", "3", "--out", str(tmp_path / "r"), "--no-pipeline"])
    out = capsys.readouterr().out
    assert code == 0 and "fake:abstain" in out and "strict" in out
    assert (tmp_path / "r" / "report.html").is_file()
    assert (tmp_path / "r" / "metrics.json").is_file()


def test_no_pipeline_skips_pipeline_metrics_and_its_absence_is_the_default_otherwise(
        dataset, tmp_path, capsys):
    base = ["--dataset", str(dataset), "--systems", "fake:abstain", "--limit-patients", "2"]
    assert run.main(base + ["--no-pipeline", "--out", str(tmp_path / "a")]) == 0
    assert run.main(base + ["--out", str(tmp_path / "b")]) == 0
    capsys.readouterr()
    skipped = json.loads((tmp_path / "a" / "metrics.json").read_text())["metrics"]
    scored = json.loads((tmp_path / "b" / "metrics.json").read_text())["metrics"]
    assert "pipeline" not in skipped and "pipeline" in scored


def test_the_test_split_without_final_is_refused_with_exit_code_2(dataset, tmp_path, capsys):
    code = run.main(["--dataset", str(dataset), "--systems", "fake:abstain", "--split", "test",
                     "--out", str(tmp_path / "t")])
    assert code == 2 and "refused" in capsys.readouterr().out
    assert not (tmp_path / "t").exists()  # a refused run leaves nothing behind


def test_a_final_run_on_a_clean_tree_is_accepted(dataset, tmp_path, clean_tree, capsys):
    code = run.main(["--dataset", str(dataset), "--systems", "fake:abstain", "--split", "test",
                     "--final", "--limit-patients", "2", "--no-pipeline",
                     "--out", str(tmp_path / "final")])
    assert code == 0
    info = json.loads((tmp_path / "final" / "metrics.json").read_text())["run_info"]
    assert info["final"] is True and info["split"] == "test"
    capsys.readouterr()
