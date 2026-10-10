"""Safety classifier (YGH-59).

Rule-based, same philosophy as services/reasoning/intent_classifier.py and the
extractors: a deliberate, offline, auditable placeholder for a trained classifier,
behind a signature (`classify_safety(question) -> SafetyClassification`) that will
not need to change when a model replaces it. A rule-based safety gate is also
predictable: the same question always gets the same category, and every decision
names the rule that made it.

Categories are checked from MOST to LEAST dangerous, and the first match wins, so
a question with several signals ("My chest hurts, should I stop my medicine?") is
routed by its most dangerous part. Anything that matches nothing dangerous falls
through to PERSONAL_HISTORY (it talks about "my ...") or SAFE_INFORMATIONAL.

Known limitation: keyword rules miss novel phrasings. services/safety/output_guard.py
is the second line of defence (it checks what the model actually says), and
tests/safety/ is the regression suite that keeps both honest.
"""

import re
from dataclasses import dataclass

from packages.schemas.safety import SafetyCategory

# Words that mean the question is about the user's RECORDS, not about a condition
# ("Do I have any records of headaches?" is not a diagnosis request).
_RECORDS_WORDS = (
    r"records?|reports?|documents?|results?|appointments?|visits?|prescriptions?|"
    r"medications?|medicines?|labs?|tests?|history|data|files?|uploads?|entry|"
    r"entries|notes?|scans?|measurements?|readings?|values?"
)

# --- EMERGENCY ------------------------------------------------------------
# Always an emergency, whatever the framing.
_EMERGENCY_ALWAYS = re.compile(
    r"\b(suicid\w*|kill myself|end(ing)? my (own )?life|want to die|self[- ]?harm|"
    r"hurt myself|overdos\w*|took too (many|much)|not breathing|stopped breathing|"
    r"unconscious|call (911|999|112|an ambulance)|"
    r"need an ambulance|medical emergency|this is an emergency|choking|poison(ed|ing)?|"
    r"is [^?]{0,20}an emergency(?! (room|contact|department|visit))|"
    r"(accidentally|by mistake) (took|swallowed|injected)|(took|taken|injected) "
    r"(double|extra|two) (of )?(my|the) (usual )?(dose|pills?|tablets?|insulin)|"
    r"can'?t go on|better off dead|no reason to live|panic attack|heart (is )?racing|"
    r"swallowed (a whole|too many|a bottle|all|my whole)|"
    r"go to the (er|emergency room|emergency department|hospital|a&e))\b"
)
# Emergency symptoms. Skipped when the question is clearly about the PAST
# ("When did I first report chest pain?") unless it also says it is happening now.
_EMERGENCY_SYMPTOM = re.compile(
    r"\b(heart attack|stroke|cardiac arrest|chest pain|chest tightness|crushing chest|"
    r"can'?t breathe|cannot breathe|trouble breathing|difficulty breathing|"
    r"short(ness)? of breath|severe bleeding|bleeding (a lot|heavily|badly)|"
    r"vomiting blood|coughing (up )?blood|seizure|anaphyla\w*|passing out|fainting|"
    r"face (is )?droop\w*|slurred speech|speech is slurred|"
    r"can'?t feel my (arm|leg|face|hand)|(arm|leg|face) (is |feels )?numb|"
    r"chest (hurts|is hurting|is crushing|feels (tight|heavy))|crushing (me|pain|chest)|"
    r"(might|about to|going to) (pass out|faint|collapse)|feel faint)\b"
)
_EMERGENCY_NOW = re.compile(
    r"\b(am i having|i am having|i'?m having|im having|i think i'?m having|"
    r"right now|at the moment|currently having|i am experiencing|i'?m experiencing|"
    r"happening now|just started|suddenly)\b"
)
_HISTORY_CUE = re.compile(
    r"\b(when did|did i ever|have i ever|history of|was it|were there|"
    r"in my (records|reports|documents|history|notes)|documented|reported|"
    r"last (year|month|week)|previous(ly)?|ever had|first time|"
    # General or risk questions about a symptom are not "it is happening now":
    r"risk of|chance of|likelihood of|odds of|prevent\w*|signs? of|symptoms? of|"
    r"causes? of|what (is|are|causes))\b"
)

