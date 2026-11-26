"""Seeded generator for a synthetic electrical subcontractor.

Realistic properties
    * 14 general contractors, each paying with a lognormal lag (medians 32-75 days, spreads
      0.18-0.35) after a monthly pay application; 10% of every application is retained until the
      job closes out.
    * 30 jobs, roughly 560-620 pay applications, four crews on weekly certified payroll, about
      400 supplier invoices on net 30 / 45 terms, lower-tier subcontract shares of 15-35%.

Injected patterns (recorded in ``truth.json``)
    * GC ``G09`` (five jobs) pays 21 days later on every application billed in the final 26 weeks.
    * One application on job ``J17`` is disputed and never paid; it is far older than any lag
      observed for its GC.
    * Three jobs reach substantial completion inside the forecast horizon, so their retainage is
      released within 13 weeks.
    * A $1.2M switchgear invoice falls due in forecast week 6.
    * ``inputs.json`` carries the treasury inputs and a ``stress`` set (G09 four weeks later still,
      lower opening cash) that exhausts the revolver and breaches the liquidity covenant in week 6.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from .dataio import write_dataset
from .models import GC, Dataset, Inputs, Invoice, Job, PayApp, PayrollRow, Stress

AS_OF = date(2026, 3, 6)
G09_SHIFT_DAYS = 21
LAG_CAP_DAYS = 190
RETAINAGE_RATE = 0.10

# (gc_id, name, median lag days, lognormal sigma)
GC_TABLE = [
    ("G01", "Aldergate Builders", 38, 0.28),
    ("G02", "Brightwater Construction", 45, 0.22),
    ("G03", "Calloway & Reed", 52, 0.30),
    ("G04", "Dunmore Contracting", 61, 0.35),
    ("G05", "Eastfield Group", 34, 0.20),
    ("G06", "Fenwick Builders", 70, 0.33),
    ("G07", "Granite Ridge Construction", 58, 0.26),
    ("G08", "Hollis Brothers", 42, 0.30),
    ("G09", "Ironbridge Constructors", 49, 0.22),
    ("G10", "Juniper Development", 75, 0.35),
    ("G11", "Kestrel Construction", 55, 0.25),
    ("G12", "Larkspur Builders", 33, 0.18),
    ("G13", "Marlowe Group", 66, 0.32),
    ("G14", "Northgate Contractors", 47, 0.28),
]

# GC of J01..J30. G09 has five jobs; J17 (the disputed application) belongs to G05.
JOB_GCS = (
    "G01 G02 G03 G04 G05 G06 G07 G08 G09 G10 G11 G12 G13 G14 G01 G03 G05 G09 G09 G09 G09 "
    "G01 G02 G04 G06 G08 G10 G11 G03 G01"
).split()
COMPLETED_JOBS = ("J02", "J06", "J10", "J11", "J23", "J27")
HORIZON_JOBS = {"J04": 5, "J07": 18, "J25": 30}  # substantial completion, days after as-of
LATE_STARTS = {"J13": date(2025, 7, 14), "J14": date(2025, 11, 3)}
DISPUTED_JOB = "J17"

CREWS = (
    "Crew A - Commercial",
    "Crew B - Tenant Improvement",
    "Crew C - Service",
    "Crew D - Controls",
)
CREW_BASE = (88000.0, 76000.0, 54000.0, 48000.0)
SUPPLIERS = (
    "Halden Wire & Cable",
    "Brightmoor Lighting",
    "Ostrander Conduit",
    "Pelham Electrical Wholesale",
    "Quillon Panelboards",
    "Rowan Controls",
    "Stanton Fixtures",
    "Tallis Fasteners & Hardware",
)

BASE_INPUTS = Inputs(
    opening_cash=1_000_000.0,
    revolver_limit=1_600_000.0,
    revolver_outstanding=500_000.0,
    revolver_rate=0.0825,
    min_cash=750_000.0,
    min_liquidity=1_000_000.0,
)
STRESS = Stress(gc_id="G09", extra_weeks=4, opening_cash=250_000.0)


def _lag_days(rng: np.random.Generator, median: float, sigma: float) -> int:
    lag = rng.lognormal(math.log(median), sigma)
    return int(min(LAG_CAP_DAYS, max(3, round(lag))))


def _make_jobs(rng: np.random.Generator) -> list[Job]:
    jobs: list[Job] = []
    for idx, gc in enumerate(JOB_GCS, start=1):
        job_id = f"J{idx:02d}"
        contract = round(float(np.clip(rng.lognormal(math.log(4_300_000), 0.5), 1.5e6, 16e6)), -3)
        share = round(float(rng.uniform(0.15, 0.35)), 3)
        start = date(2024, 1, 15) + timedelta(days=int(rng.integers(0, 230)))
        if job_id in LATE_STARTS:
            start = LATE_STARTS[job_id]
        if job_id == DISPUTED_JOB:
            start = date(2024, 6, 3)
        if job_id in COMPLETED_JOBS:
            sc = AS_OF - timedelta(days=int(rng.integers(30, 230)))
        elif job_id in HORIZON_JOBS:
            sc = AS_OF + timedelta(days=HORIZON_JOBS[job_id])
        else:
            sc = AS_OF + timedelta(days=int(rng.integers(130, 520)))
        released: date | None = None
        if sc <= AS_OF:
            candidate = sc + timedelta(days=int(rng.integers(25, 56)))
            released = candidate if candidate <= AS_OF else None
        jobs.append(Job(job_id, gc, contract, start, sc, share, released))
    return jobs


def _make_apps(rng: np.random.Generator, jobs: list[Job]) -> tuple[list[PayApp], dict]:
    gc_params = {g[0]: (g[2], g[3]) for g in GC_TABLE}
    apps: list[PayApp] = []
    n = 0
    for job in jobs:
        span = (job.substantial_completion - job.start_date).days
        months = max(1, round(span / 30.4))
        k = np.arange(1, months + 1)
        weights = np.sin(np.pi * (k - 0.5) / months) ** 1.2 + 0.2
        weights = weights / weights.sum()
        for i in range(months):
            if i == months - 1:
                billed = job.substantial_completion
            else:
                billed = job.start_date + timedelta(
                    days=min(span, round(30.4 * (i + 1))) + int(rng.integers(-3, 4))
                )
            if billed > AS_OF:
                break
            gross = round(job.contract_value * float(weights[i]) * rng.uniform(0.92, 1.08), 2)
            median, sigma = gc_params[job.gc_id]
            lag = _lag_days(rng, median, sigma)
            if job.gc_id == "G09" and billed > AS_OF - timedelta(days=26 * 7):
                lag += G09_SHIFT_DAYS
            paid = billed + timedelta(days=lag)
            n += 1
            apps.append(
                PayApp(
                    f"PA-{n:04d}",
                    job.job_id,
                    billed,
                    gross,
                    round(gross * RETAINAGE_RATE, 2),
                    paid if paid <= AS_OF else None,
                )
            )
    # The disputed application: the J17 pay app closest to mid-November 2024, never paid.
    target = date(2024, 11, 15)
    j17 = [a for a in apps if a.job_id == DISPUTED_JOB]
    disputed = min(j17, key=lambda a: abs((a.billed - target).days))
    apps = [
        PayApp(a.app_id, a.job_id, a.billed, a.gross, a.retainage, None)
        if a.app_id == disputed.app_id
        else a
        for a in apps
    ]
    return apps, {
        "app_id": disputed.app_id,
        "job_id": disputed.job_id,
        "billed_date": disputed.billed.isoformat(),
        "gross": disputed.gross,
        "retainage_held": disputed.retainage,
        "net": disputed.net,
    }


def _make_payroll(rng: np.random.Generator) -> list[PayrollRow]:
    rows: list[PayrollRow] = []
    for w in range(-110, 16):
        week_ending = AS_OF + timedelta(days=7 * w)
        for crew, base in zip(CREWS, CREW_BASE, strict=True):
            rows.append(
                PayrollRow(week_ending, crew, round(base * float(rng.uniform(0.92, 1.08)), 2))
            )
    return rows


def _make_invoices(rng: np.random.Generator) -> list[Invoice]:
    raw = []
    for _ in range(400):
        inv_date = AS_OF - timedelta(days=int(rng.integers(0, 690)))
        terms = 30 if rng.random() < 0.55 else 45
        supplier = SUPPLIERS[int(rng.integers(0, len(SUPPLIERS)))]
        amount = round(float(np.clip(rng.lognormal(math.log(52_000), 0.9), 1_500, 600_000)), 2)
        due = inv_date + timedelta(days=terms)
        paid = due + timedelta(days=int(rng.integers(-3, 10)))
        raw.append((inv_date, supplier, due, amount, paid if paid <= AS_OF else None))
    raw.sort(key=lambda r: (r[0], r[1]))
    invoices = [
        Invoice(f"INV-{i:04d}", s, d, due, amt, paid)
        for i, (d, s, due, amt, paid) in enumerate(raw, start=1)
    ]
    invoices.append(
        Invoice(
            "INV-9001",
            "Corvane Switchgear",
            AS_OF - timedelta(days=7),
            AS_OF + timedelta(days=38),
            1_200_000.0,
            None,
        )
    )
    return invoices


def generate(seed: int = 7) -> tuple[Dataset, dict]:
    rng = np.random.default_rng(seed)
    gcs = tuple(GC(g[0], g[1], 30) for g in GC_TABLE)
    jobs = _make_jobs(rng)
    apps, disputed = _make_apps(rng, jobs)
    payroll = _make_payroll(rng)
    invoices = _make_invoices(rng)
    ds = Dataset(
        gcs=gcs,
        jobs=tuple(jobs),
        apps=tuple(apps),
        invoices=tuple(invoices),
        payroll=tuple(payroll),
        inputs=BASE_INPUTS,
        stress=STRESS,
        as_of=AS_OF,
    )
    truth = {
        "seed": seed,
        "as_of": AS_OF.isoformat(),
        "counts": {
            "gcs": len(gcs),
            "jobs": len(jobs),
            "pay_apps": len(apps),
            "invoices": len(invoices),
        },
        "g09_lag_shift": {
            "gc_id": "G09",
            "extra_days": G09_SHIFT_DAYS,
            "applies_to_apps_billed_after": (AS_OF - timedelta(days=26 * 7)).isoformat(),
        },
        "disputed_app": disputed,
        "retainage_release_jobs": {
            j: (AS_OF + timedelta(days=d)).isoformat() for j, d in HORIZON_JOBS.items()
        },
        "switchgear_invoice": {
            "invoice_id": "INV-9001",
            "amount": 1_200_000.0,
            "due_date": (AS_OF + timedelta(days=38)).isoformat(),
            "forecast_week": 6,
        },
        "stress": {
            "gc_id": STRESS.gc_id,
            "extra_weeks": STRESS.extra_weeks,
            "opening_cash": STRESS.opening_cash,
            "expected": "revolver exhausted and liquidity covenant breached in week 6",
        },
    }
    return ds, truth


def generate_to(seed: int, out: Path) -> dict:
    ds, truth = generate(seed)
    write_dataset(ds, out, truth)
    return truth
