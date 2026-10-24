"""Evidence verification shapes (M12). Each claim in an Answer (YGH-84) gets
checked against the evidence it cited and labeled accordingly.
"""

from typing import Literal, Optional

from pydantic import BaseModel

# SUPPORTED: the claim's text matches what its cited evidence actually says.
# UNSUPPORTED: cited evidence exists but doesn't actually back up the claim.
# CONTRADICTED: cited evidence directly conflicts with the claim (YGH-55).
# INSUFFICIENT: the claim cited no valid evidence at all.
VerificationLabel = Literal["SUPPORTED", "UNSUPPORTED", "CONTRADICTED", "INSUFFICIENT"]


class ClaimVerification(BaseModel):
    claim_text: str
    label: VerificationLabel
    citation_indices: list[int]
    explanation: Optional[str] = None


class SourceEvidence(BaseModel):
    """One piece of evidence an answer was generated from, as the user is shown it.
    `number` is what a claim's `citation_indices` and the answer's [n] markers refer to."""

    number: int
    text: str  # the source quote the fact was extracted from
    document_id: str
    filename: Optional[str] = None  # None if the document is not one of the user's
    page: int
    event_ids: list[str] = []
    source: str


class VerifiedAnswer(BaseModel):
    answer: str
    claim_verifications: list[ClaimVerification]
    evidence_ids: list[str]
    uncertainty: Optional[str] = None
    intent: str
    # True only if every claim is SUPPORTED — the gate YGH-56 will act on.
    fully_verified: bool
    # How the safety layer (M13) routed the question; see packages/schemas/safety.py.
    safety_category: Optional[str] = None
    safety_action: Optional[str] = None
    # The numbered evidence the answer was generated from (YGH-122). Empty whenever the safety
    # layer blocked or replaced the answer: nothing behind a blocked answer is shown.
    evidence: list[SourceEvidence] = []

