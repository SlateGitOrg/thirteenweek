from __future__ import annotations

import json
from datetime import date, timedelta

from thirteenweek.dataio import load_dataset
from thirteenweek.generate import generate, generate_to


def test_dataset_has_the_documented_shape(ds, truth):
    assert len(ds.gcs) == 14
    assert len(ds.jobs) == 30
    assert 550 <= len(ds.apps) <= 650
    assert 380 <= len(ds.invoices) <= 420
    assert {p.crew for p in ds.payroll} and len({p.crew for p in ds.payroll}) == 4
    assert truth["counts"]["pay_apps"] == len(ds.apps)
    assert all(abs(a.retainage - round(a.gross * 0.10, 2)) < 0.011 for a in ds.apps)
    shares = [j.lower_tier_share for j in ds.jobs]
    assert 0.15 <= min(shares) and max(shares) <= 0.35


def test_generation_is_deterministic_and_seed_sensitive():
    a, _ = generate(7)
    b, _ = generate(7)
    c, _ = generate(8)
    assert a == b
    assert a.apps != c.apps


def test_csv_round_trip(tmp_path, ds):
    truth = generate_to(7, tmp_path)
    loaded = load_dataset(tmp_path)
    assert loaded == ds
    assert json.loads((tmp_path / "truth.json").read_text()) == truth
    for name in ("gcs", "jobs", "pay_apps", "payroll_plan", "ap_open"):
        assert (tmp_path / f"{name}.csv").exists()


def test_g09_has_enough_active_jobs_and_a_late_shift(ds):
    jobs = [j for j in ds.jobs if j.gc_id == "G09"]
    assert len(jobs) >= 4
    assert all(j.substantial_completion > ds.as_of for j in jobs)


def test_disputed_job17_app_is_unpaid_and_old(ds, truth):
    d = truth["disputed_app"]
    app = next(a for a in ds.apps if a.app_id == d["app_id"])
    assert app.job_id == "J17" and app.paid is None
    assert (ds.as_of - app.billed).days > 52 * 7
    other_j17_paid = [a for a in ds.apps if a.job_id == "J17" and a.paid]
    assert other_j17_paid


def test_three_jobs_complete_inside_the_horizon(ds, truth):
    horizon_end = ds.as_of + timedelta(days=91)
    inside = [j for j in ds.jobs if ds.as_of < j.substantial_completion <= horizon_end]
    assert {j.job_id for j in inside} == set(truth["retainage_release_jobs"])
    assert len(inside) == 3


def test_switchgear_invoice_falls_due_in_week_six(ds):
    inv = next(i for i in ds.invoices if i.invoice_id == "INV-9001")
    assert inv.amount == 1_200_000.0 and inv.paid is None
    week = -(-(inv.due_date - ds.as_of).days // 7)
    assert week == 6


def test_nothing_is_dated_after_the_as_of_date(ds):
    assert all(a.billed <= ds.as_of for a in ds.apps)
    assert all(a.paid is None or a.paid <= ds.as_of for a in ds.apps)
    assert all(i.invoice_date <= ds.as_of for i in ds.invoices)
    assert ds.as_of == date(2026, 3, 6)
