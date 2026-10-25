"""AI regression suite (YGH-100): fixed questions and the properties each answer must keep.

The cases live in ai_cases.json so adding one is a data change. Each runs the REAL pipeline over a
fixed patient record in a real Postgres (retrieval, safety gate, verifier, output guard, trace);
only the model is scripted. A case that starts failing means behaviour changed: fix the code, or,
if the change is intended, change the expectation and say why in the commit."""

import json
import re
from pathlib import Path

import pytest

from packages.schemas.models import AITrace
from tests.integration.conftest import (  # noqa: F401  (fixtures used by name)
    ScriptedProvider,
    clean,
    engine,
    session_factory,
    stack,
)

DATA = json.loads((Path(__file__).parent / "ai_cases.json").read_text(encoding="utf-8"))
CASES = DATA["cases"]
EVIDENCE_LINE = re.compile(r"^\[(\d+)\] (.*)$")


class ResolvingProvider(ScriptedProvider):
    """Replies from the case, turning `cite_matching` into the evidence numbers the model was
    actually shown in the prompt."""

    def __init__(self, case):
        super().__init__()
        self.case = case

    def generate_with_usage(self, messages):
        self.calls.append(messages)
        if "raw" in self.case:
            return self.case["raw"], {"input_tokens": 50, "output_tokens": 10}
        shown = {}
        for line in messages[1]["content"].split("Evidence:\n", 1)[1].splitlines():
            found = EVIDENCE_LINE.match(line)
            if found:
                shown[int(found.group(1))] = found.group(2)
        claims = []
        for claim in self.case["reply"]["claims"]:
            numbers = []
            for needle in claim["cite_matching"]:
                hits = [n for n, text in shown.items() if needle in text]
                assert hits, f"nothing in the evidence matches {needle!r}: {shown}"
                numbers.append(hits[0])
            claims.append({"text": claim["text"], "citations": numbers})
        reply = {"answer": self.case["reply"]["answer"], "claims": claims, "uncertainty": None}
        return json.dumps(reply), {"input_tokens": 50, "output_tokens": 10}


@pytest.fixture
def patient(stack):  # noqa: F811
    headers = stack.register()
    for number, text in enumerate(DATA["patient"], 1):
        stack.confirmed_document(headers, text, f"record-{number}.pdf")
    return stack, headers


def as_list(value):
    return value if isinstance(value, list) else [value]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_the_answer_keeps_its_properties(patient, monkeypatch, case):
    system, headers = patient
    provider = ResolvingProvider(case)
    monkeypatch.setattr("services.reasoning.generate.get_llm_provider", lambda: provider)
    expect = case["expect"]

    response = system.ask(headers, case["question"])
    assert response.status_code == 200, response.text
    body = response.json()
    answer = body["answer"].lower()
    labels = [v["label"] for v in body["claim_verifications"]]

    if "intent" in expect:
        assert body["intent"] == expect["intent"]
    if "safety_action" in expect:
        assert body["safety_action"] in as_list(expect["safety_action"]), body["safety_action"]
    if "labels" in expect:
        assert labels == expect["labels"], labels
    if "fully_verified" in expect:
        assert body["fully_verified"] is expect["fully_verified"]
    for word in expect.get("answer_contains", []):
        assert word.lower() in answer, f"{word!r} missing from {body['answer']!r}"
    for word in expect.get("answer_excludes", []):
        assert word.lower() not in answer, f"{word!r} present in {body['answer']!r}"
    if "model_called" in expect:
        assert bool(provider.calls) is expect["model_called"]
    if "evidence_min" in expect:
        assert len(body["evidence"]) >= expect["evidence_min"]
    if "evidence_max" in expect:
        assert len(body["evidence"]) <= expect["evidence_max"]
    if "status" in expect:
        with system.db() as db:
            assert db.query(AITrace).order_by(AITrace.created_at.desc()).first().status == (
                expect["status"])


def test_the_suite_is_not_quietly_shrinking():
    """Fixed questions are the point: a case is removed on purpose or not at all."""
    assert len(CASES) >= 18
    assert len({c["id"] for c in CASES}) == len(CASES)
    assert sum(1 for c in CASES if c["id"].startswith("safety-")) >= 6
    assert all(case.get("expect") for case in CASES)
