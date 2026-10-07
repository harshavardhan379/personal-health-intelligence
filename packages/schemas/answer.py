"""Structured answer shape (YGH-83). What the assistant returns for a question.

Deliberately NOT just a raw string — "claims" + "evidence_ids" are what M12
(evidence verification) will check each claim against, and "uncertainty" is
what lets the assistant admit it doesn't know rather than guess (M11-T09).
"""

from typing import Optional

from pydantic import BaseModel


class Answer(BaseModel):
    answer: str
    claims: list[str] = []
    evidence_ids: list[str] = []
    uncertainty: Optional[str] = None
    intent: str
