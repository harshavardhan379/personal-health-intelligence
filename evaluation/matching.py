"""Matching extracted events to ground-truth events (framework 3.1).

Two events match when the type is equal, the dates agree at the COARSER of their two
precisions (or the truth is undated), and the key field is equal after alias normalisation.
Values are not part of the match: a right-named, right-dated lab with the wrong value is a
match that then counts against numeric accuracy.

The assignment is one-to-one and MAXIMUM (Kuhn's augmenting paths), not greedy: a greedy pass
can leave a truth event unmatched that another pairing would have matched, and the score
would then depend on the order the events were listed in. Inputs are sorted first, so the
chosen pairs, not just their number, are deterministic.

Ties are broken in the extractor's favour. Two truth events can be indistinguishable under
the match rule (the same drug on the same day at two different doses, which is exactly what a
planted contradiction is). Among equally valid pairings the one whose values AGREE is chosen,
so a perfect pipeline cannot lose dose or numeric accuracy to an arbitrary swap. This only
ever chooses between candidates the rule cannot separate; it never adds a match.
"""

import calendar
from datetime import date
from typing import Optional

from evaluation.catalogs import canonical_name
from evaluation.model import KEY_FIELD

_COARSENESS = {"day": 0, "month": 1, "year": 2}


def key_of(event) -> str:
    """The event's key field (a test name, medication, ...), alias-normalised."""
    return canonical_name(str(event.data.get(KEY_FIELD[event.event_type], "")))


def date_range(event) -> Optional[tuple]:
    """(first day, last day) the event's date could mean; None when undated."""
    when, precision = event.event_date, event.date_precision
    if when is None or precision == "none":
        return None
    if precision == "month":
        last = calendar.monthrange(when.year, when.month)[1]
        return (when.replace(day=1), when.replace(day=last))
    if precision == "year":
        return (date(when.year, 1, 1), date(when.year, 12, 31))
    return (when, when)


def dates_agree(truth, predicted) -> bool:
    """Framework 3.1 condition 2."""
    if truth.date_precision == "none" or truth.event_date is None:
        return True  # undated truth: waived
    if predicted.date_precision == "none" or predicted.event_date is None:
        return False  # a truth that has a date never matches an extraction without one
    level = max(_COARSENESS[truth.date_precision], _COARSENESS[predicted.date_precision])
    a, b = truth.event_date, predicted.event_date
    if level == 0:
        return a == b
    if level == 1:
        return (a.year, a.month) == (b.year, b.month)
    return a.year == b.year


def can_match(truth, predicted) -> bool:
    return (truth.event_type == predicted.event_type
            and key_of(truth) == key_of(predicted)
            and dates_agree(truth, predicted))


def _norm(text) -> str:
    return "".join(str(text).split()).lower()


def values_differ(truth, predicted) -> int:
    """0 when the principal value (a lab's value, a medication's dose) agrees, else 1."""
    if truth.event_type == "lab":
        try:
            return int(abs(float(truth.data.get("value")) - float(predicted.data.get("value")))
                       > 1e-6)
        except (TypeError, ValueError):
            return 1
    if truth.event_type == "medication":
        return int(_norm(truth.data.get("dose")) != _norm(predicted.data.get("dose")))
    return 0


def _sort_key(event) -> tuple:
    # Within one type and key, DATED events come before undated ones. Kuhn's algorithm never
    # un-matches a truth it has already matched, so this makes a dated truth win an
    # extraction over an undated truth (which waives the date and is the looser match)
    # without ever reducing the total number of matches.
    undated = event.event_date is None
    return (event.event_type, key_of(event), undated, event.event_date or date.max,
            event.date_precision, repr(sorted(event.data.items(), key=lambda kv: kv[0])))


def match_events(truth: list, predicted: list) -> tuple:
    """Returns (pairs, unmatched_truth, unmatched_predicted) as lists of events, where pairs
    is a list of (truth event, predicted event). Deterministic and order-independent."""
    truth = sorted(truth, key=_sort_key)
    predicted = sorted(predicted, key=_sort_key)

    def exactness(t, p) -> int:
        return 0 if (t.event_date == p.event_date and t.date_precision == p.date_precision) else 1

    adjacent = []
    for t in truth:
        options = [j for j, p in enumerate(predicted) if can_match(t, p)]
        adjacent.append(sorted(options, key=lambda j: (
            exactness(t, predicted[j]), values_differ(t, predicted[j]), j)))

    owner: dict = {}  # predicted index -> truth index

    def assign(i: int, seen: set) -> bool:
        for j in adjacent[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in owner or assign(owner[j], seen):
                owner[j] = i
                return True
        return False

    for i in range(len(truth)):
        assign(i, set())

    pairs = [(truth[i], predicted[j]) for j, i in sorted(owner.items(), key=lambda kv: kv[1])]
    matched_truth = set(owner.values())
    unmatched_truth = [t for i, t in enumerate(truth) if i not in matched_truth]
    unmatched_predicted = [p for j, p in enumerate(predicted) if j not in owner]
    return pairs, unmatched_truth, unmatched_predicted
