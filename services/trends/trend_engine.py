"""Trend detection (YGH-32, YGH-33). Pure functions, unit-tested in
tests/unit/test_trends.py without needing a database.

No medical causality is inferred here (brief, M9-T02) — this only characterizes
the shape of a numeric series over time.
"""

from dataclasses import dataclass
from typing import Literal

TrendDirection = Literal["increasing", "decreasing", "stable", "insufficient_data"]

MIN_POINTS_FOR_TREND = 2
# A relative change below this is "stable", not noise misread as a trend.
STABLE_THRESHOLD_RATIO = 0.05


@dataclass
class MeasurementPoint:
    date: str  # ISO date
    value: float
    unit: str


def detect_trend(points: list[MeasurementPoint]) -> TrendDirection:
    if len(points) < MIN_POINTS_FOR_TREND:
        return "insufficient_data"

    ordered = sorted(points, key=lambda p: p.date)
    first, last = ordered[0], ordered[-1]

    if first.unit != last.unit:
        # Unit mismatch: never silently compare mg/dL to mmol/L (M9-T04 test case).
        return "insufficient_data"

    if first.value == 0:
        return "stable" if last.value == 0 else "increasing" if last.value > 0 else "decreasing"

    relative_change = (last.value - first.value) / abs(first.value)

    if abs(relative_change) < STABLE_THRESHOLD_RATIO:
        return "stable"
    return "increasing" if relative_change > 0 else "decreasing"


def measurement_history(
    points: list[MeasurementPoint],
) -> tuple[list[MeasurementPoint], TrendDirection]:
    """YGH-32: ordered history + its trend, in one call."""
    ordered = sorted(points, key=lambda p: p.date)
    return ordered, detect_trend(ordered)
