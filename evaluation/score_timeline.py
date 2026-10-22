"""Timeline scoring (framework section 5), defined for EXTRACTED mode.

  * event accuracy       F1 over timeline entries with the extraction match rule (3.1): extraction
                         F1 after review-gating and de-duplication.
  * ordering accuracy    among matched events whose TRUTH date ranges do not overlap, the
                         fraction the timeline lists in the right order. Month- and year-precise
                         events overlap everything in their period, and undated ones have no
                         range, so those pairs are excluded.
  * duplicate detection  `injected` duplicates (known from the dataset), `residual` duplicates left
                         in the timeline (entries identical to another entry in type, key, date
                         and principal value). removal rate = removed / injected, where removed
                         = injected - residual, floored at 0. Residual duplicates that did not
                         come from an injected one (an extraction quirk) also count against the
                         system, which is the intent: a clean timeline has none.
"""

from dataclasses import dataclass, field

from evaluation.matching import date_range, key_of, match_events
from evaluation.score_extraction import ExtractionCounts, score_extraction
from evaluation.stats import Rate


@dataclass
class TimelineCounts:
    events: ExtractionCounts = field(default_factory=ExtractionCounts)
    ordering_pairs: int = 0
    ordering_correct: int = 0
    injected_duplicates: int = 0
    removed_duplicates: int = 0  # per patient: max(0, injected - residual)
    residual_duplicates: int = 0
    timeline_entries: int = 0

    def __add__(self, other: "TimelineCounts") -> "TimelineCounts":
        return TimelineCounts(
            events=self.events + other.events,
            ordering_pairs=self.ordering_pairs + other.ordering_pairs,
            ordering_correct=self.ordering_correct + other.ordering_correct,
            injected_duplicates=self.injected_duplicates + other.injected_duplicates,
            removed_duplicates=self.removed_duplicates + other.removed_duplicates,
            residual_duplicates=self.residual_duplicates + other.residual_duplicates,
            timeline_entries=self.timeline_entries + other.timeline_entries,
        )


def _principal(event) -> str:
    if event.event_type == "lab":
        return str(event.data.get("value"))
    if event.event_type == "medication":
        return "".join(str(event.data.get("dose")).split()).lower()
    return ""


def _signature(event) -> tuple:
    return (event.event_type, key_of(event), event.event_date, event.date_precision,
            _principal(event))


def residual_duplicates(timeline: list) -> int:
    """Entries that repeat another entry exactly."""
    return len(timeline) - len({_signature(e) for e in timeline})


def score_timeline(timeline: list, truth: list, injected_duplicates: int) -> TimelineCounts:
    """`timeline` is the system's timeline IN THE ORDER IT LISTS IT."""
    position = {id(event): index for index, event in enumerate(timeline)}
    pairs, _, _ = match_events(truth, timeline)

    ranged = []
    for t, p in pairs:
        window = date_range(t)
        if window is not None:
            ranged.append((window, position[id(p)]))
    comparable = correct = 0
    for i in range(len(ranged)):
        for j in range(i + 1, len(ranged)):
            (a_start, a_end), a_at = ranged[i]
            (b_start, b_end), b_at = ranged[j]
            if a_end < b_start:
                earlier_at, later_at = a_at, b_at
            elif b_end < a_start:
                earlier_at, later_at = b_at, a_at
            else:
                continue  # the ranges overlap: their order is not defined
            comparable += 1
            correct += earlier_at < later_at

    residual = residual_duplicates(timeline)
    return TimelineCounts(
        events=score_extraction(timeline, truth), ordering_pairs=comparable,
        ordering_correct=correct, injected_duplicates=injected_duplicates,
        removed_duplicates=max(0, injected_duplicates - residual),
        residual_duplicates=residual, timeline_entries=len(timeline))


def summarize_timeline(counts: TimelineCounts) -> dict:
    return {
        "event_accuracy_precision": Rate(counts.events.tp, counts.events.tp + counts.events.fp),
        "event_accuracy_recall": Rate(counts.events.tp, counts.events.tp + counts.events.fn),
        "ordering_accuracy": Rate(counts.ordering_correct, counts.ordering_pairs),
        "duplicate_removal_rate": Rate(counts.removed_duplicates, counts.injected_duplicates),
        "residual_duplicate_rate": Rate(counts.residual_duplicates, counts.timeline_entries),
    }
