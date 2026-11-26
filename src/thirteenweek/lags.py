"""Per-GC weekly pay-lag distributions, estimated from what is known at the as-of date.

A pay app's lag bin is ``ceil(lag_days / 7)``: bin ``j`` holds payments made more than ``j - 1``
and at most ``j`` weeks after billing. Bins run 0..30 plus a "beyond" bin (index 31).

Unpaid apps are right-censored, so a plain histogram of paid apps would understate long lags
(everything slow is still unpaid). The estimator is a discrete Kaplan-Meier product-limit:
an app of age ``a`` whole weeks sits in the risk set of bins ``0..a`` without an event, and the
distribution ends at the longest *observed* paid lag. That last step is what makes "older than
any observed lag" a well-defined, zero-survival state.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from .models import BEYOND, N_BINS, SHRINK_K, WINDOW_WEEKS, Dataset


def lag_bin(lag_days: int) -> int:
    return min(max(math.ceil(lag_days / 7), 0), BEYOND)


def age_weeks(billed: date, as_of: date) -> int:
    return (as_of - billed).days // 7


def estimate_pmf(paid_bins: list[int], open_ages: list[int]) -> np.ndarray | None:
    """Discrete Kaplan-Meier PMF over the 32 lag bins; ``None`` when nothing has been paid."""
    if not paid_bins:
        return None
    events = np.bincount(paid_bins, minlength=N_BINS).astype(float)
    at_risk = np.zeros(N_BINS)
    for j in range(N_BINS):
        at_risk[j] = sum(1 for b in paid_bins if b >= j) + sum(1 for a in open_ages if a >= j)
    hazard = np.divide(events, at_risk, out=np.zeros(N_BINS), where=at_risk > 0)
    last = max(paid_bins)
    hazard[last] = 1.0
    pmf = np.zeros(N_BINS)
    surviving = 1.0
    for j in range(last + 1):
        pmf[j] = surviving * hazard[j]
        surviving *= 1.0 - hazard[j]
    return pmf


def shift_pmf(pmf: np.ndarray, weeks: int) -> np.ndarray:
    """Move a PMF ``weeks`` bins later; mass pushed past bin 30 joins "beyond"."""
    out = np.zeros(N_BINS)
    for j in range(N_BINS):
        out[min(j + weeks, BEYOND) if j < BEYOND else BEYOND] += pmf[j]
    return out


def survival(pmf: np.ndarray) -> np.ndarray:
    """``S[j] = P(lag bin >= j)`` for j = 0..31."""
    return np.array([float(np.sum(pmf[j:])) for j in range(N_BINS)])


@dataclass
class LagModel:
    gc_ids: list[str]
    pmf: dict[str, np.ndarray]  # includes "POOLED"
    n_paid: dict[str, int]
    n_open: dict[str, int]
    weight: dict[str, float]  # weight on the GC's own PMF
    stressed: str | None = None

    def surv(self, gc_id: str) -> np.ndarray:
        return survival(self.pmf[gc_id])


def build_model(
    ds: Dataset, as_of: date, stress_gc: str | None = None, stress_weeks: int = 0
) -> LagModel:
    """Estimate every GC's PMF from apps billed in the 78 weeks up to ``as_of``."""
    known = ds.known_at(as_of)
    window_start = as_of - timedelta(weeks=WINDOW_WEEKS)
    job_gc = {j.job_id: j.gc_id for j in known.jobs}
    paid: dict[str, list[int]] = {g.gc_id: [] for g in known.gcs}
    open_: dict[str, list[int]] = {g.gc_id: [] for g in known.gcs}
    for app in sorted(known.apps, key=lambda a: a.app_id):
        if app.billed < window_start:
            continue
        gc = job_gc[app.job_id]
        if app.paid is not None:
            paid[gc].append(lag_bin((app.paid - app.billed).days))
        else:
            open_[gc].append(age_weeks(app.billed, as_of))
    all_paid = [b for g in known.gcs for b in paid[g.gc_id]]
    all_open = [a for g in known.gcs for a in open_[g.gc_id]]
    pooled = estimate_pmf(all_paid, all_open)
    if pooled is None:
        raise ValueError(f"no paid pay apps in the {WINDOW_WEEKS} weeks before {as_of}")
    pmf: dict[str, np.ndarray] = {}
    weight: dict[str, float] = {}
    for g in known.gcs:
        own = estimate_pmf(paid[g.gc_id], open_[g.gc_id])
        n = len(paid[g.gc_id])
        if own is None:
            w = 0.0
            pmf[g.gc_id] = pooled.copy()
        elif n >= SHRINK_K:
            w = 1.0
            pmf[g.gc_id] = own
        else:
            w = n / (n + SHRINK_K)
            pmf[g.gc_id] = w * own + (1.0 - w) * pooled
        weight[g.gc_id] = w
    pmf["POOLED"] = pooled
    if stress_gc is not None and stress_weeks:
        pmf[stress_gc] = shift_pmf(pmf[stress_gc], stress_weeks)
    return LagModel(
        gc_ids=[g.gc_id for g in known.gcs],
        pmf=pmf,
        n_paid={g.gc_id: len(paid[g.gc_id]) for g in known.gcs} | {"POOLED": len(all_paid)},
        n_open={g.gc_id: len(open_[g.gc_id]) for g in known.gcs} | {"POOLED": len(all_open)},
        weight=weight,
        stressed=stress_gc if stress_weeks else None,
    )


def pmf_median_bin(pmf: np.ndarray) -> int:
    return int(np.searchsorted(np.cumsum(pmf), 0.5 - 1e-12))
