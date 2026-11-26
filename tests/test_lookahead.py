from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from thirteenweek.reference import compute_forecast, make_snapshot


def truncate(ds, origin):
    """Independent truncation: drop later rows, blank later payment dates."""

    def blank(d):
        return None if d is not None and d > origin else d

    return replace(
        ds,
        jobs=tuple(replace(j, retainage_paid=blank(j.retainage_paid)) for j in ds.jobs),
        apps=tuple(replace(a, paid=blank(a.paid)) for a in ds.apps if a.billed <= origin),
        invoices=tuple(
            replace(i, paid=blank(i.paid)) for i in ds.invoices if i.invoice_date <= origin
        ),
        payroll=tuple(p for p in ds.payroll if p.week_ending <= origin + timedelta(days=91)),
    )


def perturb_the_future(ds, origin):
    """Rewrite every field that was not knowable at ``origin``; leave known facts alone.

    Later billings change amount and payment; open apps get arbitrary later payment dates; later
    invoices change amount and due date; later payments on earlier invoices move; retainage release
    dates after the origin move; jobs that had not started get different terms and shares; payroll
    plan rows beyond the horizon change.
    """
    horizon_end = origin + timedelta(days=91)
    apps = []
    for i, a in enumerate(ds.apps):
        if a.billed > origin:
            apps.append(
                replace(
                    a,
                    gross=a.gross * 1.7 + 1000,
                    retainage=a.retainage * 1.7,
                    paid=None if i % 2 else a.billed + timedelta(days=2),
                )
            )
        elif a.paid is None or a.paid > origin:
            later = None if i % 3 == 0 else origin + timedelta(days=1 + i % 40)
            apps.append(replace(a, paid=later))
        else:
            apps.append(a)
    invoices = []
    for i, inv in enumerate(ds.invoices):
        if inv.invoice_date > origin:
            invoices.append(
                replace(inv, amount=inv.amount * 3, due_date=inv.due_date - timedelta(days=200))
            )
        elif inv.paid is None or inv.paid > origin:
            invoices.append(replace(inv, paid=None if i % 2 else origin + timedelta(days=3)))
        else:
            invoices.append(inv)
    started = {a.job_id for a in ds.apps if a.billed <= origin}
    other_gc = {g.gc_id: ds.gcs[(n + 1) % len(ds.gcs)].gc_id for n, g in enumerate(ds.gcs)}
    jobs = []
    for j in ds.jobs:
        if j.job_id not in started:
            jobs.append(
                replace(
                    j,
                    gc_id=other_gc[j.gc_id],
                    contract_value=j.contract_value * 5,
                    start_date=j.start_date + timedelta(days=90),
                    substantial_completion=j.substantial_completion + timedelta(days=90),
                    lower_tier_share=0.99,
                    retainage_paid=origin + timedelta(days=9),
                )
            )
        elif j.retainage_paid is None or j.retainage_paid > origin:
            jobs.append(replace(j, retainage_paid=origin + timedelta(days=4)))
        else:
            jobs.append(j)
    payroll = tuple(
        replace(p, amount=p.amount * 5) if p.week_ending > horizon_end else p for p in ds.payroll
    )
    return replace(
        ds, apps=tuple(apps), invoices=tuple(invoices), jobs=tuple(jobs), payroll=payroll
    )


@pytest.mark.parametrize("weeks_back", [13, 20, 30, 45])
@pytest.mark.parametrize("stress", [False, True])
def test_snapshot_from_full_data_equals_snapshot_from_truncated_data(ds, weeks_back, stress):
    origin = ds.as_of - timedelta(weeks=weeks_back)
    full = make_snapshot(compute_forecast(ds, origin, stress))
    cut = make_snapshot(compute_forecast(truncate(ds, origin), origin, stress))
    assert full == cut
    assert full["sha256"] == cut["sha256"]


@pytest.mark.parametrize("weeks_back", [13, 20, 30, 45])
@pytest.mark.parametrize("stress", [False, True])
def test_rewriting_every_later_dated_field_leaves_the_snapshot_unchanged(ds, weeks_back, stress):
    origin = ds.as_of - timedelta(weeks=weeks_back)
    future_changed = perturb_the_future(ds, origin)
    assert future_changed != ds
    before = make_snapshot(compute_forecast(ds, origin, stress))
    after = make_snapshot(compute_forecast(future_changed, origin, stress))
    assert before == after


def test_the_perturbation_is_not_vacuous(ds):
    """The same rewrite does change a forecast made at a later date, so it touches live inputs."""
    origin = ds.as_of - timedelta(weeks=20)
    later = origin + timedelta(weeks=10)
    changed = perturb_the_future(ds, origin)
    assert (
        make_snapshot(compute_forecast(changed, later))["sha256"]
        != make_snapshot(compute_forecast(ds, later))["sha256"]
    )


def test_snapshots_at_different_origins_differ(ds):
    a = make_snapshot(compute_forecast(ds, ds.as_of))
    b = make_snapshot(compute_forecast(ds, ds.as_of - timedelta(weeks=1)))
    assert a["sha256"] != b["sha256"]
