"""Safe response policies (YGH-60): category -> action, and the user-facing text.

The mapping is a plain dict on purpose: it is the whole policy, readable in one
glance, and tests/safety/test_policy.py asserts every category is covered and no
dangerous category can ever map to ALLOW.

Wording rules for every message below (docs/02_SAFETY_SPEC.md section 6):
say plainly what the assistant will not do, say what it CAN do with the user's own
records, and point to a human (clinician, pharmacist, emergency services). No
message may itself diagnose, prescribe or direct treatment; the output guard in
output_guard.py is run over all of them in tests to prove it.
"""

from packages.schemas.safety import SafetyAction, SafetyCategory

POLICY: dict[SafetyCategory, SafetyAction] = {
    "SAFE_INFORMATIONAL": "ALLOW_WITH_UNCERTAINTY",
    "PERSONAL_HISTORY": "ALLOW",
    "HIGH_RISK_MEDICAL": "REDIRECT_TO_PROFESSIONAL",
    "DIAGNOSIS_REQUEST": "ABSTAIN",
    "PRESCRIPTION_REQUEST": "ABSTAIN",
    "TREATMENT_REQUEST": "REDIRECT_TO_PROFESSIONAL",
    "EMERGENCY_REQUEST": "REDIRECT_TO_PROFESSIONAL",
}

# Actions after which the question never reaches retrieval or the model.
BLOCKING_ACTIONS = ("CLARIFY", "ABSTAIN", "REDIRECT_TO_PROFESSIONAL")

SCOPE_NOTE = (
    "This assistant answers only from your own confirmed records. It does not give "
    "general medical information or advice."
)

REFUSAL_MESSAGES: dict[SafetyCategory, str] = {
    "EMERGENCY_REQUEST": (
        "This could be a medical emergency, and I cannot assess emergencies. "
        "Please contact your local emergency number now (for example 911 in the US, "
        "112 in the EU, 999 in the UK) or go to the nearest emergency department. "
        "If you are thinking about harming yourself, call or text 988 in the US, or "
        "contact your local crisis line or emergency services right away."
    ),
    "DIAGNOSIS_REQUEST": (
        "I cannot diagnose conditions or tell you whether you have one. I can show "
        "what your own confirmed records contain, such as lab values and their dates. "
        "A clinician can interpret your results and symptoms together."
    ),
    "PRESCRIPTION_REQUEST": (
        "I cannot recommend, prescribe or change medications or doses. I can show "
        "what your records say you were prescribed and when. Please ask your "
        "prescriber or pharmacist about your medication."
    ),
    "TREATMENT_REQUEST": (
        "I cannot advise on starting, stopping or changing treatment, or on whether "
        "to follow your clinician's advice. Please talk to your doctor or care team "
        "before making any change. I can show what your records contain."
    ),
    "HIGH_RISK_MEDICAL": (
        "How serious something is, and questions about risks, side effects or "
        "interactions, need a clinician who knows your full situation. I cannot judge "
        "that from your records. If you are worried, please contact your clinician, "
        "and seek urgent care for severe or sudden symptoms."
    ),
}

CLARIFY_MESSAGE = (
    "I could not tell what to look up in your records. Could you ask about something "
    "specific, such as a lab value, a medication or a date? If this is about a "
    "symptom or a health concern, please contact your clinician, and seek urgent "
    "care for severe or sudden symptoms."
)


def action_for(category: SafetyCategory) -> SafetyAction:
    return POLICY[category]


def message_for(category: SafetyCategory, action: SafetyAction) -> str:
    """User-facing text for a blocking action."""
    if action == "CLARIFY":
        return CLARIFY_MESSAGE
    return REFUSAL_MESSAGES[category]
