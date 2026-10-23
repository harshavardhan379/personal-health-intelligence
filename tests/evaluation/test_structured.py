"""The structured-retrieval baseline (YGH-70) and EXTRACTED-mode citation mapping."""

import re
from datetime import date

import pytest

from evaluation import run
from evaluation.citations import translate_answer, truth_id_map
from evaluation.contracts import (
    AnswerClaim,
    AskedQuestion,
    EvidenceItem,
    OracleEvent,
    PatientRecord,
    PredictedEvent,
    SystemAnswer,
)
from evaluation.loader import events_from_truth, load_truth, patient_record
from evaluation.report import dig
from evaluation.runner import RunConfig, run_benchmark
from evaluation.systems import REGISTRY, get_system
from evaluation.systems.naive_rag import DOCUMENT_PREFIX, QUERY_PREFIX, REFUSAL, TOP_K
from evaluation.systems.structured import (
    SYSTEM_PROMPT,
    StructuredRetrievalSystem,
    date_text,
    event_line,
    parse_claims,
)
from evaluation.writer import write_dataset

DIM = 128


def word_embed(texts):
    out = []
    for text in texts:
        vector = [0.0] * DIM
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            vector[sum(ord(c) * (31 ** i) for i, c in enumerate(word)) % DIM] += 1.0
        out.append(vector)
    return out


def scripted(reply, seen=None):
    def chat(messages):
        if seen is not None:
            seen.append(messages)
        return reply
    return chat


def oracle(event_id, event_type, day, data, precision="day", quote="QUOTE-SENTINEL"):
    return OracleEvent(event_id, event_type, day, precision, data, "DOC-SENTINEL", 3, quote)


def lab(event_id, name, value, day=date(2024, 3, 2), unit="%"):
    return oracle(event_id, "lab", day, {"test_name": name, "value": value, "unit": unit})


def patient_with(events, patient_id="p1"):
    return PatientRecord(patient_id, (), tuple(events))


def ask(system, patient, text="What was my HbA1c?", mode="ORACLE"):
    return system.answer(patient, AskedQuestion("q1", text), mode)


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("structured") / "v1"
    write_dataset(out)
    return out


# --- how an event is written -------------------------------------------------------------
def test_every_field_of_every_event_type_appears_in_its_line():
    cases = [
        (oracle("e", "lab", date(2023, 5, 17), {"test_name": "HbA1c", "value": 9.8, "unit": "%",
                                                "reference_range": "4.0-5.6"}),
         "2023-05-17 · lab · HbA1c 9.8 % (reference 4.0-5.6)"),
        (oracle("e", "medication", date(2023, 5, 17), {"medication": "Metformin", "dose": "500mg",
                                                        "frequency": "twice daily",
                                                        "course_days": 30}),
         "2023-05-17 · medication · Metformin 500mg, twice daily, for 30 days"),
        (oracle("e", "encounter", date(2023, 6, 1), {"encounter_type": "Follow-up",
                                                      "provider_or_facility": "Riverside Clinic",
                                                      "reason": "review"}),
         "2023-06-01 · encounter · Follow-up at Riverside Clinic, reason: review"),
        (oracle("e", "symptom", date(2023, 6, 1), {"symptom": "fatigue", "detail": "for a week"}),
         "2023-06-01 · symptom · fatigue, for a week"),
    ]
    for event, expected in cases:
        assert event_line(event) == expected


def test_fields_a_document_omitted_leave_no_trace_in_the_line():
    assert event_line(oracle("e", "lab", date(2023, 5, 17), {"test_name": "HbA1c", "value": 9.8,
                                                              "unit": None})) \
        == "2023-05-17 · lab · HbA1c 9.8"
    assert event_line(oracle("e", "medication", date(2023, 5, 17), {"medication": "Metformin"})) \
        == "2023-05-17 · medication · Metformin"


def test_dates_are_written_at_their_precision_and_undated_events_say_so():
    full = date(2023, 5, 17)
    assert date_text(oracle("e", "lab", full, {}, "day")) == "2023-05-17"
    assert date_text(oracle("e", "lab", full, {}, "month")) == "2023-05"
    assert date_text(oracle("e", "lab", full, {}, "year")) == "2023"
    assert date_text(oracle("e", "lab", None, {}, "none")) == "date unknown"
    assert date_text(oracle("e", "lab", full, {}, "none")) == "date unknown"


