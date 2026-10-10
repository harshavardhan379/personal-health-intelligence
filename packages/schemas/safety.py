"""Safety taxonomy and response actions (YGH-58, YGH-60).

Every question is classified into exactly one SafetyCategory BEFORE any retrieval
or model call, and each category maps to exactly one SafetyAction (see
services/safety/policy.py). The definitions below are the contract; the full
rationale, examples and escalation language live in docs/02_SAFETY_SPEC.md.
"""

from typing import Literal

# SAFE_INFORMATIONAL   General, non-personal question ("What does HbA1c measure?").
#                      Not the product's job: answered only from the user's own
#                      records, with a note saying so.
# PERSONAL_HISTORY     Asks what the user's OWN records contain or how they changed
#                      ("Is my HbA1c improving?"). The core use case.
# HIGH_RISK_MEDICAL    Asks how serious / dangerous / risky something is, or about
#                      side effects and interactions. Needs a clinician's judgment.
# DIAGNOSIS_REQUEST    Asks whether the user has a condition, or what is wrong.
# PRESCRIPTION_REQUEST Asks what medication or dose to take, or to change a dose.
# TREATMENT_REQUEST    Asks whether to stop/skip/change treatment, how to treat or
#                      cure something, or whether to disregard a clinician.
# EMERGENCY_REQUEST    Describes a possible emergency happening now, or self-harm.
SafetyCategory = Literal[
    "SAFE_INFORMATIONAL",
    "PERSONAL_HISTORY",
    "HIGH_RISK_MEDICAL",
    "DIAGNOSIS_REQUEST",
    "PRESCRIPTION_REQUEST",
    "TREATMENT_REQUEST",
    "EMERGENCY_REQUEST",
]

# ALLOW                    Answer normally from the user's records.
# ALLOW_WITH_UNCERTAINTY   Answer from the records, with an explicit scope note.
# CLARIFY                  Ask the user to rephrase; nothing is retrieved or generated.
# ABSTAIN                  Decline, say what the system can and cannot do.
# REDIRECT_TO_PROFESSIONAL Decline and point to a clinician or emergency services.
SafetyAction = Literal[
    "ALLOW",
    "ALLOW_WITH_UNCERTAINTY",
    "CLARIFY",
    "ABSTAIN",
    "REDIRECT_TO_PROFESSIONAL",
]

ALL_CATEGORIES = (
    "SAFE_INFORMATIONAL",
    "PERSONAL_HISTORY",
    "HIGH_RISK_MEDICAL",
    "DIAGNOSIS_REQUEST",
    "PRESCRIPTION_REQUEST",
    "TREATMENT_REQUEST",
    "EMERGENCY_REQUEST",
)
