"""Baseline 3 (YGH-70): vector search over the patient's structured health events.

question -> embed -> top 5 of the patient's events by cosine similarity -> model. No intent
routing, no verification, no safety layer. Differs from the naive-RAG baseline in one thing: the
model is given extracted facts instead of raw text chunks. Decisions (agreed with the owner, one
at a time, before any result):

  1. Search, then the top 5 events (same embedding model, search and K as naive RAG), so the
     only variable between the two baselines is chunks of text versus structured events.
  2. Each event is one fixed line holding every field, written here (not the product's lossy
     short labels): `2023-05-17 | lab | HbA1c 9.8 % (reference 4.0-5.6)`. Month-only and
     undated events read `2023-05` and `date unknown`. The same line is searched and shown.
  3. Refusal as in the other baselines: only the exact sentence sets `abstained`; refusals in
     other words are near misses. The model is asked to cite events by number, `[1]`; sentences
     become claims tied to the events they cite, so evidence and citation metrics apply.
  4. EXTRACTED mode: the events are the pipeline's, cited by ids of this system's own
     (`x1`, `x2`, ...); the runner maps them to ground-truth ids after the answer
     (evaluation/citations.py). This system never sees the truth.

Modes: ORACLE (the patient's ground-truth events, as a perfect reviewer would confirm them) and
EXTRACTED (what the real extraction pipeline finds, all auto-confirmed).
"""

import os
import re
from collections import Counter
from typing import Callable, Optional, TypedDict

import numpy as np
from langgraph.graph import END, START, StateGraph

from evaluation.contracts import AnswerClaim, EvidenceItem, SystemAnswer
from evaluation.systems.direct_llm import is_exact_refusal, ollama_chat
from evaluation.systems.naive_rag import (
    _REFUSAL_IN_OTHER_WORDS,
    DOCUMENT_PREFIX,
    QUERY_PREFIX,
    REFUSAL,
    TOP_K,
    normalised,
    ollama_embed,
)
from evaluation.systems.pipeline import extract_events

SYSTEM_PROMPT = (
    "You answer questions about a person's own health records using only the numbered events "
    "you are given. After each statement, put the number of the event it comes from in square "
    "brackets, like [1] or [2][3]. If the events do not contain the answer, reply with "
    "exactly: " + REFUSAL
)

_CITATION = re.compile(r"\[(\d+)\]")
_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")


class _State(TypedDict, total=False):
    patient: object
    question: object
    mode: str
    top: list  # (event id, line) pairs sent to the model, best first
    reply: str
    result: SystemAnswer


def date_text(event) -> str:
    if event.event_date is None or event.date_precision == "none":
        return "date unknown"
    iso = event.event_date.isoformat()
    return {"day": iso, "month": iso[:7], "year": iso[:4]}.get(event.date_precision, iso)


def _present(*parts) -> str:
    return " ".join(str(p) for p in parts if p not in (None, ""))


def event_line(event) -> str:
    """One event as one line holding every field it has."""
    d = event.data
    if event.event_type == "lab":
        text = _present(d.get("test_name"), d.get("value"), d.get("unit"))
        if d.get("reference_range"):
            text += f" (reference {d['reference_range']})"
    elif event.event_type == "medication":
        text = _present(d.get("medication"), d.get("dose"))
        if d.get("frequency"):
            text += f", {d['frequency']}"
        if d.get("course_days"):
            text += f", for {d['course_days']} days"
    elif event.event_type == "encounter":
        text = _present(d.get("encounter_type"))
        if d.get("provider_or_facility"):
            text += f" at {d['provider_or_facility']}"
        if d.get("reason"):
            text += f", reason: {d['reason']}"
    elif event.event_type == "symptom":
        text = _present(d.get("symptom"))
        if d.get("detail"):
            text += f", {d['detail']}"
    else:
        text = str(d)
    return f"{date_text(event)} · {event.event_type} · {text}"


def parse_claims(reply: str, ids: list) -> tuple:
    """Split a reply into claims; a sentence cites the events whose numbers it carries.
    Returns (claims, number of citations that named no event shown)."""
    claims, invalid = [], 0
    for piece in _SENTENCES.split(reply):
        numbers = [int(n) for n in _CITATION.findall(piece)]
        squeezed = re.sub(r"\s+", " ", _CITATION.sub("", piece))
        text = re.sub(r"\s+([.,;:!?])", r"\1", squeezed).strip()
        if not text:
            continue
        cited = []
        for number in numbers:
            if 1 <= number <= len(ids):
                if ids[number - 1] not in cited:
                    cited.append(ids[number - 1])
            else:
                invalid += 1
        claims.append(AnswerClaim(text=text, evidence_ids=cited))
    return claims, invalid


