"""Paired statistics for the preregistered decision rule.

Randomization (sign-flip) tests follow Smucker, Allan & Carterette (CIKM 2007)
and Urbano, Lima & Hanjalic (SIGIR 2019); see research/evidence.md.
"""
from itertools import product
import math
import numpy as np
from statistics import NormalDist

SEED = 20261018
TOLERANCE = 1e-12


def _finite(values):
    values = [float(v) for v in values]
    if not values or not all(math.isfinite(v) for v in values):
        raise ValueError("Paired differences must be a non-empty list of finite numbers")
    return values


def sign_flip_p(diffs, exact_limit=16, resamples=100_000):
    """One-sided p-value for H1: mean difference > 0.

    Under H0 each paired difference is equally likely to have either sign.
    Enumerates every assignment up to exact_limit pairs; otherwise samples
    with a fixed seed, so the result is reproducible.
    """
    diffs = _finite(diffs)
    observed = sum(diffs) - TOLERANCE
    if len(diffs) <= exact_limit:
        sums = (sum(s * d for s, d in zip(signs, diffs))
                for signs in product((1, -1), repeat=len(diffs)))
        return sum(total >= observed for total in sums) / 2 ** len(diffs)
    signs = np.random.default_rng(SEED).choice(
        np.array([-1.0, 1.0]), size=(resamples, len(diffs)))
    hits = int(np.count_nonzero(signs @ np.array(diffs) >= observed))
    return (hits + 1) / (resamples + 1)


def non_inferiority_p(diffs, margin):
    """One-sided p-value for H1: mean difference > -margin (candidate minus baseline)."""
    return sign_flip_p([d + margin for d in _finite(diffs)])


def holm(p_values, alpha):
    """Holm step-down: which hypotheses are rejected at family-wise level alpha."""
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    rejected = [False] * len(p_values)
    for rank, i in enumerate(order):
        if p_values[i] > alpha / (len(p_values) - rank):
            break
        rejected[i] = True
    return rejected


def wilson(successes, n, confidence=0.95):
    """Wilson score interval for a binomial proportion."""
    if n <= 0 or not 0 <= successes <= n:
        raise ValueError("Wilson interval needs 0 <= successes <= n and n > 0")
    z = NormalDist().inv_cdf(1 - (1 - confidence) / 2)
    p = successes / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def required_n(sd, margin, alpha=0.05, power=0.80):
    """Paired one-sided non-inferiority size, normal approximation, true difference 0.

    A planning estimate only; it is not a property of any cited paper.
    """
    if margin <= 0 or sd < 0:
        raise ValueError("Margin must be positive and sd non-negative")
    z = NormalDist().inv_cdf(1 - alpha) + NormalDist().inv_cdf(power)
    return max(2, math.ceil(z * z * sd * sd / (margin * margin) - TOLERANCE))
