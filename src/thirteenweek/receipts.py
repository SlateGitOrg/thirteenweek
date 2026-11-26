"""Open receivables and their survival-conditioned expected receipts.

For an open pay app aged ``a`` whole weeks the lag is known to be at least ``a + 1``. Its expected
receipt in forecast week ``k`` is ``amount * pmf(a + k) / S(a + 1)`` and the part still unpaid after
week 13 is ``amount * S(a + 14) / S(a + 1)``. The two always add back to the open amount.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from .lags import LagModel, age_weeks
from .models import BEYOND, DEFAULT_RETAINAGE_LAG_DAYS, HORIZON, Dataset, week_of

EPS = 1e-12  # S(a + 1) at or below this is "older than any observed lag"


@dataclass(frozen=True)
class OpenItem:
    item_id: str
    kind: str  # "PayApp" or "Retainage"
    job_id: str
    gc_id: str
    anchor: date  # billed date, or substantial completion for retainage
    amount: float
    share: float  # lower-tier subcontract share of the job
    age: int | None = None  # whole weeks since billing (pay apps)
    target_week: int | None = None  # expected release week (retainage)


@dataclass(frozen=True)
class WeightedItem:
    item: OpenItem
    weeks: tuple[float, ...]  # expected receipt in weeks 1..13
    after: float  # expected amount still unpaid after week 13


def pay_app_weights(
    model: LagModel, gc_id: str, age: int, amount: float
) -> tuple[list[float], float] | None:
    """Expected receipts by week and the amount left after week 13; ``None`` when S(a + 1) = 0."""
    pmf = model.pmf[gc_id]
    surv = model.surv(gc_id)
    s0 = float(surv[min(age + 1, BEYOND)])
    if s0 <= EPS:
        return None
    weeks = [
        0.0 if age + k > BEYOND - 1 else amount * float(pmf[age + k]) / s0
        for k in range(1, HORIZON + 1)
    ]
    return weeks, amount * float(surv[min(age + HORIZON + 1, BEYOND)]) / s0


def retainage_lags(known: Dataset) -> tuple[dict[str, float], float]:
    """Median days from substantial completion to retainage release, per GC and pooled."""
    by_gc: dict[str, list[int]] = {}
    for job in known.jobs:
        if job.retainage_paid is not None:
            by_gc.setdefault(job.gc_id, []).append(
                (job.retainage_paid - job.substantial_completion).days
            )
    pooled = [d for v in by_gc.values() for d in v]
    fallback = float(median(pooled)) if pooled else DEFAULT_RETAINAGE_LAG_DAYS
    return {gc: float(median(v)) for gc, v in by_gc.items()}, fallback


def build_open_items(
    known: Dataset, as_of: date, model: LagModel
) -> tuple[list[WeightedItem], list[OpenItem]]:
    """Weighted receivables, plus pay apps excluded as aged / disputed."""
    jobs = known.job_map()
    weighted: list[WeightedItem] = []
    disputed: list[OpenItem] = []
    for app in sorted(known.apps, key=lambda a: (a.billed, a.app_id)):
        if app.paid is not None:
            continue
        job = jobs[app.job_id]
        item = OpenItem(
            app.app_id,
            "PayApp",
            job.job_id,
            job.gc_id,
            app.billed,
            app.net,
            job.lower_tier_share,
            age=age_weeks(app.billed, as_of),
        )
        result = pay_app_weights(model, job.gc_id, item.age, item.amount)
        if result is None:
            disputed.append(item)
        else:
            weighted.append(WeightedItem(item, tuple(result[0]), result[1]))
    by_gc, fallback = retainage_lags(known)
    held: dict[str, float] = {}
    for app in known.apps:
        held[app.job_id] = held.get(app.job_id, 0.0) + app.retainage
    for job in sorted(known.jobs, key=lambda j: j.job_id):
        amount = round(held.get(job.job_id, 0.0), 2)
        if job.retainage_paid is not None or amount <= 0:
            continue
        lag = by_gc.get(job.gc_id, fallback)
        release = job.substantial_completion + timedelta(days=lag)
        week = max(1, math.ceil(((release - as_of).days) / 7))
        item = OpenItem(
            f"RET-{job.job_id}",
            "Retainage",
            job.job_id,
            job.gc_id,
            job.substantial_completion,
            amount,
            job.lower_tier_share,
            target_week=week,
        )
        weeks = tuple(amount if week == k else 0.0 for k in range(1, HORIZON + 1))
        weighted.append(WeightedItem(item, weeks, amount if week > HORIZON else 0.0))
    return weighted, disputed


def terms_weights(
    known: Dataset, as_of: date, terms_days: dict[str, int], include_past_due: bool = True
) -> list[float]:
    """The baseline: every open pay app is collected on contract terms, retainage 30 days after
    substantial completion. Anything already past due lands in week 1, unless
    ``include_past_due`` is false, in which case past-due items are left out entirely."""
    jobs = known.job_map()
    weeks = [0.0] * HORIZON
    for app in known.apps:
        if app.paid is not None:
            continue
        due = app.billed + timedelta(days=terms_days[jobs[app.job_id].gc_id])
        if not include_past_due and due <= as_of:
            continue
        k = week_of(due, as_of)
        if k <= HORIZON:
            weeks[k - 1] += app.net
    held: dict[str, float] = {}
    for app in known.apps:
        held[app.job_id] = held.get(app.job_id, 0.0) + app.retainage
    for job in known.jobs:
        if job.retainage_paid is not None or held.get(job.job_id, 0.0) <= 0:
            continue
        due = job.substantial_completion + timedelta(days=30)
        if not include_past_due and due <= as_of:
            continue
        k = week_of(due, as_of)
        if k <= HORIZON:
            weeks[k - 1] += round(held[job.job_id], 2)
    return weeks
