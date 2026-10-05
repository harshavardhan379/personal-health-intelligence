"""YGH-35: monotonic, stable, noisy, missing-value and unit-mismatch cases."""

from services.trends.trend_engine import MeasurementPoint, detect_trend


def _pts(*rows):
    return [MeasurementPoint(date=d, value=v, unit=u) for d, v, u in rows]


def test_increasing_monotonic():
    points = _pts(("2026-01-01", 5.0, "%"), ("2026-02-01", 6.0, "%"), ("2026-03-01", 7.0, "%"))
    assert detect_trend(points) == "increasing"


def test_decreasing_monotonic():
    points = _pts(("2026-01-01", 9.0, "%"), ("2026-02-01", 7.0, "%"), ("2026-03-01", 5.0, "%"))
    assert detect_trend(points) == "decreasing"


def test_stable_small_fluctuation():
    points = _pts(("2026-01-01", 5.00, "%"), ("2026-02-01", 5.10, "%"), ("2026-03-01", 5.02, "%"))
    assert detect_trend(points) == "stable"


def test_noisy_but_net_increase_is_increasing():
    # First vs last point still moves up > threshold, even with a dip in the middle.
    points = _pts(
        ("2026-01-01", 5.0, "%"),
        ("2026-02-01", 4.0, "%"),
        ("2026-03-01", 4.5, "%"),
        ("2026-04-01", 6.5, "%"),
    )
    assert detect_trend(points) == "increasing"


def test_insufficient_data_single_point():
    points = _pts(("2026-01-01", 5.0, "%"))
    assert detect_trend(points) == "insufficient_data"


def test_insufficient_data_no_points():
    assert detect_trend([]) == "insufficient_data"


def test_unit_mismatch_is_insufficient_data():
    points = _pts(("2026-01-01", 5.0, "mg/dL"), ("2026-02-01", 90.0, "mmol/L"))
    assert detect_trend(points) == "insufficient_data"


def test_unordered_input_is_sorted_before_comparison():
    points = _pts(("2026-03-01", 7.0, "%"), ("2026-01-01", 5.0, "%"))
    assert detect_trend(points) == "increasing"


def test_zero_baseline_increasing():
    points = _pts(("2026-01-01", 0.0, "count"), ("2026-02-01", 2.0, "count"))
    assert detect_trend(points) == "increasing"