# --- retrieval and prompt ----------------------------------------------------------------
def test_the_five_nearest_events_are_shown_numbered_and_returned_as_evidence_best_first():
    events = [lab(f"e{n}", "HbA1c", 6.0 + n) for n in range(6)]
    events.append(oracle("e9", "medication", date(2024, 1, 1),
                         {"medication": "Warfarin", "dose": "5mg"}))
    seen = []
    system = StructuredRetrievalSystem(chat=scripted("It was 6.0 [1].", seen), embed=word_embed)
    answer = ask(system, patient_with(events), "What was my HbA1c lab value?")

    assert len(answer.evidence) == TOP_K
    assert all("HbA1c" in item.text for item in answer.evidence)
    assert all(len(item.event_ids) == 1 for item in answer.evidence)
    prompt = seen[0][1]["content"]
    assert [f"[{n}] " in prompt for n in range(1, 6)] == [True] * 5 and "[6] " not in prompt
    assert "Warfarin" not in prompt
    assert answer.evidence[0].text in prompt


def test_the_model_sees_only_the_lines_not_ids_documents_pages_or_quotes():
    seen = []
    system = StructuredRetrievalSystem(chat=scripted(REFUSAL, seen), embed=word_embed)
    ask(system, patient_with([lab("p1-e001", "HbA1c", 9.8)], patient_id="PATIENT-SENTINEL"))
    sent = repr(seen)
    for secret in ("QUOTE-SENTINEL", "DOC-SENTINEL", "p1-e001", "PATIENT-SENTINEL"):
        assert secret not in sent
    assert seen[0][0]["content"] == SYSTEM_PROMPT and REFUSAL in SYSTEM_PROMPT


def test_events_are_embedded_once_per_patient_and_mode_with_the_document_prefix():
    calls = []

    def spy(texts):
        calls.append(list(texts))
        return word_embed(texts)

    patient = patient_with([lab("e1", "HbA1c", 9.8), lab("e2", "Creatinine", 0.8, unit="mg/dL")])
    system = StructuredRetrievalSystem(chat=scripted(REFUSAL), embed=spy)
    for _ in range(3):
        ask(system, patient)
    documents = [c for c in calls if c[0].startswith(DOCUMENT_PREFIX)]
    queries = [c for c in calls if c[0].startswith(QUERY_PREFIX)]
    assert len(documents) == 1 and len(queries) == 3
    assert all(t.startswith(DOCUMENT_PREFIX) for t in documents[0])


def test_one_patients_events_never_reach_another():
    a = patient_with([lab("a1", "Warfarin-level", 2.1)], "a")
    b = patient_with([lab("b1", "Insulin-level", 9.1)], "b")
    system = StructuredRetrievalSystem(chat=scripted(REFUSAL), embed=word_embed)
    assert [i.event_ids for i in ask(system, a).evidence] == [["a1"]]
    assert [i.event_ids for i in ask(system, b).evidence] == [["b1"]]


def test_a_patient_with_no_events_abstains_without_calling_the_model():
    system = StructuredRetrievalSystem(chat=lambda m: pytest.fail("no model call"),
                                       embed=word_embed)
    answer = ask(system, patient_with([]))
    assert answer.abstained and answer.evidence == []


def test_questions_where_every_event_fit_in_the_top_five_are_counted():
    system = StructuredRetrievalSystem(chat=scripted(REFUSAL), embed=word_embed)
    ask(system, patient_with([lab(f"e{n}", "HbA1c", n) for n in range(5)], "five"))
    ask(system, patient_with([lab(f"e{n}", "HbA1c", n) for n in range(6)], "six"))
    assert system.diagnostics["whole_record_given"] == 1


# --- answers, claims and citations ------------------------------------------------------
def test_sentences_become_claims_citing_the_events_whose_numbers_they_carry():
    claims, invalid = parse_claims(
        "Your HbA1c was 9.8 % on 2023-05-17 [1]. Metformin was started [2][3]. It is high.",
        ["e1", "e2", "e3"])
    assert [(c.text, c.evidence_ids) for c in claims] == [
        ("Your HbA1c was 9.8 % on 2023-05-17.", ["e1"]),
        ("Metformin was started.", ["e2", "e3"]),
        ("It is high.", [])]
    assert invalid == 0


def test_a_citation_naming_no_event_shown_is_invalid_and_never_becomes_evidence():
    claims, invalid = parse_claims("It was 9.8 [7] and also [0] and [2].", ["e1", "e2"])
    assert claims[0].evidence_ids == ["e2"] and invalid == 2


def test_the_same_event_cited_twice_in_a_sentence_counts_once():
    claims, _ = parse_claims("HbA1c 9.8 [1][1].", ["e1"])
    assert claims[0].evidence_ids == ["e1"]


