"""Score the receipts forecast at every horizon against what was actually collected.

At each weekly origin the forecast is rebuilt from data known on that day only. It is scored on the
receivables that were open at the origin (pay apps and retainage), so new billings after the origin
never count for or against either method. The baseline is the contract-terms method the lender's
template assumed: every open pay app is collected ``terms_days`` after billing, past-due items in
week 1. A second terms variant leaves past-due items out, to show how much of the baseline's
week-1 spike is past-due accumulation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .models import HORIZON, Dataset, week_of
from .receipts import terms_weights
from .reference import compute_forecast, make_snapshot

HORIZON_DAYS = 7 * HORIZON


@dataclass(frozen=True)
class HorizonScore:
    horizon: int
    actual: float
    wape_lag: float
    bias_lag: float
    wape_terms: float
    bias_terms: float
    cum_bias_lag: float  # bias of cumulative receipts through this horizon
    cum_bias_terms: float
    wape_terms_ex: float  # terms method with past-due items left out
    bias_terms_ex: float


def origins(as_of: date, weeks: int = 26) -> list[date]:
    """Weekly origins, oldest first, the last one 13 weeks before the last data date."""
    last = as_of - timedelta(days=HORIZON_DAYS)
    return [last - timedelta(days=7 * i) for i in range(weeks - 1, -1, -1)]


def realised_receipts(full: Dataset, origin: date) -> list[float]:
    """Weekly cash actually collected on the items that were open at ``origin``."""
    known = full.known_at(origin)
    paid_later = {a.app_id: a.paid for a in full.apps}
    weeks = [0.0] * HORIZON
    for app in known.apps:
        if app.paid is not None:
            continue
        paid = paid_later[app.app_id]
        if paid is not None and week_of(paid, origin) <= HORIZON:
            weeks[week_of(paid, origin) - 1] += app.net
    held: dict[str, float] = {}
    for app in known.apps:
        held[app.job_id] = held.get(app.job_id, 0.0) + app.retainage
    release = {j.job_id: j.retainage_paid for j in full.jobs}
    for job in known.jobs:
        if job.retainage_paid is not None or held.get(job.job_id, 0.0) <= 0:
            continue
        paid = release[job.job_id]
        if paid is not None and week_of(paid, origin) <= HORIZON:
            weeks[week_of(paid, origin) - 1] += round(held[job.job_id], 2)
    return weeks


def run_backtest(
    ds: Dataset, as_of: date, weeks: int = 26, snapshot_dir=None, force: bool = False
) -> list[HorizonScore]:
    terms = {g.gc_id: g.terms_days for g in ds.gcs}
    abs_lag = [0.0] * HORIZON
    abs_terms = [0.0] * HORIZON
    err_lag = [0.0] * HORIZON
    err_terms = [0.0] * HORIZON
    abs_ex = [0.0] * HORIZON
    err_ex = [0.0] * HORIZON
    actual_total = [0.0] * HORIZON
    for origin in origins(as_of, weeks):
        forecast = compute_forecast(ds, origin)
        if snapshot_dir is not None:
            from .snapshot import write_snapshot

            write_snapshot(make_snapshot(forecast), snapshot_dir, force=force)
        lag = forecast.lines["receipts"]
        baseline = terms_weights(ds.known_at(origin), origin, terms)
        baseline_ex = terms_weights(ds.known_at(origin), origin, terms, include_past_due=False)
        actual = realised_receipts(ds, origin)
        for h in range(HORIZON):
            actual_total[h] += actual[h]
            err_lag[h] += lag[h] - actual[h]
            err_terms[h] += baseline[h] - actual[h]
            abs_lag[h] += abs(lag[h] - actual[h])
            abs_terms[h] += abs(baseline[h] - actual[h])
            err_ex[h] += baseline_ex[h] - actual[h]
            abs_ex[h] += abs(baseline_ex[h] - actual[h])
    scores = []
    for h in range(HORIZON):
        cum_actual = sum(actual_total[: h + 1])
        scores.append(
            HorizonScore(
                h + 1,
                actual_total[h],
                _ratio(abs_lag[h], actual_total[h]),
                _ratio(err_lag[h], actual_total[h]),
                _ratio(abs_terms[h], actual_total[h]),
                _ratio(err_terms[h], actual_total[h]),
                _ratio(sum(err_lag[: h + 1]), cum_actual),
                _ratio(sum(err_terms[: h + 1]), cum_actual),
                _ratio(abs_ex[h], actual_total[h]),
                _ratio(err_ex[h], actual_total[h]),
            )
        )
    return scores


def _ratio(num: float, den: float) -> float:
    """Scores are undefined (NaN) for a horizon in which nothing was collected."""
    return num / den if den else float("nan")
