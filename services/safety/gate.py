"""The safety gate (YGH-59, YGH-60): the single entry point the API pipeline uses.

Two checkpoints around the model, so safety is enforced by structure and not only
by the system prompt:

  1. evaluate_question()  BEFORE retrieval or generation. Dangerous requests are
                          refused with fixed, reviewed text. The question never
                          reaches the model, so it cannot be talked into anything.
  2. guard_output()       AFTER generation + verification. If the model's text
                          still diagnoses, prescribes, directs treatment or
                          falsely reassures, it is replaced by a redirect.
"""

import logging
import re
from dataclasses import dataclass

from packages.schemas.safety import SafetyAction, SafetyCategory
from packages.schemas.verification import VerifiedAnswer
from services.safety.classifier import UNRECOGNIZED_PERSONAL, classify_safety
from services.safety.output_guard import check_texts
from services.safety.policy import (
    BLOCKING_ACTIONS,
    REFUSAL_MESSAGES,
    SCOPE_NOTE,
    action_for,
    message_for,
)

logger = logging.getLogger("app.safety")

_MIN_LETTERS = 3  # fewer than this and there is no question to answer


@dataclass(frozen=True)
class SafetyDecision:
    category: SafetyCategory
    action: SafetyAction
    rule: str

    @property
    def blocks_generation(self) -> bool:
        return self.action in BLOCKING_ACTIONS


def evaluate_question(question: str) -> SafetyDecision:
    if len(re.findall(r"[a-zA-Z]", question)) < _MIN_LETTERS:
        decision = SafetyDecision("SAFE_INFORMATIONAL", "CLARIFY", "too_short")
    else:
        result = classify_safety(question)
        if result.rule == UNRECOGNIZED_PERSONAL:
            # Not recognisably a records lookup and not a known dangerous request:
            # fail closed and ask the user to rephrase, rather than let the model guess.
            decision = SafetyDecision(result.category, "CLARIFY", result.rule)
        else:
            decision = SafetyDecision(result.category, action_for(result.category), result.rule)

    # Category, action and rule name only. The question is health data: never logged.
    logger.info(
        "safety_decision category=%s action=%s rule=%s",
        decision.category, decision.action, decision.rule,
    )
    return decision


def refusal_answer(decision: SafetyDecision, intent: str) -> VerifiedAnswer:
    """The fixed response for a blocked question. No retrieval, no model, no claims."""
    return VerifiedAnswer(
        answer=message_for(decision.category, decision.action),
        claim_verifications=[],
        evidence_ids=[],
        uncertainty=None,
        intent=intent,
        fully_verified=False,
        safety_category=decision.category,
        safety_action=decision.action,
    )


def guard_output(
    verified: VerifiedAnswer, decision: SafetyDecision, intent: str
) -> VerifiedAnswer:
    """Final check on an answer the model produced for an allowed question."""
    texts = [verified.answer] + [v.claim_text for v in verified.claim_verifications]
    violation = check_texts(texts)

    if violation is not None:
        logger.warning(
            "safety output guard blocked an answer rule=%s category=%s",
            violation.rule, decision.category,
        )
        # The model said something the product must never say. Replace the whole
        # answer, and drop its claims and evidence rather than show any of it.
        category = (
            "DIAGNOSIS_REQUEST" if violation.rule == "asserts_diagnosis" else "TREATMENT_REQUEST"
        )
        blocked = SafetyDecision(category, action_for(category), f"output_guard:{violation.rule}")
        return refusal_answer(blocked, intent)

    verified.safety_category = decision.category
    verified.safety_action = decision.action
    if decision.action == "ALLOW_WITH_UNCERTAINTY":
        verified.uncertainty = (
            f"{verified.uncertainty} {SCOPE_NOTE}" if verified.uncertainty else SCOPE_NOTE
        )
    return verified


__all__ = [
    "REFUSAL_MESSAGES",
    "SafetyDecision",
    "evaluate_question",
    "guard_output",
    "refusal_answer",
]