def test_the_answer_text_has_the_markers_removed_and_its_claims_carry_the_ids():
    system = StructuredRetrievalSystem(
        chat=scripted("Your HbA1c was 9.8 % [1]. That is above range [1]."), embed=word_embed)
    answer = ask(system, patient_with([lab("e1", "HbA1c", 9.8)]))
    assert "[" not in answer.answer
    assert answer.answer == "Your HbA1c was 9.8 %. That is above range."
    assert [c.evidence_ids for c in answer.claims] == [["e1"], ["e1"]]
    assert not answer.abstained


def test_exact_refusal_abstains_and_other_wording_is_a_near_miss_not_an_abstention():
    patient = patient_with([lab("e1", "HbA1c", 9.8)])
    for reply, key, abstained in (
            (REFUSAL, "exact_refusals", True),
            (f'"{REFUSAL.upper()}"', "exact_refusals", True),
            ("The events do not mention a result.", "near_misses", False),
            ("It was 9.8 [1].", "answered", False)):
        system = StructuredRetrievalSystem(chat=scripted(reply), embed=word_embed)
        answer = ask(system, patient)
        assert answer.abstained is abstained, reply
        assert system.diagnostics[key] == 1, reply
        assert answer.evidence  # what was retrieved is reported even when it refuses


def test_diagnostics_count_invalid_citations_and_answers_that_cite():
    system = StructuredRetrievalSystem(chat=scripted("A [1]. B [9]."), embed=word_embed)
    ask(system, patient_with([lab("e1", "HbA1c", 9.8)]))
    ask(system, patient_with([lab("e1", "HbA1c", 9.8)], "other"))
    assert system.diagnostics["invalid_citations"] == 2
    assert system.diagnostics["answers_with_a_citation"] == 2


# --- EXTRACTED mode ---------------------------------------------------------------------
def test_extracted_mode_reads_the_pipelines_events_under_ids_of_its_own():
    extracted = [PredictedEvent("lab", date(2024, 3, 2), "day",
                                {"test_name": "HbA1c", "value": 7.1, "unit": "%"}),
                 PredictedEvent("medication", date(2024, 3, 2), "day",
                                {"medication": "Metformin", "dose": "500mg"})]
    calls = []
    system = StructuredRetrievalSystem(
        chat=scripted("It was 7.1 [1]."), embed=word_embed,
        extract=lambda patient: calls.append(patient) or extracted)
    patient = PatientRecord("p1", ())
    answer = ask(system, patient, "HbA1c result", mode="EXTRACTED")
    assert answer.claims[0].evidence_ids == ["x1"]
    assert list(system.extracted_events(patient)) == ["x1", "x2"]
    ask(system, patient, "again", mode="EXTRACTED")
    assert len(calls) == 1  # extracted once per patient


def test_oracle_and_extracted_events_are_kept_apart_for_the_same_patient():
    system = StructuredRetrievalSystem(
        chat=scripted(REFUSAL), embed=word_embed,
        extract=lambda p: [PredictedEvent("lab", date(2024, 1, 1), "day",
                                          {"test_name": "Other", "value": 1, "unit": "x"})])
    patient = patient_with([lab("e1", "HbA1c", 9.8)])
    assert ask(system, patient, mode="ORACLE").evidence[0].event_ids == ["e1"]
    assert ask(system, patient, mode="EXTRACTED").evidence[0].event_ids == ["x1"]


# --- mapping extracted ids to the truth ------------------------------------------------
def test_matched_extracted_events_take_the_truth_id_and_the_rest_are_marked(dataset):
    truth = events_from_truth(load_truth(dataset, "p001"))
    t = next(e for e in truth if e.event_type == "lab")
    right = PredictedEvent(t.event_type, t.event_date, t.date_precision, dict(t.data))
    invented = PredictedEvent("lab", date(1999, 1, 1), "day",
                              {"test_name": "Nothing", "value": 1, "unit": "x"})
    mapping = truth_id_map({"x1": right, "x2": invented}, truth)
    assert mapping == {"x1": t.event_id, "x2": "unmatched:x2"}


def test_two_extracted_copies_of_one_event_map_one_to_the_truth_and_one_to_unmatched(dataset):
    truth = events_from_truth(load_truth(dataset, "p001"))
    t = next(e for e in truth if e.event_type == "lab")
    copy = lambda: PredictedEvent(t.event_type, t.event_date, t.date_precision,  # noqa: E731
                                  dict(t.data))
    mapping = truth_id_map({"x1": copy(), "x2": copy()}, truth)
    assert sorted(mapping.values()) == sorted([t.event_id, "unmatched:x2"]) \
        or sorted(mapping.values()) == sorted([t.event_id, "unmatched:x1"])


