"""Output guard: checks what the model actually SAID, after generation.

The pre-generation classifier can miss a novel phrasing, and a model can ignore its
system prompt. This is the second, independent line of defence: it scans the text
about to be shown for the three things the product must never do on its own
(docs/02_SAFETY_SPEC.md section 2): diagnose, prescribe or direct treatment, plus
false reassurance. It is deliberately pattern-based and auditable, not another
model call (same reasoning as services/verification/match.py).

It errs toward blocking: a false positive costs one refused answer, a false
negative is the failure this whole milestone exists to prevent.
"""

import re
from dataclasses import dataclass
from typing import Optional

_CONDITIONS = (
    r"(type [12] )?(diabetes|prediabetes|pre-diabetes|cancer|a tumou?r|a tumor|"
    r"hypertension|high blood pressure|anemia|anaemia|an infection|heart "
    r"(disease|failure|attack)|a stroke|kidney (disease|failure)|liver "
    r"(disease|failure)|depression|anxiety|asthma|copd|pneumonia|sepsis|"
    r"[a-z]+itis|[a-z]+emia|[a-z]+osis|[a-z]+pathy)"
)
_ACTIONS = (
    r"stop|start|take|increase|decrease|reduce|double|halve|skip|discontinue|switch|"
    r"avoid|continue|ignore|use|try|begin|quit|lower|raise|change|cut"
)

# (rule name, pattern). The rule name is logged; the matched text never is.
_RULES = [
    (
        "asserts_diagnosis",
        re.compile(
            rf"\byou (likely |probably |possibly |may |might |could |definitely )?"
            rf"(have|has|suffer from|are suffering from|are diagnosed with|"
            rf"appear to have|seem to have) {_CONDITIONS}\b"
        ),
    ),
    (
        "asserts_diagnosis",
        re.compile(
            r"\b(your (condition|diagnosis) is|you are diagnosed|"
            r"this (indicates|means|suggests|confirms|is a sign of|is evidence of) "
            r"(that )?you\b)"
        ),
    ),
    (
        "directs_treatment",
        re.compile(rf"\byou (should|must|need to|ought to|have to|had better) ({_ACTIONS})\b"),
    ),
    (
        "directs_treatment",
        re.compile(r"\b(i|we) (would |'d )?(strongly )?(recommend|advise|suggest|urge)\b"),
    ),
    (
        "directs_treatment",
        re.compile(rf"(^|[.!?;:]\s+)({_ACTIONS})\b (taking|using|your|the|any|more|less|\d)"),
    ),
    (
        "directs_treatment",
        re.compile(r"\byour (dose|dosage) (should|needs to|must) (be|go)\b"),
    ),
    (
        "prescribes",
        re.compile(
            r"\b(take|taking|use|using) \d+(\.\d+)? ?(mg|mcg|g|ml|iu|units?)\b"
            r".{0,30}\b(daily|twice|once|every|per day|a day|at night|morning)\b"
        ),
    ),
    (
        "false_reassurance",
        re.compile(
            r"\b(nothing to worry about|no (need|reason) to (worry|see|visit|call|be concerned)|"
            r"(don'?t|do not) need (to see )?(a |your )?(doctor|clinician|medical)|"
            r"you('re| are) (fine|healthy|ok|okay|in the clear|not at risk)|"
            r"no cause for (concern|alarm)|it is (perfectly )?(safe|fine) to)\b"
        ),
    ),
]


@dataclass(frozen=True)
class OutputViolation:
    rule: str


def check_text(text: str) -> Optional[OutputViolation]:
    """Returns the first violated rule, or None if the text is clean."""
    lowered = text.lower().replace("’", "'")
    for rule, pattern in _RULES:
        if pattern.search(lowered):
            return OutputViolation(rule=rule)
    return None


def check_texts(texts: list) -> Optional[OutputViolation]:
    for text in texts:
        violation = check_text(text)
        if violation is not None:
            return violation
    return None
