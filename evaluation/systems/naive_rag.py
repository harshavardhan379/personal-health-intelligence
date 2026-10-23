"""Baseline 2 (YGH-69): vector search over the raw text of the patient's documents.

question -> embed -> top 5 of the patient's chunks by cosine similarity -> model. No events, no
structure, no verification, no safety layer. Decisions (agreed with the owner, one at a time):

  1. Real embeddings from Ollama (`nomic-embed-text`), not the product's placeholder hashing
     embedding, which matches words rather than meaning and would make this a strawman. The
     model's documented `search_document:` / `search_query:` prefixes are used.
  2. Recursive chunking, 500 characters with 50 overlap (evaluation/systems/chunking.py), over
     the dataset's text layer of each document; pages are joined with a blank line.
  3. The top 5 chunks go to the model, and are the evidence: the retrieval metrics (Recall@3 and
     @5) score exactly what the model was given, ranked best first.
  4. The instruction says to answer only from the excerpts and, if they do not contain the answer,
     to reply with the exact sentence below. Only that sentence sets `abstained`; a refusal in
     other words is an answer, counted as a near miss in `diagnostics`.

Each patient's chunks are embedded once and kept for that patient's questions.

The pipeline is a LangGraph graph (owner's decision): retrieve -> generate -> finalise, with a
shortcut to `refuse` for a record with no text. The graph only orders the steps; every step is
plain code in this file. LangSmith tracing is forced off below: patient text must never leave
the machine except to the local Ollama server.
"""

import os
import re
from collections import Counter
from typing import Callable, Optional, TypedDict

# Forced, not defaulted: a developer's shell must not switch tracing on for a benchmark run.
for _tracing in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
    os.environ[_tracing] = "false"

import httpx  # noqa: E402
import numpy as np  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402

from evaluation.contracts import EvidenceItem, SystemAnswer  # noqa: E402
from evaluation.systems.chunking import split_text  # noqa: E402
from evaluation.systems.direct_llm import is_exact_refusal, ollama_chat  # noqa: E402

REFUSAL = "The records do not contain the answer to that question."
TOP_K = 5
DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "

SYSTEM_PROMPT = (
    "You answer questions about a person's own health records using only the excerpts you are "
    "given. If the excerpts do not contain the answer, reply with exactly: " + REFUSAL
)

# A refusal in the model's own words: counted as an answer, but reported as a near miss.
_REFUSAL_IN_OTHER_WORDS = re.compile(
    r"(not (?:contain|mention|include|provide|specif)|no (?:information|record|mention)|"
    r"(?:can(?:'|no)t|cannot|unable to) (?:find|determine|answer|tell)|"
    r"(?:does|do)(?:n't| not) (?:contain|say|show|list))", re.IGNORECASE)


class _State(TypedDict, total=False):
    patient: object
    question: object
    top: list  # the chunks sent to the model, best first
    reply: str
    result: SystemAnswer


def ollama_embed(base_url: str, model: str, timeout: float) -> Callable[[list], list]:
    def embed(texts: list) -> list:
        response = httpx.post(f"{base_url.rstrip('/')}/api/embed",
                              json={"model": model, "input": texts}, timeout=timeout)
        response.raise_for_status()
        return response.json()["embeddings"]
    return embed


def chunks_of(patient) -> list:
    chunks = []
    for document in patient.documents:
        chunks.extend(split_text("\n\n".join(page.strip() for page in document.pages())))
    return chunks


class NaiveRAGSystem:
    name = "naive_rag"
    modes = ("NONE",)
    cites_events = False

    def __init__(self, chat: Optional[Callable[[list], str]] = None,
                 embed: Optional[Callable[[list], list]] = None,
                 base_url: Optional[str] = None, model: Optional[str] = None,
                 embed_model: Optional[str] = None, timeout: float = 120.0):
        base_url = base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        llm = model or os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
        self.embed_model = embed_model or os.environ.get("EMBED_MODEL", "nomic-embed-text")
        self.model_name = f"{llm} + {self.embed_model}"
        self._chat = chat or ollama_chat(base_url, llm, timeout)
        self._embed = embed or ollama_embed(base_url, self.embed_model, timeout)
        self._index: dict = {}  # patient_id -> (chunks, unit-length vectors)
        self.diagnostics: Counter = Counter()
        self._graph = self._build_graph()

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

    def _chunks_and_vectors(self, patient):
        if patient.patient_id not in self._index:
            chunks = chunks_of(patient)
            vectors = self._normalised(self._embed([DOCUMENT_PREFIX + c for c in chunks])) \
                if chunks else np.zeros((0, 1))
            self._index[patient.patient_id] = (chunks, vectors)
            self.diagnostics["chunks_indexed"] += len(chunks)
        return self._index[patient.patient_id]

    @staticmethod
    def _normalised(vectors) -> np.ndarray:
        matrix = np.asarray(vectors, dtype=float)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.where(norms == 0, 1.0, norms)

    def answer(self, patient, question, mode) -> SystemAnswer:
        return self._graph.invoke({"patient": patient, "question": question})["result"]

    def _retrieve(self, state: _State) -> dict:
        chunks, vectors = self._chunks_and_vectors(state["patient"])
        if not chunks:
            return {"top": []}
        query = self._normalised(self._embed([QUERY_PREFIX + state["question"].text]))[0]
        order = np.argsort(-(vectors @ query), kind="stable")[:TOP_K]
        if len(chunks) <= TOP_K:  # nothing was left out: search had no work to do
            self.diagnostics["whole_record_given"] += 1
        return {"top": [chunks[i] for i in order]}

    def _generate(self, state: _State) -> dict:
        excerpts = "\n\n".join(f"[{n}] {chunk}" for n, chunk in enumerate(state["top"], start=1))
        reply = self._chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Excerpts from the records:\n\n{excerpts}\n\n"
                                        f"Question: {state['question'].text}"},
        ])
        return {"reply": reply.strip()}

    def _finalise(self, state: _State) -> dict:
        reply = state["reply"]
        abstained = is_exact_refusal(reply, REFUSAL)
        if abstained:
            self.diagnostics["exact_refusals"] += 1
        elif _REFUSAL_IN_OTHER_WORDS.search(reply[:200]):
            self.diagnostics["near_misses"] += 1
        else:
            self.diagnostics["answered"] += 1
        return {"result": SystemAnswer(answer=reply, abstained=abstained,
                                       evidence=[EvidenceItem(text=c) for c in state["top"]])}
