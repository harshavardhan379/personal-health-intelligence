"""QA scoring (framework section 6).

`score_answer` scores ONE answer against ONE question record and the patient's ground truth
and returns COUNTS (they add with `+`, so a split is pooled before it is divided).

Principle: no system grades itself. Everything here uses only the question record, the
patient's ground-truth events and the text of the answer. It never calls services/verification
or services/safety (tests enforce that no scorer imports them).

What is scored
  answerable questions    strict (every expected fact present, no forbidden fact, not
                          abstained), partial credit (fraction of expected facts present;
                          0 if a forbidden fact appears or the system abstained), and
                          over-abstention (abstained on a question the records can answer)
  unanswerable questions  abstention (the explicit `abstained` flag is true AND no forbidden
                          fact is stated), and the confident-wrong rate (it answered)
  evidence                precision / recall of the event ids the answer cites against the gold
                          ids, ONLY for systems that can cite events (`cites_events`); a
                          baseline that returns raw text scores "not applicable", not 0
  citation correctness    a claim that cites evidence is correct if everything it cites is gold
                          AND every number and date in the claim agrees with what it cites
  unsupported claims      a claim stating a number, date or medication that appears nowhere in
                          the patient's ground truth (numbers and dates the QUESTION itself
                          contained are exempt); for a system that labels its own claims, how
                          many of the really-unsupported ones it flagged (verifier catch rate)

Known heuristics (framework section 9): numbers are found by pattern, so a derived figure
the records never print (a percentage change) can be flagged unsupported; small whole
numbers up to ten (counts such as "2 reports") are always allowed. The difference between two
measurements of the same thing (two HbA1c values, two Metformin doses) is derivable and is
treated as supported (metric definitions 0.4).
"""

import re
from dataclasses import dataclass
from typing import Optional

from evaluation.catalogs import MEDS, canonical_name
from evaluation.facts import Fact, fact_matches, find_dates, strip_dates
from evaluation.model import KEY_FIELD
from evaluation.stats import Mean, Rate

_NUMBER = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)(?!\d)")
_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")
SMALL_COUNTS = {float(n) for n in range(0, 11)}


def numbers_in(text: str) -> set:
    """The numbers a text states, ignoring calendar dates and digits inside words like
    HbA1c or 1000mg's leading letters (a number may be followed by a unit)."""
    return {round(float(m), 6) for m in _NUMBER.findall(strip_dates(text))}


def claims_of(answer) -> list:
    """The atomic claims: the system's own, else the answer split into sentences."""
    if answer.claims:
        return [c.text for c in answer.claims]
    return [piece.strip() for piece in _SENTENCES.split(answer.answer) if piece.strip()]


def _event_numbers(event) -> set:
    out = set()
    for key, value in event.data.items():
        if key in ("stay_id",):
            continue
        out |= numbers_in(str(value))
    return out


_MEASURED_FIELD = {"lab": "value", "medication": "dose"}


def _measured_differences(events: list) -> set:
    """The differences between two measurements of the same thing: two values of one lab, two
    doses of one medication. A claim such as 'HbA1c fell by 1.9' is derivable from the records
    even though 1.9 is printed nowhere in them. Different quantities never combine."""
    groups: dict = {}
    for event in events:
        field = _MEASURED_FIELD.get(event.event_type)
        if field is None or event.data.get(field) is None:
            continue
        found = numbers_in(str(event.data[field]))
        if len(found) == 1:
            key = (event.event_type, canonical_name(str(event.data[KEY_FIELD[event.event_type]])))
            groups.setdefault(key, []).append(next(iter(found)))
    return {round(abs(a - b), 6) for values in groups.values()
            for i, a in enumerate(values) for b in values[i + 1:]}


def _day_differences(events: list) -> set:
    days = sorted({e.event_date for e in events if e.event_date and e.date_precision == "day"})
    return {float((b - a).days) for i, a in enumerate(days) for b in days[i + 1:]}


def _event_dates(events: list) -> set:
    return {e.event_date for e in events if e.event_date and e.date_precision == "day"}


def _mentions(text: str, name: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text, re.IGNORECASE) is not None


def _medications_mentioned(text: str) -> set:
    found = set()
    for med in MEDS.values():
        if any(_mentions(text, n) for n in (med.name, *med.aliases)):
            found.add(canonical_name(med.name))
    return found


def _claim_supported(claim: str, patient_events: list, question_text: str) -> bool:
    """True unless the claim states a number, date or medication that is nowhere in the
    patient's ground truth (and was not in the question)."""
    allowed = set(SMALL_COUNTS) | _day_differences(patient_events)
    allowed |= _measured_differences(patient_events)
    for event in patient_events:
        allowed |= _event_numbers(event)
    allowed |= numbers_in(question_text)
    if not numbers_in(claim) <= allowed:
        return False
    known_dates = _event_dates(patient_events) | set(find_dates(question_text))
    if not set(find_dates(claim)) <= known_dates:
        return False
    patient_meds = {canonical_name(str(e.data[KEY_FIELD["medication"]]))
                    for e in patient_events if e.event_type == "medication"}
    mentioned = _medications_mentioned(claim) - _medications_mentioned(question_text)
    return mentioned <= patient_meds


