"""The naive-RAG baseline (YGH-69): vector search over raw document text, then the model."""

import re

import pytest

from evaluation import run
from evaluation.contracts import AskedQuestion, DocumentInput, PatientRecord
from evaluation.loader import patient_record
from evaluation.report import dig
from evaluation.runner import RunConfig, run_benchmark
from evaluation.systems import REGISTRY, get_system
from evaluation.systems.naive_rag import (
    DOCUMENT_PREFIX,
    QUERY_PREFIX,
    REFUSAL,
    SYSTEM_PROMPT,
    TOP_K,
    NaiveRAGSystem,
    chunks_of,
    ollama_embed,
)
from evaluation.writer import write_dataset

DIM = 128


def word_embed(texts):
    """A deterministic bag-of-words embedder: similar words, similar vectors."""
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


def make_patient(tmp_path, patient_id, texts):
    documents = []
    for n, text in enumerate(texts):
        path = tmp_path / f"{patient_id}_{n}.txt"
        path.write_text(text, encoding="utf-8")
        documents.append(DocumentInput(f"{patient_id}_{n}.pdf", path.with_suffix(".pdf"), path))
    return PatientRecord(patient_id, tuple(documents))


def ask(system, patient, text="What was my HbA1c?"):
    return system.answer(patient, AskedQuestion("q1", text), "NONE")


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("rag") / "v1"
    write_dataset(out)
    return out


def filler(n):
    return "\n".join(f"Visit note {n}.{k}: routine follow up, no change, patient well."
                     for k in range(7))


def test_the_five_most_similar_chunks_go_to_the_model_best_first(tmp_path):
    texts = [f"{filler(n)}\n\nHbA1c: 9.8 % (ref 4.0-5.6) glycated haemoglobin result {n}"
             for n in range(6)]
    texts.append("Cholesterol panel: LDL 160 mg/dL, HDL 41 mg/dL, triglycerides 200")
    patient = make_patient(tmp_path, "p1", texts)
    seen = []
    system = NaiveRAGSystem(chat=scripted("The HbA1c was 9.8 %.", seen), embed=word_embed)
    answer = ask(system, patient, "What was my HbA1c glycated haemoglobin?")

    assert len(answer.evidence) == TOP_K == 5
    assert all("HbA1c" in item.text for item in answer.evidence)  # the five best are all relevant
    prompt = seen[0][1]["content"]
    assert all(f"[{n}] " in prompt for n in range(1, 6)) and "[6] " not in prompt
    assert all(item.text in prompt for item in answer.evidence)
    assert "Cholesterol panel" not in prompt  # not one of the five nearest


def test_evidence_is_ranked_so_the_best_chunk_is_first(tmp_path):
    patient = make_patient(tmp_path, "p1", [
        "Allergy list: penicillin rash noted in 2019\n\nSmoking status: never smoker",
        "Prescription: metformin 500 mg twice daily started 2023-05-17"])
    system = NaiveRAGSystem(chat=scripted(REFUSAL), embed=word_embed)
    answer = ask(system, patient, "metformin 500 mg twice daily")
    assert "metformin" in answer.evidence[0].text


def test_the_model_sees_the_instruction_the_excerpts_and_the_question_only(tmp_path):
    patient = make_patient(tmp_path, "p-secret-id",
                           ["Visit on 2024-02-01 for review of blood pressure"])
    seen = []
    system = NaiveRAGSystem(chat=scripted(REFUSAL, seen), embed=word_embed)
    ask(system, patient, "When was my blood pressure reviewed?")
    assert [m["role"] for m in seen[0]] == ["system", "user"]
    assert seen[0][0]["content"] == SYSTEM_PROMPT and REFUSAL in SYSTEM_PROMPT
    assert seen[0][1]["content"].endswith("Question: When was my blood pressure reviewed?")
    assert "p-secret-id" not in repr(seen)


