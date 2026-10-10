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