# --- PRESCRIPTION ---------------------------------------------------------
_PRESCRIPTION = re.compile(
    r"\b(what (dose|dosage)\b.*\b(should|can|could|do)|"
    r"(dose|dosage)\b.*\bshould i\b|"
    r"(dose|dosage)\b.*\b(right|best|safe|correct|appropriate|suitable) for me|"
    r"how (much|many)\b.*\bshould i (take|use)|"
    r"how (much|many)\b.*\bdo i need to take|"
    r"how often should i take|"
    r"how (much|many) .{0,30}\bdo i (inject|take|use|need|give)|"
    r"(double|doubling) up on|"
    r"is it (fine|ok|okay|safe) to (double|take more|add|increase|combine)|"
    r"(tell me |what |how much |how many )(dose|dosage|mg|milligrams|pills|tablets)\b.*"
    r"\b(to take|should|be taking|i take)|"
    r"should i (increase|decrease|double|raise|lower|reduce|up|halve)\b.*\b"
    r"(dose|dosage|medication|medicine|pills?)|"
    r"should i take (more|less|extra|double)|can i take (more|extra|double)|"
    r"(can|could|will) you prescribe|prescribe (me|something)|"
    r"what (medication|medicine|drug|pill|supplement)s? (should|can|could) i (take|use|try|start)|"
    r"should i (start|begin|take)\b.*\b"
    r"(medication|medicine|drug|pills?|metformin|insulin|statins?)|"
    r"recommend (a|some|any) (medication|medicine|drug|dose|dosage))\b"
)

# --- TREATMENT ------------------------------------------------------------
_TREATMENT = re.compile(
    r"\b(should i (stop|quit|discontinue|skip|switch|change|continue|keep taking|"
    r"ignore|avoid|go off|come off|follow|trust|listen to)\b|"
    r"(ignore|disregard|don'?t (need|trust|listen to)|instead of) (my |the )?"
    r"(doctor|physician|clinician|gp|nurse|specialist|medical advice|advice)|"
    r"(can|could|may) i (just )?(stop|skip|quit|miss)\b|"
    r"(i want to|i'?m going to|i will|i plan to|is it (ok|okay|fine|safe) to) "
    r"(stop|skip|quit)\b|"
    r"how (do|can|should|would) i (treat|cure|fix|heal)\b|"
    r"(how to|ways? to|tell me how to) (treat|cure|fix|heal|manage|lower|reduce)\b|"
    r"tell me (if|whether) i should|get rid of my|"
    r"would (stopping|skipping|quitting|changing|missing) .{0,40}(hurt|harm|help|affect)|"
    r"(should|can|could|will) my (doctor|prescriber|gp|specialist) "
    r"(change|adjust|stop|increase|switch|reduce)|"
    r"how (do|can|should) i (lower|reduce|control|bring down|improve)\b.*\b"
    r"(hba1c|a1c|blood sugar|glucose|cholesterol|blood pressure)|"
    r"what (treatment|therapy|surgery|procedure)s?\b|"
    r"(natural|home) (remed(y|ies)|cure)|cure (for|my)|"
    r"should i (have|get|see about) (surgery|an operation|chemo\w*|dialysis))\b"
)

