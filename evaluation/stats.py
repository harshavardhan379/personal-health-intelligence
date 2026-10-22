"""Rates and intervals (framework principle 4 and section 8).

Per-item scorers return COUNTS; this module turns summed counts into rates with 95 percent
intervals. A rate is reported with its numerator and denominator so it stays traceable:
"14 of 20", never a bare 0.7.

  Rate      a proportion (successes of n), with a Wilson score interval. Right for any
            "fraction of X that Y" metric; behaves for small n and for 0 or n successes.
  Mean      an average of per-item scores in [0, 1] (partial credit, reciprocal rank) with a
            normal-approximation interval clipped to [0, 1]. Not a proportion, so no Wilson.

`differ(a, b)` is the framework's rule for calling something a difference: the intervals do
not overlap. It is deliberately conservative.
"""

import math
from dataclasses import dataclass
from typing import Optional

Z95 = 1.96


def wilson_interval(successes: int, n: int, z: float = Z95) -> tuple:
    """95 percent Wilson score interval for a proportion. (0.0, 1.0) when n is 0."""
    if n < 0 or successes < 0 or successes > n:
        raise ValueError(f"need 0 <= successes <= n, got {successes} of {n}")
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True)
class Rate:
    successes: int
    n: int

    @property
    def value(self) -> Optional[float]:
        """None when n is 0: an undefined rate is never reported as 0 or 1."""
        return self.successes / self.n if self.n else None

    @property
    def interval(self) -> tuple:
        return wilson_interval(self.successes, self.n)

    def to_dict(self) -> dict:
        low, high = self.interval
        return {
            "successes": self.successes, "n": self.n,
            "value": None if self.value is None else round(self.value, 6),
            "ci_low": None if self.n == 0 else round(low, 6),
            "ci_high": None if self.n == 0 else round(high, 6),
        }

    def __str__(self) -> str:
        if self.n == 0:
            return "n/a (0 of 0)"
        low, high = self.interval
        return f"{self.value:.3f} ({self.successes} of {self.n}; 95% CI {low:.3f}-{high:.3f})"


@dataclass(frozen=True)
class Mean:
    """The mean of n per-item scores in [0, 1]."""

    total: float
    total_squares: float
    n: int

    @staticmethod
    def of(scores: list) -> "Mean":
        return Mean(sum(scores), sum(s * s for s in scores), len(scores))

    def __add__(self, other: "Mean") -> "Mean":
        return Mean(self.total + other.total, self.total_squares + other.total_squares,
                    self.n + other.n)

    @property
    def value(self) -> Optional[float]:
        return self.total / self.n if self.n else None

    @property
    def interval(self) -> tuple:
        if self.n == 0:
            return (0.0, 1.0)
        mean = self.total / self.n
        if self.n == 1:
            return (0.0, 1.0)
        variance = max(0.0, (self.total_squares - self.n * mean * mean) / (self.n - 1))
        half = Z95 * math.sqrt(variance / self.n)
        return (max(0.0, mean - half), min(1.0, mean + half))

    def to_dict(self) -> dict:
        low, high = self.interval
        return {
            "mean": None if self.value is None else round(self.value, 6), "n": self.n,
            "ci_low": None if self.n == 0 else round(low, 6),
            "ci_high": None if self.n == 0 else round(high, 6),
        }


def differ(a, b) -> bool:
    """True only if the two intervals do not overlap (framework principle 4)."""
    if a.n == 0 or b.n == 0:
        return False
    (a_low, a_high), (b_low, b_high) = a.interval, b.interval
    return a_high < b_low or b_high < a_low


def f1(precision: Optional[float], recall: Optional[float]) -> Optional[float]:
    """Harmonic mean. 0 when nothing was extracted; undefined only if recall is undefined."""
    if recall is None:
        return None
    if precision is None:
        return 0.0
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)
