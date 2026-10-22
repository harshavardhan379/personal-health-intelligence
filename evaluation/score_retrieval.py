"""Retrieval scoring (framework section 4): Recall@K, Precision@K and MRR.

Subject: the ranked evidence list a system retrieved, BEFORE any model call. Reference: the
question's gold evidence (ground-truth event ids), plus each gold event's source quote.

An evidence item is relevant to a gold event when its `event_ids` include it. A raw-text
chunk (Naive RAG) has no event ids, so it is relevant when it contains that event's source
quote. Questions with no gold evidence (missing information) are not retrieval questions:
`score_retrieval` returns None for them so the caller leaves them out.
"""

from dataclasses import dataclass, field
from typing import Optional

from evaluation.stats import Mean, Rate

DEFAULT_KS = (3, 5)
HEADLINE_K = 5


def _squash(text: str) -> str:
    return " ".join(str(text).split()).lower()


@dataclass
class RetrievalCounts:
    questions: int = 0
    gold_total: int = 0
    found: dict = field(default_factory=dict)  # k -> gold events found in the top k
    relevant: dict = field(default_factory=dict)  # k -> relevant items in the top k
    slots: dict = field(default_factory=dict)  # k -> k per question (precision denominator)
    reciprocal_ranks: Mean = field(default_factory=lambda: Mean(0.0, 0.0, 0))

    def __add__(self, other: "RetrievalCounts") -> "RetrievalCounts":
        ks = set(self.found) | set(other.found)
        return RetrievalCounts(
            questions=self.questions + other.questions,
            gold_total=self.gold_total + other.gold_total,
            found={k: self.found.get(k, 0) + other.found.get(k, 0) for k in ks},
            relevant={k: self.relevant.get(k, 0) + other.relevant.get(k, 0) for k in ks},
            slots={k: self.slots.get(k, 0) + other.slots.get(k, 0) for k in ks},
            reciprocal_ranks=self.reciprocal_ranks + other.reciprocal_ranks,
        )


def _gold_matched(item, gold_ids: set, gold_quotes: dict) -> set:
    """The gold events this one evidence item is relevant to."""
    if item.event_ids:
        return gold_ids & set(item.event_ids)
    text = _squash(item.text)
    return {g for g in gold_ids if g in gold_quotes and _squash(gold_quotes[g]) in text}


def score_retrieval(evidence: list, gold_event_ids: set, gold_quotes: dict,
                    ks: tuple = DEFAULT_KS) -> Optional[RetrievalCounts]:
    if not gold_event_ids:
        return None
    gold = set(gold_event_ids)
    matched = [_gold_matched(item, gold, gold_quotes) for item in evidence]

    counts = RetrievalCounts(questions=1, gold_total=len(gold))
    for k in ks:
        top = matched[:k]
        counts.found[k] = len(set().union(*top)) if top else 0
        counts.relevant[k] = sum(1 for m in top if m)
        counts.slots[k] = k  # the denominator is K even if fewer items came back
    first = next((rank for rank, m in enumerate(matched, start=1) if m), None)
    counts.reciprocal_ranks = Mean.of([1.0 / first if first else 0.0])
    return counts


def summarize_retrieval(counts: RetrievalCounts) -> dict:
    return {
        "questions": counts.questions,
        "recall_at_k": {k: Rate(counts.found[k], counts.gold_total) for k in sorted(counts.found)},
        "precision_at_k": {k: Rate(counts.relevant[k], counts.slots[k])
                           for k in sorted(counts.relevant)},
        "mrr": counts.reciprocal_ranks,
    }
