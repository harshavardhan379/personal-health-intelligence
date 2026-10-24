"""The benchmark runner (evaluation/runner.py): protections, leakage, scoring, determinism.

Systems here are tiny stand-ins. None of them is handed an answer; the spy proves what a real
system is (and is not) given.
"""

import shutil

import pytest

from evaluation import loader, runner
from evaluation.contracts import AnswerClaim, AskedQuestion, PatientRecord, SystemAnswer
from evaluation.runner import RunConfig, RunRefused, run_benchmark, select_audit_sample
from evaluation.systems import available, get_system
from evaluation.systems.fakes import AbstainSystem, EchoSystem
from evaluation.writer import write_dataset


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("run") / "v1"
    write_dataset(out)
    return out


@pytest.fixture
def clean_tree(monkeypatch):
    """The real working tree has uncommitted changes while we develop; pretend it is clean."""
    monkeypatch.setattr(runner, "git_state", lambda: {"commit": "abc1234", "dirty": False})


def config(dataset, **overrides):
    base = dict(dataset_dir=dataset, split="dev", limit_patients=3, score_pipeline=False)
    base.update(overrides)
    return RunConfig(**base)


class Spy:
    """Records everything a system is handed."""

    name, model_name, modes, cites_events = "spy", "none", ("ORACLE", "EXTRACTED", "NONE"), False

    def __init__(self):
        self.calls = []

    def answer(self, patient, question, mode):
        self.calls.append((patient, question, mode))
        return SystemAnswer(answer="no idea", abstained=True)


class Exploding:
    name, model_name, modes, cites_events = "exploding", "none", ("NONE",), False

    def answer(self, patient, question, mode):
        raise RuntimeError("model timed out")


class TwoMode:
    name, model_name, modes, cites_events = "twomode", "none", ("ORACLE", "EXTRACTED"), True

    def answer(self, patient, question, mode):
        return SystemAnswer(answer=f"mode {mode}", abstained=True)


# --- protections ---
def test_the_locked_test_split_is_refused_without_final(dataset):
    for split in ("test", "both"):
        with pytest.raises(RunRefused, match="--final"):
            run_benchmark(config(dataset, split=split), [AbstainSystem()])


def test_a_final_run_needs_a_clean_git_tree(dataset, monkeypatch):
    monkeypatch.setattr(runner, "git_state", lambda: {"commit": "abc1234", "dirty": True})
    with pytest.raises(RunRefused, match="clean git tree"):
        run_benchmark(config(dataset, split="test", final=True), [AbstainSystem()])


def test_a_final_run_on_a_clean_tree_is_allowed_and_recorded(dataset, clean_tree):
    result = run_benchmark(config(dataset, split="test", final=True), [AbstainSystem()])
    assert result.run_info["final"] is True and result.run_info["split"] == "test"
    assert result.run_info["git"] == {"commit": "abc1234", "dirty": False}
    assert {row["split"] for row in result.rows} == {"test"}


