"""Document classification (YGH-17).

Keyword-based classifier. This is a deliberate placeholder for an LLM-based
classifier: it's cheap, offline, and good enough to unblock the rest of the
pipeline. Swap it behind the same `classify(text) -> (label, confidence)`
signature when a real model is wired in.
"""


CLASSES = ["lab_report", "prescription", "discharge_summary", "doctor_note", "other"]

_KEYWORDS = {
    "lab_report": ["reference range", "specimen", "lab result", "hemoglobin", "cholesterol", "a1c"],
    "prescription": ["rx", "prescribed", "dosage", "refill", "pharmacy", "sig:"],
    "discharge_summary": ["discharge summary", "discharge date", "admitted", "hospital course"],
    "doctor_note": ["chief complaint", "assessment and plan", "progress note", "visit note"],
}


def classify(text: str) -> tuple[str, float]:
    lowered = text.lower()
    scores = {
        label: sum(1 for kw in keywords if kw in lowered)
        for label, keywords in _KEYWORDS.items()
    }
    best_label = max(scores, key=scores.get)
    best_score = scores[best_label]

    if best_score == 0:
        return "other", 0.3

    confidence = min(0.95, 0.5 + 0.1 * best_score)
    return best_label, round(confidence, 2)
