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

# A decimal point only counts when digits follow it. The older pattern
# `\d+\.?\d*` swallowed a sentence-ending period ("...on 2026-04-10." ->
# "10."), so a true claim failed to match its own evidence (found via live
# testing of "What changed in my recent reports?").
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
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

# A claim that says something is NOT in the records ("Cholesterol level is not
# reported in the provided evidence"). Needs a negation word AND a
# records/reporting word, so ordinary claims containing "not" (e.g. "HbA1c did
# not change") are not mistaken for absence claims.
_ABSENCE_RE = re.compile(
    r"\b(?:no|not|none|nothing|without|insufficient)\b.*"
    r"\b(?:reported|documented|mentioned|found|available|recorded|provided|listed"
    r"|evidence|information|data|records?|mentions?)\b",
    re.IGNORECASE,
)
# The same claim made in other words ("I couldn't find your latest cholesterol result").
# Found by using the assistant: a correct "not in your records" answer was labelled unsupported.
_NOT_FOUND_RE = re.compile(
    r"\b(?:couldn'?t|could not|can'?t|cannot|can not|unable to|wasn'?t able to"
    r"|was not able to|did not|didn'?t|do not|don'?t)\s+(?:find|see|locate|identify|spot)\b",
    re.IGNORECASE,
)
# Words that make up the "is not in the records" phrasing itself. Whatever is
# left in the claim is the topic that is being declared absent.
_ABSENCE_FILLER = {
    "not", "none", "nothing", "without", "insufficient", "there", "your",
    "about", "regarding", "any", "reported", "documented", "mentioned", "found",
    "available", "recorded", "provided", "listed", "evidence", "information",
    "data", "record", "records", "mention", "mentions", "present", "confirmed",
    "medical", "health", "current", "currently", "exist", "exists", "contain",
    "contains", "include", "includes", "included",
    # "I couldn't find your latest cholesterol result": the phrasing, not the topic.
    "couldn", "can", "cannot", "unable", "able", "find", "see", "locate", "identify", "spot",
    "did", "didn", "don", "wasn", "latest", "recent", "newest", "most", "result", "results",
    "reading", "readings", "measurement", "measurements",
}

# A claim that calls something the latest ("Your latest HbA1c is 7.2%"). The cited fact must be
# the newest one on that topic in the evidence: found by using the assistant, which stated the
# January value as the latest while the April one was in the evidence and the claim was "supported".
_RECENCY_CLAIM_RE = re.compile(r"\b(?:latest|most recent|newest|current(?:ly)?)\b", re.IGNORECASE)
_RECENCY_WORDS = {
    "latest", "most", "recent", "newest", "current", "currently", "reading", "readings",
    "result", "results", "measurement", "measurements", "value", "values", "level", "levels",
}
# "Your latest HbA1c": "your" is how the claim is phrased, not what it is about.
_CLAIM_PRONOUNS = {"your", "you", "yours", "own", "mine", "ours"}
_DATE_IN_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}")

# Below this fraction of claim keywords found in the evidence text, there's
# not enough topical overlap to call it SUPPORTED even if numbers happen to
# match coincidentally.
_KEYWORD_OVERLAP_THRESHOLD = 0.5


def _keywords(text: str) -> set:
    tokens = _WORD_RE.findall(text.lower())
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 2 and not t.isdigit()}


def _numbers(text: str) -> set:
    return set(_NUMBER_RE.findall(text))


def _match_absence_claim(claim: Claim, all_evidence: list[Evidence]) -> ClaimVerification:
    """Verifies "X is not in the records". It is true when the topic is not in the
    retrieved evidence, and contradicted when every topic word is. Checked
    against ALL evidence, not just the cited items: absence is a statement about
    the whole retrieved set, and an honest abstention often cites nothing.
    """
    topic = _keywords(claim.text) - _ABSENCE_FILLER
    evidence_keywords = _keywords(" ".join(e.text for e in all_evidence))

    if topic and topic.issubset(evidence_keywords):
        return ClaimVerification(
            claim_text=claim.text,
            label="CONTRADICTED",
            citation_indices=claim.citation_indices,
            explanation=(
                "Claim says this is not documented, but the retrieved evidence "
                "does mention it."
            ),
        )
    return ClaimVerification(
        claim_text=claim.text,
        label="SUPPORTED",
        citation_indices=claim.citation_indices,
        explanation=(
            "Claim states the topic is absent, and it does not appear in the "
            "retrieved evidence."
        ),
    )


def _evidence_date(item: Evidence):
    found = _DATE_IN_TEXT.search(item.text)
    return found.group(0) if found else None


def _check_recency(claim: Claim, all_evidence: list[Evidence]):
    """CONTRADICTED when the claim calls its cited fact the latest and the evidence holds a newer
    fact on the same topic; None when this check does not apply (the normal check then runs)."""
    if not _RECENCY_CLAIM_RE.search(claim.text):
        return None
    topic = _keywords(claim.text) - _RECENCY_WORDS - _CLAIM_PRONOUNS
    if not topic:
        return None

    def on_topic(item: Evidence) -> bool:
        lowered = item.text.lower()
        return all(word in lowered for word in topic)

    dated = [(d, e) for e in all_evidence if on_topic(e) and (d := _evidence_date(e))]
    cited_dates = [
        d
        for i in claim.citation_indices
        if 1 <= i <= len(all_evidence)
        and on_topic(all_evidence[i - 1])
        and (d := _evidence_date(all_evidence[i - 1]))
    ]
    if not dated or not cited_dates:
        return None
    newest = max(d for d, _ in dated)
    if max(cited_dates) >= newest:
        return None
    return ClaimVerification(
        claim_text=claim.text,
        label="CONTRADICTED",
        citation_indices=claim.citation_indices,
        explanation=(
            "Claim calls this the most recent, but the evidence has a more recent one "
            f"dated {newest}."
        ),
    )


def match_claim_to_evidence(
    claim: Claim, all_evidence: list[Evidence]
) -> ClaimVerification:
    if _ABSENCE_RE.search(claim.text) or _NOT_FOUND_RE.search(claim.text):
        return _match_absence_claim(claim, all_evidence)

    stale = _check_recency(claim, all_evidence)
    if stale is not None:
        return stale

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