def _claim_matches_cited(claim: str, cited_events: list) -> bool:
    """Every number and date in the claim agrees with the events it cites."""
    allowed = _day_differences(cited_events) | _measured_differences(cited_events)
    allowed |= {float(k) for k in range(len(cited_events) + 1)}  # a count of the cited items
    for event in cited_events:
        allowed |= _event_numbers(event)
    if not numbers_in(claim) <= allowed:
        return False
    return set(find_dates(claim)) <= _event_dates(cited_events)


@dataclass
class AnswerCounts:
    answerable: int = 0
    strict_correct: int = 0
    partial: Mean = None  # type: ignore[assignment]
    over_abstained: int = 0
    unanswerable: int = 0
    abstention_correct: int = 0
    answered_unanswerable: int = 0
    evidence_questions: int = 0  # questions where precision/recall apply
    cited_total: int = 0
    cited_gold: int = 0
    gold_total: int = 0
    precision_questions: int = 0  # evidence questions where something was cited
    citation_required: int = 0
    citation_met: int = 0
    citing_claims: int = 0
    correct_citing_claims: int = 0
    claims_total: int = 0
    unsupported_claims: int = 0
    labelled_claims: int = 0
    labelled_unsupported: int = 0  # really unsupported, among the claims that carry a label
    flagged_claims: int = 0
    unsupported_and_flagged: int = 0

    def __post_init__(self) -> None:
        if self.partial is None:
            self.partial = Mean(0.0, 0.0, 0)

    def __add__(self, other: "AnswerCounts") -> "AnswerCounts":
        merged = AnswerCounts(partial=self.partial + other.partial)
        for name in self.__dataclass_fields__:
            if name != "partial":
                setattr(merged, name, getattr(self, name) + getattr(other, name))
        return merged


def score_answer(answer, question: dict, truth_events: list, cites_events: bool = True,
                 ) -> AnswerCounts:
    expected = [Fact.from_dict(f) for f in question["expected_facts"]]
    forbidden = [Fact.from_dict(f) for f in question["forbidden_facts"]]
    gold = set(question["gold_evidence_ids"])
    text = answer.answer
    counts = AnswerCounts()

    forbidden_hit = any(fact_matches(f, text) for f in forbidden)
    if question["answerable"]:
        counts.answerable = 1
        found = sum(1 for f in expected if fact_matches(f, text))
        usable = not answer.abstained and not forbidden_hit
        counts.strict_correct = int(usable and found == len(expected))
        counts.partial = Mean.of([found / len(expected) if usable else 0.0])
        counts.over_abstained = int(answer.abstained)
    else:
        counts.unanswerable = 1
        counts.abstention_correct = int(answer.abstained and not forbidden_hit)
        counts.answered_unanswerable = int(not answer.abstained)

    cited = {i for claim in answer.claims for i in claim.evidence_ids}
    if cites_events and gold:
        counts.evidence_questions = 1
        counts.gold_total = len(gold)
        counts.cited_total = len(cited)
        counts.cited_gold = len(cited & gold)
        counts.precision_questions = int(bool(cited))
    if question.get("requires_citation"):
        counts.citation_required = 1
        counts.citation_met = int(bool(cited & gold))

    by_id = {e.event_id: e for e in truth_events}
    for claim in answer.claims:
        if not claim.evidence_ids:
            continue
        counts.citing_claims += 1
        cited_events = [by_id[i] for i in claim.evidence_ids if i in by_id]
        everything_gold = set(claim.evidence_ids) <= gold
        counts.correct_citing_claims += int(
            everything_gold and len(cited_events) == len(claim.evidence_ids)
            and _claim_matches_cited(claim.text, cited_events))

    claim_texts = claims_of(answer)
    unsupported = [not _claim_supported(t, truth_events, question["text"]) for t in claim_texts]
    counts.claims_total = len(claim_texts)
    counts.unsupported_claims = sum(unsupported)
    labels: Optional[list] = answer.claim_labels
    if labels is not None and len(labels) == len(claim_texts):
        flagged = [label != "SUPPORTED" for label in labels]
        counts.labelled_claims = len(claim_texts)
        counts.labelled_unsupported = sum(unsupported)
        counts.flagged_claims = sum(flagged)
        counts.unsupported_and_flagged = sum(u and f for u, f in zip(unsupported, flagged))
    return counts


def summarize_qa(c: AnswerCounts) -> dict:
    return {
        "answerable": c.answerable,
        "unanswerable": c.unanswerable,
        "strict_correctness": Rate(c.strict_correct, c.answerable),
        "partial_credit": c.partial,
        "over_abstention_rate": Rate(c.over_abstained, c.answerable),
        "abstention_correct": Rate(c.abstention_correct, c.unanswerable),
        "confident_wrong_rate": Rate(c.answered_unanswerable, c.unanswerable),
        "evidence_precision": Rate(c.cited_gold, c.cited_total),
        "evidence_recall": Rate(c.cited_gold, c.gold_total),
        "citation_required_met": Rate(c.citation_met, c.citation_required),
        "citation_correctness": Rate(c.correct_citing_claims, c.citing_claims),
        "unsupported_claim_rate": Rate(c.unsupported_claims, c.claims_total),
        # How well a system's own claim labels agree with the ground truth. Undefined (n=0) for
        # a system that does not label its claims.
        "verifier_catch_rate": Rate(c.unsupported_and_flagged, c.labelled_unsupported),
        "verifier_false_alarm_rate": Rate(
            c.flagged_claims - c.unsupported_and_flagged,
            c.labelled_claims - c.labelled_unsupported),
    }

