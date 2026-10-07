"""Query intent taxonomy (YGH-80). Classified by services/reasoning/intent_classifier.py
(YGH-81), consumed by the retrieval strategy it selects (YGH-82 onward).
"""

from typing import Literal

QueryIntent = Literal[
    "RETRIEVAL",
    "TEMPORAL",
    "COMPARISON",
    "TREND",
    "SUMMARY",
    "DOCUMENT_EXPLANATION",
    "UNKNOWN",
]