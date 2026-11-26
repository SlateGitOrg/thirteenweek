"""Reading and writing the CSV / JSON dataset."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from .models import GC, Dataset, Inputs, Invoice, Job, PayApp, PayrollRow, Stress


def _date(v: object) -> date | None:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v == "":
        return None
    return date.fromisoformat(str(v))


def _iso(d: date | None) -> str:
    return "" if d is None else d.isoformat()


def write_dataset(ds: Dataset, out: Path, truth: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([g.__dict__ for g in ds.gcs]).to_csv(out / "gcs.csv", index=False)
    pd.DataFrame(
        [
            {
                "job_id": j.job_id,
                "gc_id": j.gc_id,
                "contract_value": j.contract_value,
                "start_date": _iso(j.start_date),
                "substantial_completion": _iso(j.substantial_completion),
                "lower_tier_share": j.lower_tier_share,
                "retainage_paid_date": _iso(j.retainage_paid),
            }
            for j in ds.jobs
        ]
    ).to_csv(out / "jobs.csv", index=False)
    pd.DataFrame(
        [
            {
                "app_id": a.app_id,
                "job_id": a.job_id,
                "billed_date": _iso(a.billed),
                "gross": a.gross,
                "retainage_held": a.retainage,
                "paid_date": _iso(a.paid),
            }
            for a in ds.apps
        ]
    ).to_csv(out / "pay_apps.csv", index=False)
    pd.DataFrame(
        [
            {"week_ending": _iso(p.week_ending), "crew": p.crew, "amount": p.amount}
            for p in ds.payroll
        ]
    ).to_csv(out / "payroll_plan.csv", index=False)
    pd.DataFrame(
        [
            {
                "invoice_id": i.invoice_id,
                "supplier": i.supplier,
                "invoice_date": _iso(i.invoice_date),
                "due_date": _iso(i.due_date),
                "amount": i.amount,
                "paid_date": _iso(i.paid),
            }
            for i in ds.invoices
        ]
    ).to_csv(out / "ap_open.csv", index=False)
    (out / "inputs.json").write_text(
        json.dumps(
            {
                "as_of": ds.as_of.isoformat(),
                "base": ds.inputs.__dict__,
                "stress": ds.stress.__dict__,
            },
            indent=2,
        )
        + "\n"
    )
    (out / "truth.json").write_text(json.dumps(truth, indent=2) + "\n")


def load_dataset(path: Path) -> Dataset:
    path = Path(path)
    if not (path / "pay_apps.csv").exists():
        raise FileNotFoundError(f"no dataset in {path}; run `thirteenweek generate` first")
    gcs = pd.read_csv(path / "gcs.csv")
    jobs = pd.read_csv(path / "jobs.csv")
    apps = pd.read_csv(path / "pay_apps.csv")
    payroll = pd.read_csv(path / "payroll_plan.csv")
    ap = pd.read_csv(path / "ap_open.csv")
    meta = json.loads((path / "inputs.json").read_text())
    return Dataset(
        gcs=tuple(GC(r.gc_id, r.name, int(r.terms_days)) for r in gcs.itertuples()),
        jobs=tuple(
            Job(
                r.job_id,
                r.gc_id,
                float(r.contract_value),
                _date(r.start_date),
                _date(r.substantial_completion),
                float(r.lower_tier_share),
                _date(r.retainage_paid_date),
            )
            for r in jobs.itertuples()
        ),
        apps=tuple(
            PayApp(
                r.app_id,
                r.job_id,
                _date(r.billed_date),
                float(r.gross),
                float(r.retainage_held),
                _date(r.paid_date),
            )
            for r in apps.itertuples()
        ),
        invoices=tuple(
            Invoice(
                r.invoice_id,
                r.supplier,
                _date(r.invoice_date),
                _date(r.due_date),
                float(r.amount),
                _date(r.paid_date),
            )
            for r in ap.itertuples()
        ),
        payroll=tuple(
            PayrollRow(_date(r.week_ending), r.crew, float(r.amount)) for r in payroll.itertuples()
        ),
        inputs=Inputs(**meta["base"]),
        stress=Stress(**meta["stress"]),
        as_of=date.fromisoformat(meta["as_of"]),
    )
