"""The 13-week forecast in plain Python: the reference the workbook is checked against."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, timedelta

from .lags import LagModel, build_model
from .models import HORIZON, Dataset, Inputs, Invoice
from .receipts import OpenItem, WeightedItem, build_open_items

LINES = (
    "beginning_cash",
    "receipts",
    "disbursements",
    "net_flow",
    "interest",
    "pre_revolver_cash",
    "draw",
    "repay",
    "ending_cash",
    "outstanding",
    "availability",
    "liquidity",
    "covenant_breach",
)
WEEKS_PER_YEAR = 52


@dataclass
class ForecastResult:
    as_of: date
    scenario: str
    inputs: Inputs
    model: LagModel
    items: list[WeightedItem]
    disputed: list[OpenItem]
    crews: list[str]
    payroll: dict[str, list[float]]  # crew -> weekly amount
    invoices: list[Invoice]  # open supplier invoices at the as-of date
    supplier: list[float]
    lower_tier: list[float]
    lines: dict[str, list[float]] = field(default_factory=dict)
    receipts_after: float = 0.0
    open_ar: float = 0.0  # net of retainage, every open pay app including disputed ones
    retainage_held: float = 0.0
    disputed_total: float = 0.0

    def week_end(self, k: int) -> date:
        return self.as_of + timedelta(days=7 * k)


def effective_inputs(ds: Dataset, stress: bool) -> Inputs:
    base = ds.inputs
    if not stress:
        return base
    return Inputs(
        opening_cash=ds.stress.opening_cash,
        revolver_limit=base.revolver_limit,
        revolver_outstanding=base.revolver_outstanding,
        revolver_rate=base.revolver_rate,
        min_cash=base.min_cash,
        min_liquidity=base.min_liquidity,
    )


def _revolver(inputs: Inputs, receipts: list[float], disb: list[float]) -> dict[str, list[float]]:
    lines: dict[str, list[float]] = {k: [] for k in LINES}
    cash = inputs.opening_cash
    outstanding = inputs.revolver_outstanding
    availability = inputs.revolver_limit - outstanding
    for k in range(HORIZON):
        net = receipts[k] - disb[k]
        interest = round(outstanding * inputs.revolver_rate / WEEKS_PER_YEAR, 2)
        pre = cash + net - interest
        draw = min(availability, max(0.0, inputs.min_cash - pre))
        repay = min(outstanding, max(0.0, pre - inputs.min_cash))
        ending = pre + draw - repay
        outstanding = outstanding + draw - repay
        availability = inputs.revolver_limit - outstanding
        liquidity = ending + availability
        for name, value in zip(
            LINES,
            (
                cash,
                receipts[k],
                disb[k],
                net,
                interest,
                pre,
                draw,
                repay,
                ending,
                outstanding,
                availability,
                liquidity,
                1.0 if liquidity < inputs.min_liquidity else 0.0,
            ),
            strict=True,
        ):
            lines[name].append(value)
        cash = ending
    return lines


def compute_forecast(ds: Dataset, as_of: date, stress: bool = False) -> ForecastResult:
    """Forecast from the data known at ``as_of`` (anything later in ``ds`` is ignored)."""
    known = ds.known_at(as_of)
    model = (
        build_model(known, as_of, ds.stress.gc_id, ds.stress.extra_weeks)
        if stress
        else build_model(known, as_of)
    )
    items, disputed = build_open_items(known, as_of, model)
    inputs = effective_inputs(ds, stress)

    receipts = [sum(w.weeks[k] for w in items) for k in range(HORIZON)]
    crews = sorted({p.crew for p in known.payroll})
    payroll = {c: [0.0] * HORIZON for c in crews}
    for p in known.payroll:
        k = (p.week_ending - as_of).days
        if 0 < k <= 7 * HORIZON:
            payroll[p.crew][(k - 1) // 7] += p.amount
    open_inv = sorted(
        (i for i in known.invoices if i.paid is None), key=lambda i: (i.due_date, i.invoice_id)
    )
    supplier = [0.0] * HORIZON
    for inv in open_inv:
        k = (inv.due_date - as_of).days
        if k <= 7 * HORIZON:
            supplier[max(0, (k - 1) // 7)] += inv.amount
    lower = [0.0] + [sum(w.item.share * w.weeks[k - 1] for w in items) for k in range(1, HORIZON)]
    disb = [sum(payroll[c][k] for c in crews) + supplier[k] + lower[k] for k in range(HORIZON)]

    held: dict[str, float] = {}
    for app in known.apps:
        held[app.job_id] = held.get(app.job_id, 0.0) + app.retainage
    unreleased = {j.job_id for j in known.jobs if j.retainage_paid is None}
    result = ForecastResult(
        as_of=as_of,
        scenario="stress" if stress else "base",
        inputs=inputs,
        model=model,
        items=items,
        disputed=disputed,
        crews=crews,
        payroll=payroll,
        invoices=open_inv,
        supplier=supplier,
        lower_tier=lower,
        lines=_revolver(inputs, receipts, disb),
        receipts_after=sum(w.after for w in items),
        open_ar=round(sum(a.net for a in known.apps if a.paid is None), 2),
        retainage_held=round(sum(v for j, v in held.items() if j in unreleased), 2),
        disputed_total=round(sum(d.amount for d in disputed), 2),
    )
    return result


def snapshot_payload(res: ForecastResult) -> dict:
    """Everything a frozen snapshot records, before the hash is added."""
    return {
        "as_of": res.as_of.isoformat(),
        "scenario": res.scenario,
        "inputs": dict(res.inputs.__dict__),
        "open_items": [
            {"id": w.item.item_id, "kind": w.item.kind, "amount": w.item.amount} for w in res.items
        ],
        "disputed": [{"id": d.item_id, "amount": d.amount} for d in res.disputed],
        "receipts_after_week_13": round(res.receipts_after, 2),
        "forecast": {k: [round(v, 2) for v in vals] for k, vals in res.lines.items()},
    }


def snapshot_hash(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def make_snapshot(res: ForecastResult) -> dict:
    payload = snapshot_payload(res)
    return {**payload, "sha256": snapshot_hash(payload)}
