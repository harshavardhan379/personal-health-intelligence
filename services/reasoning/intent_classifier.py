"""Query intent classification (YGH-81).

Keyword-based classifier — same philosophy as services/extraction/*_extractor.py and
services/document_processing/classify.py: a deliberate, offline placeholder for an
LLM-based classifier, behind a signature (`classify_intent(question) -> QueryIntent`)
that won't need to change when a real model is swapped in.
"""

from packages.schemas.intent import QueryIntent

_COMPARISON_KEYWORDS = ["compare", "versus", "vs", "difference between", "between my"]
_TREND_KEYWORDS = [
    "trend", "improving", "worsening", "getting better", "getting worse",
    "increasing", "decreasing",
]
_TEMPORAL_KEYWORDS = [
    "changed", "change", "since", "last time", "recently", "latest", "new",
    # YGH-87 historical phrasing — "first documented"/"when did I start" are
    # handled by the same date-ordered evidence as "what changed recently":
    # both need explicit dates, just read in different directions.
    "first documented", "first time", "when did i start", "when was",
    "initially", "started taking", "history of",
]
_SUMMARY_KEYWORDS = ["summarize", "summary", "overview", "overall"]
_DOCUMENT_KEYWORDS = ["this report", "this document", "this result", "explain this"]


def classify_intent(question: str) -> QueryIntent:
    lowered = question.lower()

    # Check most-specific intents first — a question can contain multiple
    # signals, and we want the one that most changes the retrieval strategy.
    if any(kw in lowered for kw in _COMPARISON_KEYWORDS):
        return "COMPARISON"
    if any(kw in lowered for kw in _TREND_KEYWORDS):
        return "TREND"
    if any(kw in lowered for kw in _DOCUMENT_KEYWORDS):
        return "DOCUMENT_EXPLANATION"
    if any(kw in lowered for kw in _SUMMARY_KEYWORDS):
        return "SUMMARY"
    if any(kw in lowered for kw in _TEMPORAL_KEYWORDS):
        return "TEMPORAL"

    if not lowered.strip():
        return "UNKNOWN"

    # Default: a question about health data, with no stronger signal, is a
    # plain retrieval question ("What's my cholesterol?").
    return "RETRIEVAL"