# --- DIAGNOSIS ------------------------------------------------------------
_DO_I_HAVE = re.compile(r"\bdo i (have|got|suffer from|show signs of)\b")
_DIAGNOSIS = re.compile(
    r"\b(am i (diabetic|pre-?diabetic|sick|ill|dying|anemic|anaemic|depressed|obese|"
    r"overweight|unhealthy|at risk|going to (die|get))|"
    r"(diagnos(e|is|ed)|what('?s| is) wrong with me|what (condition|disease|illness|disorder) "
    r"(do i have|is this|am i)|what do i have)|"
    r"is (it|this|that) (cancer|diabetes|a tumou?r|a tumor|serious|malignant|benign)|"
    r"(could|might) (it|this) be\b|(could|might) i have\b|"
    r"could .{0,30} be (a |an )?(brain )?(tumou?r|cancer|diabetes|infection|disease|clot)|"
    r"(do you think|you think) i (have|am|might)|please diagnose|"
    r"is this a (heart|lung|kidney|liver|brain|serious|medical)? ?"
    r"(problem|condition|issue|disease)|"
    r"(my )?(tumou?r|cancer|lesion|mass|lump|growth) (is )?"
    r"(growing|spreading|getting (bigger|worse))|"
    r"(do|does) (this|that|it|these|my \w+|my results) mean (i have|i'?m|i am)|"
    r"(sign|symptom|indicator|indication) of (cancer|diabetes|disease|a condition)|"
    r"do i have (cancer|diabetes|a tumou?r|a tumor|an infection|a disease))\b"
)

# --- HIGH RISK ------------------------------------------------------------
_HIGH_RISK = re.compile(
    r"\b(dangerous|life[- ]?threatening|critical(ly)?|serious(ly)?|"
    r"should i (be )?(worried|concerned|scared|alarmed)|"
    r"(am|is|are) .{0,20}\b(worried|concerned)\b|"
    r"side[- ]?effects?|interact\w*|contraindicat\w*|"
    r"is it safe (to|for)|safe to (take|combine|mix|use|drink)|"
    r"risk (of|for)|prognosis|how long (do|will) i (have|live)|"
    r"will i (die|recover|get better|be ok(ay)?)|pregnan\w*|breastfeed\w*|allerg\w*|"
    r"is [^?]{0,25}\b(bad|worrying|concerning|alarming|a problem)|"
    r"(am i going to|will i|are we going to) (need|have to|get|develop|die|lose|end up)|"
    r"(stronger|safer) (than|,)|which (pill|drug|medicine|medication)s? "
    r"(is|are) (stronger|better|safer|best)|"
    r"safe for me|(drink|alcohol) .{0,15}\bon\b)\b"
)


UNRECOGNIZED_PERSONAL = "unrecognized_personal"

# First-person wording, or wording that is about documents ("When was X first
# documented?"), marks a question as being about the user's own records.
_PERSONAL_CUE = re.compile(
    r"\b(my|i|me|mine|i'?ve|i'?m|documented|recorded|reported|uploaded|prescribed)\b"
)


# Catch-all for self-directed ADVICE wording that no specific rule above caught
# ("Do I need insulin?", "What is the best medicine for me?"). A records question
# does not ask what the user should do, so this is routed to a clinician instead of
# the model. This is what stops an unrecognised phrasing from defaulting to "allowed".
_ADVICE = re.compile(
    r"\b(should i(?! (upload|add|delete|export|share|scan))|"
    r"(can|could) i(?! (see|view|get|download|upload|find|export|delete|share|access|have|ask))|"
    r"may i(?! (have|see|view|get|ask))|"
    r"do i need( to)?|am i supposed to|what (should|can|could) i do|what do i do|"
    r"what('?s| is| are) the best|best (medicine|medication|treatment|drug|way|option|diet)|"
    r"which (medicine|medication|drug|treatment) (is|would be) (best|better)|"
    r"what should my|do you think|in your opinion|would you (recommend|advise|suggest)|"
    r"(is|are) (the )?(best|better|recommended|ideal|optimal|safest)|best (for|to)|"
    r"will (this|it|that|these|they) |"
    r"tell me (what|how|which) (to|i should)|give me (advice|a recommendation|a diagnosis))\b"
)


# A first-person statement about how the user feels right now is a clinical
# situation, not a records question ("I feel dizzy, what now?").
_SYMPTOM_STATEMENT = re.compile(
    r"\b(i feel|i'?m feeling|i am feeling|i have been feeling|i'?ve been feeling|"
    r"i keep (getting|having)|feel(ing)? (dizzy|sick|faint|weak|nauseous|numb|unwell)|"
    r"what now|feel)\b"
)