def test_one_patients_chunks_never_reach_another_patient(tmp_path):
    a = make_patient(tmp_path, "a", ["Alpha record: warfarin 5 mg daily"])
    b = make_patient(tmp_path, "b", ["Beta record: insulin glargine 20 units"])
    system = NaiveRAGSystem(chat=scripted(REFUSAL), embed=word_embed)
    texts_a = [i.text for i in ask(system, a, "warfarin").evidence]
    texts_b = [i.text for i in ask(system, b, "warfarin").evidence]
    assert texts_a == ["Alpha record: warfarin 5 mg daily"]
    assert texts_b == ["Beta record: insulin glargine 20 units"]


def test_documents_are_embedded_once_per_patient_with_the_document_prefix(tmp_path):
    patient = make_patient(tmp_path, "p1", [filler(1), filler(2)])
    calls = []

    def spy(texts):
        calls.append(list(texts))
        return word_embed(texts)

    system = NaiveRAGSystem(chat=scripted(REFUSAL), embed=spy)
    for _ in range(3):
        ask(system, patient)
    document_calls = [c for c in calls if c[0].startswith(DOCUMENT_PREFIX)]
    query_calls = [c for c in calls if c[0].startswith(QUERY_PREFIX)]
    assert len(document_calls) == 1 and len(query_calls) == 3
    assert all(t.startswith(DOCUMENT_PREFIX) for t in document_calls[0])
    assert query_calls[0] == [QUERY_PREFIX + "What was my HbA1c?"]


def test_a_patient_with_no_text_abstains_without_calling_the_model(tmp_path):
    patient = PatientRecord("empty", ())
    system = NaiveRAGSystem(chat=lambda m: pytest.fail("model must not be called"),
                            embed=word_embed)
    answer = ask(system, patient)
    assert answer.abstained and answer.evidence == []


def test_pages_are_joined_and_each_document_is_chunked(tmp_path):
    patient = make_patient(tmp_path, "p1", ["page one text\fpage two text", "second document"])
    chunks = chunks_of(patient)
    assert any("page one text" in c and "page two text" in c for c in chunks)
    assert any("second document" in c for c in chunks)
    assert not any("\f" in c for c in chunks)


def test_exact_refusal_is_an_abstention_and_other_wording_is_a_near_miss(tmp_path):
    patient = make_patient(tmp_path, "p1", [filler(1)])
    for reply, key, abstained in (
            (REFUSAL, "exact_refusals", True),
            (f'"{REFUSAL.upper()}"', "exact_refusals", True),
            ("The excerpts do not mention any HbA1c result.", "near_misses", False),
            ("Your HbA1c was 7.1 %.", "answered", False)):
        system = NaiveRAGSystem(chat=scripted(reply), embed=word_embed)
        assert ask(system, patient).abstained is abstained, reply
        assert system.diagnostics[key] == 1, reply


def test_registered_and_needs_no_server_to_construct():
    assert "naive_rag" in REGISTRY
    system = get_system("naive_rag")
    assert system.modes == ("NONE",) and system.cites_events is False
    assert "nomic-embed-text" in system.model_name


def test_the_real_embedding_call_sends_the_model_and_the_texts(monkeypatch):
    import httpx

    sent = {}

    class Reply:
        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": [[0.1, 0.2]]}

    def fake_post(url, json, timeout):
        sent.update(url=url, body=json, timeout=timeout)
        return Reply()

    monkeypatch.setattr(httpx, "post", fake_post)
    assert ollama_embed("http://example.test:1/", "m", 9)(["a"]) == [[0.1, 0.2]]
    assert sent["url"] == "http://example.test:1/api/embed"
    assert sent["body"] == {"model": "m", "input": ["a"]} and sent["timeout"] == 9


