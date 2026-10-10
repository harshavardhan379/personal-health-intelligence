"""Verification orchestrator (M12) and the answer verification gate (YGH-56).

generate_verified_answer() is what /assistant/ask now calls instead of calling
generate_answer() directly — every answer that reaches the user has already
been checked against its own cited evidence.
"""

import logging
import uuid

from sqlalchemy.orm import Session

from packages.schemas.answer import Answer
from packages.schemas.evidence import Evidence
from packages.schemas.verification import VerifiedAnswer
from services.reasoning.generate import generate_answer_with_context
from services.reasoning.intent_classifier import classify_intent
from services.safety.gate import evaluate_question, guard_output, refusal_answer
from services.verification.match import match_claim_to_evidence

logger = logging.getLogger("app.verification")


def verify_answer(answer: Answer, evidence: list[Evidence]) -> VerifiedAnswer:
    verifications = [match_claim_to_evidence(claim, evidence) for claim in answer.claims]

    fully_verified = all(v.label == "SUPPORTED" for v in verifications) if verifications else False

    # The gate (YGH-56): never let a CONTRADICTED or UNSUPPORTED claim reach the
    # user silently labeled as plain fact. We don't yet suppress specific
    # high-risk claims outright — that needs M13's safety classifier to know
    # what counts as "high-risk" in the first place. For M12's scope, every
    # non-SUPPORTED claim is surfaced explicitly in `uncertainty`, and the full
    # per-claim labels are always visible in `claim_verifications` — nothing is
    # hidden, but nothing unverified is presented as confirmed either.
    uncertainty = answer.uncertainty
    flagged = [v for v in verifications if v.label != "SUPPORTED"]
    if flagged:
        flag_text = "; ".join(f'"{v.claim_text}" ({v.label.lower()})' for v in flagged)
        gate_note = f"Not fully verified against the cited evidence: {flag_text}."
        uncertainty = f"{uncertainty} {gate_note}" if uncertainty else gate_note
        logger.warning("Answer verification gate flagged %d claim(s): %s", len(flagged), flag_text)

    return VerifiedAnswer(
        answer=answer.answer,
        claim_verifications=verifications,
        evidence_ids=answer.evidence_ids,
        uncertainty=uncertainty,
        intent=answer.intent,
        fully_verified=fully_verified,
    )


def generate_verified_answer(
    db: Session, user_id: uuid.UUID, question: str
) -> VerifiedAnswer:
    """The gated entry point — use this instead of generate_answer() wherever
    an answer is actually shown to a user (the API route).

    Order matters: safety check on the QUESTION first (a blocked question never
    reaches retrieval or the model), then generate, verify, and finally safety
    check on the model's OUTPUT.
    """
    decision = evaluate_question(question)
    intent = classify_intent(question)
    if decision.blocks_generation:
        return refusal_answer(decision, intent)

    answer, context = generate_answer_with_context(db, user_id, question)
    verified = verify_answer(answer, context.evidence)
    return guard_output(verified, decision, intent)
