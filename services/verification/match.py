"""Claim-to-evidence matching (YGH-53, YGH-54, YGH-55).

match_claim_to_evidence() is the core verification decision: given one claim
from a generated Answer and the full evidence list it was generated from,
decide whether the claim's cited evidence actually supports it.

Deliberately NOT an LLM call — same philosophy as extraction and embeddings
(docs/ARCHITECTURE_MAP.md §10, docs/HANDOVER.md §7): a verifier that itself
calls the same kind of model it's checking is a weaker safeguard than one
that reasons independently with a separate, auditable method. Numbers are
treated specially because the real hallucination this project already found
(M11's TREND bug) was a model stating a value-adjacent conclusion that didn't
match its own cited number — a check built purely on word overlap would have
missed that; a check built purely on numbers would miss qualitative claims
("Metformin was prescribed"). This combines both.
"""

import re

from packages.schemas.answer import Claim
from packages.schemas.evidence import Evidence
from packages.schemas.verification import ClaimVerification

_NUMBER_RE = re.compile(r"\d+\.?\d*")
_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "my", "me", "i", "this",
    "that", "has", "have", "had", "it", "of", "for", "to", "in", "on", "at",
    "and", "or", "with", "be", "patient",
    # Generic clinical filler — appears across almost any fact, so sharing one
    # of these alone must never count as "same topic" (found via testing: it
    # made two different medications, "daily" being the only shared word,
    # look topically related enough to be flagged as a contradiction of each
    # other instead of correctly unrelated/unsupported).
    "daily", "twice", "once", "times", "dose", "level", "value", "result",
    "takes", "taking", "documented", "reported",
}
_WORD_RE = re.compile(r"[a-z0-9]+")

# Below this fraction of claim keywords found in the evidence text, there's
# not enough topical overlap to call it SUPPORTED even if numbers happen to
# match coincidentally.
_KEYWORD_OVERLAP_THRESHOLD = 0.5


def _keywords(text: str) -> set:
    tokens = _WORD_RE.findall(text.lower())
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 2 and not t.isdigit()}


def _numbers(text: str) -> set:
    return set(_NUMBER_RE.findall(text))


def match_claim_to_evidence(
    claim: Claim, all_evidence: list[Evidence]
) -> ClaimVerification:
    cited = [
        all_evidence[i - 1]
        for i in claim.citation_indices
        if 1 <= i <= len(all_evidence)
    ]

    if not cited:
        return ClaimVerification(
            claim_text=claim.text,
            label="INSUFFICIENT",
            citation_indices=[],
            explanation="Claim cited no valid evidence.",
        )

    evidence_text = " ".join(e.text for e in cited)
    claim_keywords = _keywords(claim.text)
    evidence_keywords = _keywords(evidence_text)
    overlap_ratio = (
        len(claim_keywords & evidence_keywords) / len(claim_keywords)
        if claim_keywords
        else 0.0
    )

    claim_numbers = _numbers(claim.text)
    evidence_numbers = _numbers(evidence_text)

    if claim_numbers:
        numbers_match = claim_numbers.issubset(evidence_numbers)
        # For numeric claims, "same topic" only needs ONE real shared keyword
        # (e.g. "metformin"), not a high overlap ratio — a short claim like
        # "Metformin dose is 10mg" naturally has a low ratio against "Metformin
        # 20mg twice daily" even though they're unmistakably about the same
        # fact. The NUMBER is what actually carries the correctness signal
        # here; keyword overlap only needs to establish relevance, not prove it.
        same_topic = len(claim_keywords & evidence_keywords) >= 1
        if numbers_match and same_topic:
            label = "SUPPORTED"
            explanation = "Claim's numeric value(s) and topic both match the cited evidence."
        elif not numbers_match and same_topic:
            # Same topic, but the claim states a number the evidence doesn't —
            # this is exactly the shape of the TREND hallucination found in M11.
            label = "CONTRADICTED"
            explanation = (
                "Claim's topic matches the cited evidence, but its numeric "
                "value does not appear in that evidence."
            )
        else:
            label = "UNSUPPORTED"
            explanation = "Cited evidence does not clearly address this claim."
    else:
        if overlap_ratio >= _KEYWORD_OVERLAP_THRESHOLD:
            label = "SUPPORTED"
            explanation = "Claim's topic matches the cited evidence."
        else:
            label = "UNSUPPORTED"
            explanation = "Cited evidence does not clearly address this claim."

    return ClaimVerification(
        claim_text=claim.text,
        label=label,
        citation_indices=claim.citation_indices,
        explanation=explanation,
    )
