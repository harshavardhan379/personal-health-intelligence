"""Extraction scoring (framework section 3).

`score_extraction` is the per-patient scorer and returns COUNTS. Counts add (`+`), so a split
is scored by summing its patients' counts and only then dividing: that is the micro-average
(3.2), where a patient with many events weighs more than one with few.

`summarize_extraction` turns summed counts into rates with intervals. F1 has no interval: it
is not a proportion.
"""

from dataclasses import dataclass, field
from typing import Optional

from evaluation.catalogs import LABS, canonical_name
from evaluation.matching import dates_agree, key_of, match_events
from evaluation.model import EVENT_TYPES
from evaluation.stats import Rate, f1


@dataclass
class TypeCounts:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def __add__(self, other: "TypeCounts") -> "TypeCounts":
        return TypeCounts(self.tp + other.tp, self.fp + other.fp, self.fn + other.fn)


@dataclass
class ExtractionCounts:
    by_type: dict = field(default_factory=lambda: {t: TypeCounts() for t in EVENT_TYPES})
    numeric_total: int = 0  # matched labs
    numeric_correct: int = 0
    unit_total: int = 0  # matched labs
    unit_correct: int = 0
    dose_total: int = 0  # matched medications
    dose_correct: int = 0
    date_recall_total: int = 0  # dated truth events
    date_recall_found: int = 0
    undated_total: int = 0  # undated truth events
    undated_matched: int = 0
    undated_invented: int = 0  # matched undated events whose extraction carries a date

    def __add__(self, other: "ExtractionCounts") -> "ExtractionCounts":
        merged = ExtractionCounts(
            by_type={t: self.by_type[t] + other.by_type[t] for t in EVENT_TYPES})
        for name in ("numeric_total", "numeric_correct", "unit_total", "unit_correct",
                     "dose_total", "dose_correct", "date_recall_total", "date_recall_found",
                     "undated_total", "undated_matched", "undated_invented"):
            setattr(merged, name, getattr(self, name) + getattr(other, name))
        return merged

    @property
    def tp(self) -> int:
        return sum(c.tp for c in self.by_type.values())

    @property
    def fp(self) -> int:
        return sum(c.fp for c in self.by_type.values())

    @property
    def fn(self) -> int:
        return sum(c.fn for c in self.by_type.values())


def _same_number(truth_value, predicted_value, decimals: int) -> bool:
    try:
        return round(float(truth_value), decimals) == round(float(predicted_value), decimals)
    except (TypeError, ValueError):
        return False


def _norm(text) -> str:
    return "".join(str(text).split()).lower()


def score_extraction(predicted: list, truth: list) -> ExtractionCounts:
    """Counts for one patient. `predicted` are what a pipeline extracted, `truth` the
    ground-truth events (anything with event_type, event_date, date_precision, data)."""
    counts = ExtractionCounts()
    pairs, missed, spurious = match_events(truth, predicted)

    for t, p in pairs:
        counts.by_type[t.event_type].tp += 1
        if t.event_type == "lab":
            counts.numeric_total += 1
            counts.numeric_correct += _same_number(
                t.data.get("value"), p.data.get("value"), _decimals(t))
            counts.unit_total += 1
            counts.unit_correct += _norm(t.data.get("unit")) == _norm(p.data.get("unit"))
        elif t.event_type == "medication":
            counts.dose_total += 1
            counts.dose_correct += _norm(t.data.get("dose")) == _norm(p.data.get("dose"))
        if t.date_precision == "none":
            counts.undated_matched += 1
            counts.undated_invented += p.date_precision != "none" and p.event_date is not None
    for t in missed:
        counts.by_type[t.event_type].fn += 1
    for p in spurious:
        counts.by_type[p.event_type].fp += 1

    counts.undated_total = sum(1 for t in truth if t.date_precision == "none")
    # Date recall is NOT one-to-one: did the pipeline find this fact with an agreeing date?
    for t in truth:
        if t.date_precision == "none":
            continue
        counts.date_recall_total += 1
        counts.date_recall_found += any(
            p.event_type == t.event_type and key_of(p) == key_of(t) and dates_agree(t, p)
            for p in predicted)
    return counts


def _decimals(truth_lab) -> int:
    """Rounding precision for comparing a lab value: what the catalog prints it to."""
    for lab in LABS.values():
        if canonical_name(lab.name) == key_of(truth_lab):
            return lab.decimals
    return 1


def _prf(tp: int, fp: int, fn: int) -> dict:
    precision, recall = Rate(tp, tp + fp), Rate(tp, tp + fn)
    return {"precision": precision, "recall": recall, "f1": f1(precision.value, recall.value)}


def summarize_extraction(counts: ExtractionCounts) -> dict:
    """Rates with intervals from summed counts. Keys are stable (they feed metrics.json)."""
    overall = _prf(counts.tp, counts.fp, counts.fn)
    dated_found = counts.tp - counts.undated_matched
    dated_total = (counts.tp + counts.fn) - counts.undated_total
    summary = {
        "overall": overall,
        "by_type": {t: _prf(c.tp, c.fp, c.fn) for t, c in counts.by_type.items()},
        "recall_dated_only": Rate(dated_found, dated_total),
        "date_recall": Rate(counts.date_recall_found, counts.date_recall_total),
        "numeric_accuracy": Rate(counts.numeric_correct, counts.numeric_total),
        "unit_accuracy": Rate(counts.unit_correct, counts.unit_total),
        "dose_accuracy": Rate(counts.dose_correct, counts.dose_total),
        "undated_recall": Rate(counts.undated_matched, counts.undated_total),
        "invented_date_rate": Rate(counts.undated_invented, counts.undated_matched),
    }
    return summary


def to_jsonable(value) -> Optional[object]:
    """Rate/Mean -> dict, plain F1 floats rounded, containers recursed."""
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, float):
        return round(value, 6)
    return value
