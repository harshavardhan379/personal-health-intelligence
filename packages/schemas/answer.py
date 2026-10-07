"""Structured answer shape (YGH-83, YGH-84). What the assistant returns for a question.

Deliberately NOT just a raw string. "claims" + per-claim "citation_indices" are
what M12 (evidence verification) will check each claim against, and
"uncertainty" is what lets the assistant admit it doesn't know rather than
guess (M11-T09).
"""

from typing import Optional

from pydantic import BaseModel


class Claim(BaseModel):
    """One factual statement from the answer, tied to the specific evidence that
    backs it (YGH-84). citation_indices are 1-based, matching the [1], [2], ...
    numbering AssistantContext.to_prompt_text() shows the model.
    """

    text: str
    citation_indices: list[int] = []


class Answer(BaseModel):
    answer: str
    claims: list[Claim] = []
    evidence_ids: list[str] = []
    uncertainty: Optional[str] = None
    intent: str
