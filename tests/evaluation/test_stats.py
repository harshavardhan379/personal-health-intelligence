"""Wilson intervals, rates, means and the 'is this a difference' rule (evaluation/stats.py)."""

import math

import pytest

from evaluation.stats import Mean, Rate, differ, f1, wilson_interval


# Reference values for the Wilson score interval (z = 1.96), computed independently.
@pytest.mark.parametrize("successes,n,low,high", [
    (7, 10, 0.3968, 0.8922),
    (14, 20, 0.4810, 0.8545),
    (0, 10, 0.0, 0.2775),
    (10, 10, 0.7225, 1.0),
    (50, 100, 0.4038, 0.5962),
])
def test_wilson_interval_matches_reference_values(successes, n, low, high):
    got_low, got_high = wilson_interval(successes, n)
    assert got_low == pytest.approx(low, abs=5e-4) and got_high == pytest.approx(high, abs=5e-4)


def test_wilson_interval_is_well_behaved_at_the_edges():
    assert wilson_interval(0, 0) == (0.0, 1.0)
    for n in (1, 2, 5, 20):
        for s in (0, n):
            low, high = wilson_interval(s, n)
            assert 0.0 <= low <= high <= 1.0
    with pytest.raises(ValueError):
        wilson_interval(5, 3)
    with pytest.raises(ValueError):
        wilson_interval(-1, 3)


def test_the_interval_always_contains_the_observed_proportion_and_shrinks_with_n():
    for n in (5, 20, 80):
        for s in range(0, n + 1, max(1, n // 5)):
            low, high = wilson_interval(s, n)
            assert low <= s / n <= high
    small, large = wilson_interval(5, 10), wilson_interval(50, 100)
    assert small[1] - small[0] > large[1] - large[0]


def test_a_rate_keeps_its_numerator_and_denominator_and_is_undefined_at_zero():
    rate = Rate(14, 20)
    assert rate.value == pytest.approx(0.7)
    assert rate.to_dict()["successes"] == 14 and rate.to_dict()["n"] == 20
    assert "14 of 20" in str(rate)
    empty = Rate(0, 0)
    assert empty.value is None
    assert empty.to_dict()["value"] is None and empty.to_dict()["ci_low"] is None
    assert str(empty).startswith("n/a")


def test_a_mean_has_a_value_an_interval_and_adds_by_pooling():
    scores = [1.0, 0.5, 0.0, 1.0, 0.5]
    mean = Mean.of(scores)
    assert mean.value == pytest.approx(0.6)
    low, high = mean.interval
    assert 0.0 <= low < 0.6 < high <= 1.0
    pooled = Mean.of([1.0, 0.5]) + Mean.of([0.0, 1.0, 0.5])
    assert pooled.value == pytest.approx(mean.value) and pooled.n == 5
    assert Mean.of([]).value is None
    # identical scores have no spread
    assert Mean.of([0.5] * 10).interval == pytest.approx((0.5, 0.5))


def test_differ_only_when_the_intervals_do_not_overlap():
    assert differ(Rate(2, 50), Rate(45, 50))
    assert not differ(Rate(14, 20), Rate(10, 20))  # 70% vs 50% on 20 questions: noise
    assert not differ(Rate(0, 0), Rate(10, 10))  # an undefined rate is never a difference
    assert differ(Mean.of([0.0] * 30), Mean.of([1.0] * 30))
    assert not differ(Mean.of([0.4, 0.6, 0.5]), Mean.of([0.5, 0.45, 0.55]))


def test_f1_follows_the_frameworks_edge_rules():
    assert f1(0.5, 0.5) == pytest.approx(0.5)
    assert f1(1.0, 0.0) == 0.0
    assert f1(None, 0.4) == 0.0  # nothing extracted: precision undefined, F1 is 0
    assert f1(0.5, None) is None
    assert math.isclose(f1(0.9, 0.6), 2 * 0.9 * 0.6 / 1.5)