class StructuredRetrievalSystem:
    name = "structured"
    modes = ("ORACLE", "EXTRACTED")
    cites_events = True

    def __init__(self, chat: Optional[Callable[[list], str]] = None,
                 embed: Optional[Callable[[list], list]] = None,
                 extract: Optional[Callable[[object], list]] = None,
                 base_url: Optional[str] = None, model: Optional[str] = None,
                 embed_model: Optional[str] = None, timeout: float = 120.0):
        base_url = base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        llm = model or os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
        self.embed_model = embed_model or os.environ.get("EMBED_MODEL", "nomic-embed-text")
        self.model_name = f"{llm} + {self.embed_model}"
        self._chat = chat or ollama_chat(base_url, llm, timeout)
        self._embed = embed or ollama_embed(base_url, self.embed_model, timeout)
        self._extract = extract or extract_events
        self._index: dict = {}  # (patient_id, mode) -> (ids, events, lines, unit vectors)
        self.diagnostics: Counter = Counter()
        self._graph = self._build_graph()

    # --- the events a mode reads -------------------------------------------------------------
    def _events(self, patient, mode: str) -> tuple:
        key = (patient.patient_id, mode)
        if key not in self._index:
            if mode == "ORACLE":
                events = list(patient.oracle_events or ())
                ids = [e.event_id for e in events]
            else:
                events = list(self._extract(patient))
                ids = [f"x{n}" for n in range(1, len(events) + 1)]
            lines = [event_line(e) for e in events]
            vectors = normalised(self._embed([DOCUMENT_PREFIX + line for line in lines])) \
                if lines else np.zeros((0, 1))
            self._index[key] = (ids, events, lines, vectors)
            self.diagnostics[f"events_indexed_{mode.lower()}"] += len(events)
        return self._index[key]

    def extracted_events(self, patient) -> dict:
        """The pipeline's events for this patient under the ids this system cites them by; the
        runner maps them to ground-truth ids after answering (evaluation/citations.py)."""
        ids, events, _, _ = self._events(patient, "EXTRACTED")
        return dict(zip(ids, events))

    # --- the graph ---------------------------------------------------------------------------
    def _build_graph(self):
        graph = StateGraph(_State)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("generate", self._generate)
        graph.add_node("finalise", self._finalise)
        graph.add_node("refuse", lambda state: {"result": SystemAnswer(answer=REFUSAL,
                                                                       abstained=True)})
        graph.add_edge(START, "retrieve")
        graph.add_conditional_edges(
            "retrieve", lambda state: "generate" if state["top"] else "refuse",
            {"generate": "generate", "refuse": "refuse"})
        graph.add_edge("generate", "finalise")
        graph.add_edge("finalise", END)
        graph.add_edge("refuse", END)
        return graph.compile()

    def answer(self, patient, question, mode) -> SystemAnswer:
        state = {"patient": patient, "question": question, "mode": mode}
        return self._graph.invoke(state)["result"]

    def _retrieve(self, state: _State) -> dict:
        ids, _, lines, vectors = self._events(state["patient"], state["mode"])
        if not ids:
            return {"top": []}
        query = normalised(self._embed([QUERY_PREFIX + state["question"].text]))[0]
        order = np.argsort(-(vectors @ query), kind="stable")[:TOP_K]
        if len(ids) <= TOP_K:  # nothing was left out: search had no work to do
            self.diagnostics["whole_record_given"] += 1
        return {"top": [(ids[i], lines[i]) for i in order]}

    def _generate(self, state: _State) -> dict:
        shown = "\n".join(f"[{n}] {line}" for n, (_, line) in enumerate(state["top"], start=1))
        reply = self._chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Events from the records:\n\n{shown}\n\n"
                                        f"Question: {state['question'].text}"},
        ])
        return {"reply": reply.strip()}

    def _finalise(self, state: _State) -> dict:
        reply, top = state["reply"], state["top"]
        evidence = [EvidenceItem(event_ids=[event_id], text=line) for event_id, line in top]
        if is_exact_refusal(reply, REFUSAL):
            self.diagnostics["exact_refusals"] += 1
            return {"result": SystemAnswer(answer=reply, abstained=True, evidence=evidence)}

        claims, invalid = parse_claims(reply, [event_id for event_id, _ in top])
        self.diagnostics["invalid_citations"] += invalid
        if _REFUSAL_IN_OTHER_WORDS.search(reply[:200]):
            self.diagnostics["near_misses"] += 1
        else:
            self.diagnostics["answered"] += 1
        self.diagnostics["answers_with_a_citation"] += any(c.evidence_ids for c in claims)
        answer = " ".join(c.text for c in claims) or reply
        return {"result": SystemAnswer(answer=answer, claims=claims, evidence=evidence)}
