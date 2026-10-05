"""Evidence ranking (YGH-78): combine relevance, source quality and recency.

Pure function over a list of Evidence — unit-testable without a database.
"""


from packages.schemas.evidence import Evidence

# Structured matches come from confirmed, exact facts; weight them above fuzzy
# semantic matches when scores are otherwise close.
_SOURCE_WEIGHT = {"structured": 1.0, "semantic": 0.85}


def rank_evidence(evidence: list[Evidence], limit: int = 8) -> list[Evidence]:
    def score(e: Evidence) -> float:
        return e.relevance_score * _SOURCE_WEIGHT.get(e.source, 0.8)

    ranked = sorted(evidence, key=score, reverse=True)
    return ranked[:limit]
