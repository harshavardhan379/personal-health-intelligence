"""Standardized Evidence object (YGH-79). Every retrieval path returns a list of
these, so ranking, the assistant and verification all speak one shape.
"""


from pydantic import BaseModel


class Evidence(BaseModel):
    document_id: str
    page: int
    text: str
    event_ids: list[str] = []
    relevance_score: float
    source: str  # "structured" | "semantic" | "temporal" | "trend" | "comparison" | "historical"