# FAIL-CLOSED allowlist. A question about the user is answered only if it positively
# looks like a LOOKUP in their records: a retrieval frame ("what/when/show/did...")
# plus a records or trend cue ("dose", "report", "improving", "latest"...). Keyword
# deny-lists alone do not generalise (measured: a rule set tuned on one batch of
# unsafe questions missed 16 of 20 in the next batch), so unrecognised wording is
# not trusted by default.
_LOOKUP_FRAME = re.compile(
    r"^(what|which|when|who|where|how many|list|show|display|compare|summari[sz]e|"
    r"give me|tell me about|did|has|have|was|were|is|are|find|count|"
    r"do i have (any|a|an)|can you (show|list|summari[sz]e|compare))\b"
)
_RECORD_CUE = re.compile(
    rf"\b({_RECORDS_WORDS}|improv\w+|worsen\w+|increas\w+|decreas\w+|trend\w*|"
    r"chang\w+|updated?|latest|last|earliest|first|previous|recent\w*|since|between|"
    r"over time|timeline|documented|recorded|reported|uploaded|prescribed|dose|doses|"
    r"level|unit|range|date|page|encounters?|clinic|provider|doctor|events?|confirmed|"
    r"pending|normal|abnormal|above|below|within|higher|lower|gone (up|down)|"
    r"go(es)? (up|down)|risen|dropped|rose|fell)\b"
)
# Medical terms count as a records cue only when the question is also clearly
# about the user ("What is my cholesterol level?"), never on their own
# ("What is cholesterol?" is a general question).
_MEDICAL_TERM = re.compile(r"\b(symptoms?|medications?|medicines?|hba1c|a1c|cholesterol)\b")


@dataclass(frozen=True)
class SafetyClassification:
    category: SafetyCategory
    # Name of the rule that produced the category, for logs and tests. Never the
    # question text itself: questions are health data and must not be logged.
    rule: str


def _normalize(question: str) -> str:
    text = question.lower().replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", text).strip()


def classify_safety(question: str) -> SafetyClassification:
    q = _normalize(question)

    if _EMERGENCY_ALWAYS.search(q):
        return SafetyClassification("EMERGENCY_REQUEST", "emergency_always")
    if _EMERGENCY_SYMPTOM.search(q):
        about_the_past = _HISTORY_CUE.search(q) and not _EMERGENCY_NOW.search(q)
        if not about_the_past:
            return SafetyClassification("EMERGENCY_REQUEST", "emergency_symptom")

    if _PRESCRIPTION.search(q):
        return SafetyClassification("PRESCRIPTION_REQUEST", "prescription")
    if _TREATMENT.search(q):
        return SafetyClassification("TREATMENT_REQUEST", "treatment")

    if _DIAGNOSIS.search(q):
        return SafetyClassification("DIAGNOSIS_REQUEST", "diagnosis")
    if _DO_I_HAVE.search(q) and not re.search(rf"\b({_RECORDS_WORDS})\b", q):
        return SafetyClassification("DIAGNOSIS_REQUEST", "do_i_have")

    if _HIGH_RISK.search(q):
        return SafetyClassification("HIGH_RISK_MEDICAL", "high_risk")
    if _ADVICE.search(q):
        return SafetyClassification("HIGH_RISK_MEDICAL", "advice_seeking")
    if _SYMPTOM_STATEMENT.search(q) and not _HISTORY_CUE.search(q):
        return SafetyClassification("HIGH_RISK_MEDICAL", "symptom_statement")

    if _LOOKUP_FRAME.search(q) and (
        _RECORD_CUE.search(q) or (_MEDICAL_TERM.search(q) and _PERSONAL_CUE.search(q))
    ):
        return SafetyClassification("PERSONAL_HISTORY", "records_lookup")
    if _PERSONAL_CUE.search(q):
        # About the user, but not recognisably a records lookup: do not guess.
        # services/safety/gate.py turns this rule into a CLARIFY response.
        return SafetyClassification("PERSONAL_HISTORY", UNRECOGNIZED_PERSONAL)
    return SafetyClassification("SAFE_INFORMATIONAL", "informational_default")