def test_an_unverified_dataset_is_refused(dataset, tmp_path):
    broken = tmp_path / "broken"
    shutil.copytree(dataset, broken)
    (broken / "truth" / "p001.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RunRefused, match="does not verify"):
        run_benchmark(config(broken), [AbstainSystem()])


def test_an_unknown_split_is_refused(dataset):
    with pytest.raises(RunRefused, match="unknown split"):
        run_benchmark(config(dataset, split="validation"), [AbstainSystem()])


# --- what a system is (not) given ---
def test_a_system_never_receives_the_question_record_or_any_expected_answer(dataset):
    spy = Spy()
    run_benchmark(config(dataset, limit_patients=2), [spy])
    assert spy.calls
    for patient, question, _mode in spy.calls:
        assert isinstance(question, AskedQuestion)
        assert set(vars(question)) == {"question_id", "text"}
        assert isinstance(patient, PatientRecord)
        blob = repr(question) + repr(patient)
        for secret in ("expected_facts", "forbidden_facts", "gold_evidence", "answerable",
                       "must_not_answer", "degradations", "archetype", "conflicts_with",
                       "duplicate_of", "stay_id", "date_omitted", "name_variant"):
            assert secret not in blob, secret


def test_the_oracle_view_keeps_provenance_and_drops_benchmark_bookkeeping(dataset):
    stay = next(p for p in loader.patient_ids(dataset)
                if loader.load_truth(dataset, p)["tags"]["hospital_stays"])
    truth = loader.events_from_truth(loader.load_truth(dataset, stay))
    view = loader.oracle_view(truth)
    assert [v.event_id for v in view] == [e.event_id for e in truth]
    assert all(v.quote and v.document_id and v.page >= 1 for v in view)
    assert any("stay_id" in e.data for e in truth)  # the truth has it ...
    assert not any("stay_id" in v.data for v in view)  # ... the system does not
    assert not hasattr(view[0], "degradations") and not hasattr(view[0], "conflicts_with")


def test_ground_truth_events_are_given_only_in_oracle_mode(dataset):
    spy = Spy()
    run_benchmark(config(dataset, limit_patients=2), [spy])
    by_mode = {name: [p.oracle_events for p, _, m in spy.calls if m == name]
               for name in ("ORACLE", "EXTRACTED", "NONE")}
    assert by_mode["ORACLE"] and all(events for events in by_mode["ORACLE"])
    assert all(events is None for events in by_mode["EXTRACTED"] + by_mode["NONE"])


def test_a_systems_documents_are_the_inputs_folder_only(dataset):
    spy = Spy()
    run_benchmark(config(dataset, limit_patients=1), [spy])
    patient = spy.calls[0][0]
    assert patient.documents
    for document in patient.documents:
        assert "inputs" in document.pdf_path.parts and "truth" not in document.pdf_path.parts
        assert document.pdf_bytes()[:4] == b"%PDF"


# --- running and scoring ---
def test_every_question_is_asked_of_every_system_in_every_mode(dataset):
    spy = Spy()
    result = run_benchmark(config(dataset, limit_patients=3), [spy])
    asked = 0
    for patient_id in loader.patient_ids(dataset)[:3]:
        questions = loader.load_questions(dataset, patient_id)
        asked += len(questions["qa"]) + len(questions["safety"])
    assert len(spy.calls) == asked * 3  # three modes
    assert len(result.rows) == len(result.answers) == asked * 3


def test_a_system_with_two_modes_runs_twice_and_is_reported_per_mode(dataset):
    result = run_benchmark(config(dataset, limit_patients=2), [TwoMode()])
    assert set(result.metrics["systems"]["twomode"]) == {"ORACLE", "EXTRACTED"}
    assert {row["mode"] for row in result.rows} == {"ORACLE", "EXTRACTED"}


def test_the_abstaining_fake_scores_exactly_as_it_logically_must(dataset):
    result = run_benchmark(config(dataset, limit_patients=10), [AbstainSystem()])
    m = result.metrics["systems"]["fake:abstain"]["NONE"]
    qa, safety = m["qa"]["overall"], m["safety"]["overall"]
    assert qa["strict_correctness"]["value"] == 0.0
    assert qa["over_abstention_rate"]["value"] == 1.0
    assert qa["abstention_correct"]["value"] == 1.0
    assert safety["unsafe_response_rate"]["value"] == 0.0
    assert safety["over_refusal_rate"]["value"] == 1.0
    assert qa["evidence_recall"]["n"] == 0  # a system that cannot cite events: not applicable


def test_the_echo_fake_is_unsafe_wrong_and_unhelpful(dataset):
    result = run_benchmark(config(dataset, limit_patients=10), [EchoSystem()])
    m = result.metrics["systems"]["fake:echo"]["NONE"]
    assert m["qa"]["overall"]["strict_correctness"]["value"] == 0.0
    assert m["qa"]["overall"]["confident_wrong_rate"]["value"] == 1.0
    assert m["safety"]["overall"]["unsafe_response_rate"]["value"] == 1.0
    mrr = m["retrieval"]["overall"]["mrr"]
    assert mrr["n"] > 0 and mrr["mean"] == 0.0  # it retrieves nothing for questions that need it


def test_metrics_are_reported_per_category(dataset):
    result = run_benchmark(config(dataset, limit_patients=10), [AbstainSystem()])
    by_category = result.metrics["systems"]["fake:abstain"]["NONE"]["qa"]["by_category"]
    assert {"retrieval", "temporal", "trend"} <= set(by_category)
    safety = result.metrics["systems"]["fake:abstain"]["NONE"]["safety"]["by_category"]
    assert "PERSONAL_HISTORY" in safety and "EMERGENCY_REQUEST" in safety


def test_a_system_that_raises_is_scored_as_abstained_and_the_errors_are_counted(dataset):
    result = run_benchmark(config(dataset, limit_patients=2), [Exploding()])
    m = result.metrics["systems"]["exploding"]["NONE"]
    assert m["errors"] == len(result.rows) > 0
    assert all(row["error"] for row in result.rows)
    assert m["qa"]["overall"]["over_abstention_rate"]["value"] == 1.0
    assert "model timed out" in result.answers[0]["error"]


def test_a_cited_event_id_flows_through_to_evidence_scoring(dataset):
    class Citer:
        name, model_name, modes, cites_events = "citer", "none", ("ORACLE",), True

        def answer(self, patient, question, mode):
            first = patient.oracle_events[0].event_id
            return SystemAnswer(answer="x", claims=[AnswerClaim("x", [first])])

    result = run_benchmark(config(dataset, limit_patients=2), [Citer()])
    qa = result.metrics["systems"]["citer"]["ORACLE"]["qa"]["overall"]
    assert qa["evidence_precision"]["n"] > 0 and qa["evidence_recall"]["n"] > 0


# --- the real pipeline's metrics ---
def test_pipeline_metrics_are_computed_once_per_run_and_independent_of_systems(dataset):
    result = run_benchmark(config(dataset, limit_patients=4, score_pipeline=True),
                           [AbstainSystem()])
    pipeline = result.metrics["pipeline"]
    assert pipeline["patients"] == 4
    assert pipeline["extraction"]["overall"]["recall"]["n"] > 20
    assert pipeline["timeline"]["ordering_accuracy"]["n"] > 0
    bare = run_benchmark(config(dataset, limit_patients=4, score_pipeline=False),
                         [AbstainSystem()])
    assert "pipeline" not in bare.metrics


# --- determinism and bookkeeping ---
def _stable(result):
    rows = [{k: v for k, v in r.items() if k != "seconds"} for r in result.rows]
    metrics = result.metrics
    for mode_metrics in (m for s in metrics["systems"].values() for m in s.values()):
        mode_metrics.pop("median_seconds", None)
    return rows, metrics


def test_two_runs_give_identical_rows_and_metrics(dataset):
    a = run_benchmark(config(dataset, limit_patients=3), [AbstainSystem(), EchoSystem()])
    b = run_benchmark(config(dataset, limit_patients=3), [AbstainSystem(), EchoSystem()])
    assert _stable(a) == _stable(b)


def test_run_info_records_what_a_result_needs_to_be_reproduced(dataset):
    result = run_benchmark(config(dataset, limit_patients=1), [AbstainSystem()])
    info = result.run_info
    assert info["dataset_root_hash"] == loader.read_json(dataset / "manifest.json")["root_hash"]
    assert info["dataset_version"] == "1" and info["question_set_version"] == "2"
    assert info["metric_definitions_version"] == "0.4"
    assert info["systems"]["fake:abstain"] == {"model": "none", "modes": ["NONE"],
                                               "cites_events": False}
    assert info["seed"] == 2026 and info["git"]["commit"]


def test_the_registry_holds_the_fakes_and_rejects_unknown_names():
    assert {"fake:abstain", "fake:echo"} <= set(available())
    assert get_system("fake:abstain").name == "fake:abstain"
    with pytest.raises(KeyError, match="unknown system"):
        get_system("gpt-9000")


# --- the human audit sample ---
def _rows(flagged, unflagged):
    return ([{"id": f"f{i}", "flagged": True} for i in range(flagged)]
            + [{"id": f"u{i}", "flagged": False} for i in range(unflagged)])


def test_the_audit_sample_is_every_flagged_response_plus_a_fifth_of_the_rest():
    sample = select_audit_sample(_rows(7, 100), seed=1)
    ids = {r["id"] for r in sample}
    assert {f"f{i}" for i in range(7)} <= ids
    assert len([r for r in sample if not r["flagged"]]) == 20
    assert len(sample) == 27


def test_the_audit_sample_is_reproducible_and_seed_dependent():
    a, b = select_audit_sample(_rows(2, 50), 5), select_audit_sample(_rows(2, 50), 5)
    assert [r["id"] for r in a] == [r["id"] for r in b]
    assert [r["id"] for r in a] != [r["id"] for r in select_audit_sample(_rows(2, 50), 6)]


def test_only_test_split_runs_produce_an_audit_sample(dataset, clean_tree):
    dev = run_benchmark(config(dataset, limit_patients=3), [EchoSystem()])
    assert dev.audit_sample == []
    test = run_benchmark(config(dataset, split="test", final=True, limit_patients=3),
                         [EchoSystem()])
    assert test.audit_sample and all(r["split"] == "test" for r in test.audit_sample)
