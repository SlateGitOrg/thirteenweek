"""Has a GC started paying slower? One-sided Mann-Whitney per GC, Benjamini-Hochberg across GCs.

Recent group: pay apps billed 13-26 weeks before the as-of date. Reference group: billed 27-78
weeks before. An unpaid app enters at its current age in days, a conservative right-censored value
(its true lag can only be longer). A GC is flagged only when the adjusted p-value is at most q
*and* the median lag moved by at least ``MIN_SHIFT_DAYS``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import median

from scipy.stats import mannwhitneyu

from .models import WINDOW_WEEKS, Dataset

MIN_SHIFT_DAYS = 10.0
MIN_GROUP = 5
Q = 0.05


@dataclass(frozen=True)
class DriftRow:
    gc_id: str
    n_recent: int
    n_reference: int
    median_recent: float
    median_reference: float
    shift_days: float
    p_value: float
    p_adjusted: float
    flagged: bool


def benjamini_hochberg(p_values: list[float]) -> list[float]:
    """Step-up adjusted p-values (monotone, capped at 1)."""
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, p_values[i] * m / rank)
        adjusted[i] = running
    return adjusted


def _lags(ds: Dataset, as_of: date) -> dict[str, tuple[list[float], list[float]]]:
    known = ds.known_at(as_of)
    job_gc = {j.job_id: j.gc_id for j in known.jobs}
    out: dict[str, tuple[list[float], list[float]]] = {g.gc_id: ([], []) for g in known.gcs}
    for app in sorted(known.apps, key=lambda a: a.app_id):
        days_before = (as_of - app.billed).days
        value = float((app.paid - app.billed).days) if app.paid else float(days_before)
        recent, reference = out[job_gc[app.job_id]]
        if 13 * 7 <= days_before <= 26 * 7:
            recent.append(value)
        elif 26 * 7 < days_before <= WINDOW_WEEKS * 7:
            reference.append(value)
    return out


def run_drift(ds: Dataset, as_of: date) -> list[DriftRow]:
    groups = _lags(ds, as_of)
    tested = [g for g, (r, o) in groups.items() if len(r) >= MIN_GROUP and len(o) >= MIN_GROUP]
    pvals = [
        float(mannwhitneyu(groups[g][0], groups[g][1], alternative="greater").pvalue)
        for g in tested
    ]
    adjusted = benjamini_hochberg(pvals)
    rows = []
    for g, p, adj in zip(tested, pvals, adjusted, strict=True):
        recent, reference = groups[g]
        shift = median(recent) - median(reference)
        rows.append(
            DriftRow(
                g,
                len(recent),
                len(reference),
                float(median(recent)),
                float(median(reference)),
                float(shift),
                p,
                adj,
                adj <= Q and shift >= MIN_SHIFT_DAYS,
            )
        )
    return rows