def test_a_benchmark_run_scores_retrieval_on_what_the_model_was_given(dataset):
    system = NaiveRAGSystem(chat=scripted(REFUSAL), embed=word_embed)
    result = run_benchmark(RunConfig(dataset_dir=dataset, limit_patients=6, score_pipeline=False),
                           [system])
    retrieval = result.metrics["systems"]["naive_rag"]["NONE"]["retrieval"]["overall"]
    recall5, recall3 = dig(retrieval, ("recall_at_k", "5")), dig(retrieval, ("recall_at_k", "3"))
    assert recall5["n"] > 0 and 0 < recall5["value"] <= 1
    assert recall5["successes"] >= recall3["successes"]
    assert result.run_info["systems"]["naive_rag"]["diagnostics"]["chunks_indexed"] > 0


def test_the_real_word_embedder_finds_a_planted_fact_in_a_real_record(dataset):
    patient = patient_record(dataset, "p001", "EXTRACTED")
    system = NaiveRAGSystem(chat=scripted(REFUSAL), embed=word_embed)
    top = ask(system, patient, "What was my HbA1c result?").evidence
    assert any("HbA1c" in item.text for item in top)


def test_the_cli_prints_what_the_system_kept_about_itself(dataset, tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(REGISTRY, "naive_rag",
                        lambda: NaiveRAGSystem(chat=scripted(REFUSAL), embed=word_embed))
    code = run.main(["--dataset", str(dataset), "--systems", "naive_rag", "--limit-patients", "2",
                     "--no-pipeline", "--out", str(tmp_path / "o")])
    assert code == 0 and "chunks_indexed" in capsys.readouterr().out


def test_ranking_is_by_cosine_similarity_not_by_vector_length(tmp_path):
    """A long vector pointing the wrong way must lose to a short one pointing the right way."""
    patient = make_patient(tmp_path, "p1", ["aligned chunk", "long diagonal chunk"])
    vectors = {"aligned chunk": [1.0, 0.0], "long diagonal chunk": [5.0, 5.0]}

    def embed(texts):
        return [[1.0, 0.0] if t.startswith(QUERY_PREFIX)
                else vectors[t.replace(DOCUMENT_PREFIX, "")] for t in texts]

    answer = ask(NaiveRAGSystem(chat=scripted(REFUSAL), embed=embed), patient)
    assert answer.evidence[0].text == "aligned chunk"


def test_questions_where_the_whole_record_fit_in_the_top_five_are_counted(tmp_path):
    small = make_patient(tmp_path, "small", ["one short document"])
    large = make_patient(tmp_path, "large", [filler(n) for n in range(7)])
    system = NaiveRAGSystem(chat=scripted(REFUSAL), embed=word_embed)
    ask(system, small)
    ask(system, small)
    ask(system, large)
    assert len(chunks_of(large)) > TOP_K
    assert system.diagnostics["whole_record_given"] == 2


def test_a_record_of_exactly_five_chunks_counts_as_wholly_given(tmp_path):
    five = make_patient(tmp_path, "five", [filler(n) for n in range(5)])
    assert len(chunks_of(five)) == TOP_K
    system = NaiveRAGSystem(chat=scripted(REFUSAL), embed=word_embed)
    ask(system, five)
    assert system.diagnostics["whole_record_given"] == 1


def test_the_pipeline_is_a_langgraph_graph_with_the_agreed_steps():
    graph = NaiveRAGSystem(chat=scripted(REFUSAL), embed=word_embed)._graph.get_graph()
    assert {n for n in graph.nodes} >= {"retrieve", "generate", "finalise", "refuse"}
    edges = {(e.source, e.target) for e in graph.edges}
    assert ("__start__", "retrieve") in edges
    assert ("retrieve", "generate") in edges and ("retrieve", "refuse") in edges
    assert ("generate", "finalise") in edges and ("finalise", "__end__") in edges
    assert ("refuse", "__end__") in edges


def test_tracing_is_forced_off_even_when_the_environment_turns_it_on(monkeypatch):
    """Patient text must never be sent to a tracing service by a benchmark run."""
    import importlib
    import os

    from evaluation.systems import naive_rag

    for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
        monkeypatch.setenv(name, "true")
    importlib.reload(naive_rag)
    assert [os.environ[n] for n in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2",
                                    "LANGCHAIN_TRACING")] == ["false"] * 3
