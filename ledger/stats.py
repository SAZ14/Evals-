"""Small-sample statistics for rates."""

from __future__ import annotations

import math


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes out of n (95% by default). (0, 1) when n == 0."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def fmt_rate(k: int, n: int) -> str:
    """'3/5 60% [23–88]': count, rate and Wilson 95% CI in percent; '-' when n == 0."""
    if n == 0:
        return "-"
    lo, hi = wilson(k, n)
    return f"{k}/{n} {100 * k / n:.0f}% [{100 * lo:.0f}–{100 * hi:.0f}]"