def test_translation_rewrites_evidence_and_claims_and_marks_unknown_ids():
    answer = SystemAnswer(
        answer="a", abstained=False,
        evidence=[EvidenceItem(event_ids=["x1"], text="t1"), EvidenceItem(event_ids=["x2"])],
        claims=[AnswerClaim("c", ["x1", "x9"]), AnswerClaim("d", [])])
    out = translate_answer(answer, {"x1": "p1-e001", "x2": "unmatched:x2"})
    assert [e.event_ids for e in out.evidence] == [["p1-e001"], ["unmatched:x2"]]
    assert out.evidence[0].text == "t1"
    assert [c.evidence_ids for c in out.claims] == [["p1-e001", "unknown:x9"], []]
    assert out.answer == "a" and answer.evidence[0].event_ids == ["x1"]  # original untouched


# --- through the benchmark ---------------------------------------------------------------
def test_a_benchmark_run_scores_retrieval_in_both_modes_and_translates_the_ids(dataset):
    system = StructuredRetrievalSystem(chat=scripted("It was 7.1 [1]."), embed=word_embed)
    result = run_benchmark(RunConfig(dataset_dir=dataset, limit_patients=4, score_pipeline=False),
                           [system])
    for mode in ("ORACLE", "EXTRACTED"):
        retrieval = result.metrics["systems"]["structured"][mode]["retrieval"]["overall"]
        recall5 = dig(retrieval, ("recall_at_k", "5"))
        assert recall5["n"] > 0, mode
    oracle_recall = dig(result.metrics["systems"]["structured"]["ORACLE"]["retrieval"]["overall"],
                        ("recall_at_k", "5"))
    assert oracle_recall["successes"] > 0

    extracted = [a for a in result.answers if a["mode"] == "EXTRACTED" and a["evidence"]]
    ids = {i for a in extracted for e in a["evidence"] for i in e["event_ids"]}
    assert ids and not any(re.fullmatch(r"x\d+", i) for i in ids)  # nothing left untranslated
    assert any(re.fullmatch(r"p\d+-e\d+", i) for i in ids)  # some matched a truth event


def test_extraction_cannot_raise_recall_above_what_the_pipeline_found(dataset):
    system = StructuredRetrievalSystem(chat=scripted(REFUSAL), embed=word_embed)
    result = run_benchmark(RunConfig(dataset_dir=dataset, limit_patients=6, score_pipeline=False),
                           [system])
    metrics = result.metrics["systems"]["structured"]
    o = dig(metrics["ORACLE"]["retrieval"]["overall"], ("recall_at_k", "5"))
    x = dig(metrics["EXTRACTED"]["retrieval"]["overall"], ("recall_at_k", "5"))
    assert x["value"] < o["value"]


def test_registered_with_both_modes_and_citations():
    assert "structured" in REGISTRY
    system = get_system("structured")
    assert system.modes == ("ORACLE", "EXTRACTED") and system.cites_events is True
    assert "nomic-embed-text" in system.model_name


def test_the_pipeline_is_a_langgraph_graph_with_the_agreed_steps():
    graph = StructuredRetrievalSystem(chat=scripted(REFUSAL), embed=word_embed)._graph.get_graph()
    assert set(graph.nodes) >= {"retrieve", "generate", "finalise", "refuse"}
    edges = {(e.source, e.target) for e in graph.edges}
    assert {("__start__", "retrieve"), ("retrieve", "generate"), ("retrieve", "refuse"),
            ("generate", "finalise"), ("finalise", "__end__"), ("refuse", "__end__")} <= edges


def test_the_cli_prints_what_the_system_kept_about_itself(dataset, tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(REGISTRY, "structured",
                        lambda: StructuredRetrievalSystem(chat=scripted(REFUSAL),
                                                          embed=word_embed))
    code = run.main(["--dataset", str(dataset), "--systems", "structured", "--limit-patients", "2",
                     "--no-pipeline", "--out", str(tmp_path / "o")])
    out = capsys.readouterr().out
    assert code == 0 and "events_indexed_oracle" in out and "events_indexed_extracted" in out


def test_patient_record_in_oracle_mode_carries_events_the_system_can_read(dataset):
    record = patient_record(dataset, "p001", "ORACLE")
    system = StructuredRetrievalSystem(chat=scripted(REFUSAL), embed=word_embed)
    answer = ask(system, record, "HbA1c result")
    assert answer.evidence and all(i.event_ids[0].startswith("p001-e") for i in answer.evidence)


def test_the_instruction_tells_the_model_how_to_cite_and_how_to_refuse():
    assert "square brackets" in SYSTEM_PROMPT and "[1]" in SYSTEM_PROMPT
    assert "only the numbered events" in SYSTEM_PROMPT and REFUSAL in SYSTEM_PROMPT
