"""The direct-LLM baseline (YGH-68): the question and nothing else reaches the model."""

import pytest

from evaluation import run
from evaluation.contracts import AskedQuestion, DocumentInput, PatientRecord
from evaluation.runner import RunConfig, run_benchmark
from evaluation.systems import REGISTRY, get_system
from evaluation.systems.direct_llm import REFUSAL, SYSTEM_PROMPT, DirectLLMSystem, is_exact_refusal
from evaluation.writer import write_dataset


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("dllm") / "v1"
    write_dataset(out)
    return out


def scripted(reply, seen=None):
    def chat(messages):
        if seen is not None:
            seen.append(messages)
        return reply
    return chat


def ask(system, text="What was my last HbA1c?"):
    return system.answer(PatientRecord("p", ()), AskedQuestion("q1", text), "NONE")


def test_the_exact_refusal_sentence_is_an_abstention_however_it_is_dressed():
    for reply in (REFUSAL, "  " + REFUSAL.upper() + "  ", f'"{REFUSAL}"', f"`{REFUSAL}`",
                  REFUSAL + " Please check your portal."):
        assert ask(DirectLLMSystem(chat=scripted(reply))).abstained, reply


def test_a_guess_is_an_answer_not_an_abstention():
    system = DirectLLMSystem(chat=scripted("Your last HbA1c was 7.1% on 2024-03-02."))
    answer = ask(system)
    assert not answer.abstained and "7.1" in answer.answer
    assert dict(system.diagnostics) == {"answered": 1}


def test_refusing_in_other_words_is_an_answer_but_is_counted_as_a_near_miss():
    system = DirectLLMSystem(chat=scripted(
        "I don't have access to your records, but a normal HbA1c is below 5.7%."))
    assert not ask(system).abstained
    assert dict(system.diagnostics) == {"near_misses": 1}


def test_the_refusal_sentence_in_the_middle_of_a_reply_is_not_an_abstention():
    reply = "Probably 7.1. " + REFUSAL
    assert not is_exact_refusal(reply)


def test_the_model_sees_the_instruction_and_the_question_and_nothing_else(tmp_path):
    seen = []
    secret = "SECRET-DOCUMENT-TEXT-9731"
    text_path = tmp_path / "doc.txt"
    text_path.write_text(secret, encoding="utf-8")
    patient = PatientRecord("p1", (DocumentInput("doc.pdf", tmp_path / "doc.pdf", text_path),))
    system = DirectLLMSystem(chat=scripted(REFUSAL, seen))
    system.answer(patient, AskedQuestion("q", "Was my cholesterol high?"), "NONE")

    assert seen == [[{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": "Was my cholesterol high?"}]]
    assert secret not in repr(seen) and "p1" not in repr(seen)


def test_the_instruction_states_the_exact_refusal_sentence():
    assert REFUSAL in SYSTEM_PROMPT and "do not have access" in SYSTEM_PROMPT


def test_it_is_registered_modeless_and_makes_no_call_until_asked():
    assert "direct_llm" in REGISTRY
    system = get_system("direct_llm")  # no server needed to construct it
    assert system.modes == ("NONE",) and system.cites_events is False
    assert system.model_name


def test_a_benchmark_run_of_an_always_refusing_model(dataset):
    system = DirectLLMSystem(chat=scripted(REFUSAL))
    result = run_benchmark(RunConfig(dataset_dir=dataset, limit_patients=6,
                                     score_pipeline=False), [system])
    m = result.metrics["systems"]["direct_llm"]["NONE"]
    assert m["qa"]["overall"]["strict_correctness"]["successes"] == 0
    assert m["qa"]["overall"]["abstention_correct"]["value"] == 1.0
    assert m["safety"]["overall"]["unsafe_response_rate"]["successes"] == 0
    assert result.run_info["systems"]["direct_llm"]["diagnostics"]["exact_refusals"] == \
        len(result.rows)


def test_a_benchmark_run_of_a_confident_guesser_is_scored_as_wrong_and_unsafe(dataset):
    system = DirectLLMSystem(chat=scripted(
        "Your HbA1c was 7.1% on 2024-03-02. You should stop taking metformin."))
    result = run_benchmark(RunConfig(dataset_dir=dataset, limit_patients=6,
                                     score_pipeline=False), [system])
    m = result.metrics["systems"]["direct_llm"]["NONE"]
    assert m["qa"]["overall"]["abstention_correct"]["value"] == 0.0
    assert m["qa"]["overall"]["confident_wrong_rate"]["value"] == 1.0
    assert m["safety"]["overall"]["unsafe_response_rate"]["successes"] > 0


def test_an_unreachable_model_is_a_counted_error_not_a_crash(dataset):
    def broken(messages):
        raise ConnectionError("ollama is not running")
    result = run_benchmark(RunConfig(dataset_dir=dataset, limit_patients=2,
                                     score_pipeline=False), [DirectLLMSystem(chat=broken)])
    m = result.metrics["systems"]["direct_llm"]["NONE"]
    assert m["errors"] == len(result.rows) > 0


def test_the_cli_prints_what_the_system_kept_about_itself(dataset, tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(REGISTRY, "direct_llm",
                        lambda: DirectLLMSystem(chat=scripted(REFUSAL)))
    code = run.main(["--dataset", str(dataset), "--systems", "direct_llm", "--limit-patients",
                     "2", "--no-pipeline", "--out", str(tmp_path / "o")])
    out = capsys.readouterr().out
    assert code == 0 and "exact_refusals" in out


def test_the_real_call_is_plain_text_at_temperature_zero_with_the_configured_model(monkeypatch):
    import httpx

    sent = {}

    class Reply:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": REFUSAL}}

    def fake_post(url, json, timeout):
        sent.update(url=url, body=json, timeout=timeout)
        return Reply()

    monkeypatch.setattr(httpx, "post", fake_post)
    system = DirectLLMSystem(base_url="http://example.test:1234/", model="m:1", timeout=7)
    assert ask(system).abstained
    assert sent["url"] == "http://example.test:1234/api/chat"
    assert sent["body"]["model"] == "m:1" and sent["body"]["stream"] is False
    assert sent["body"]["options"]["temperature"] == 0
    assert "format" not in sent["body"]  # plain text, not the product's forced JSON
    assert sent["timeout"] == 7
