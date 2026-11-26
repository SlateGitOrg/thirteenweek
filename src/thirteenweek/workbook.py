"""Write the live-formula Excel workbook with openpyxl.

Sheets: Inputs, LagPMF (values), Survival (formulas), OpenAR (INDEX/MATCH formulas), Disputed
(values), Disbursements, Forecast, Checks. Only the functions in ``ALLOWED_FUNCTIONS`` are used.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter as col

from .models import BEYOND, HORIZON, N_BINS
from .reference import ForecastResult

ALLOWED_FUNCTIONS = frozenset(
    {"SUM", "SUMPRODUCT", "IF", "MIN", "MAX", "ROUND", "ABS", "INDEX", "MATCH", "AND", "OR"}
)
MONEY = "#,##0;[Red]-#,##0"
MONEY2 = "#,##0.00;[Red]-#,##0.00"
DATE_FMT = "yyyy-mm-dd"
HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
HEADER_FONT = Font(bold=True, color="FFFFFF")

# OpenAR column layout (1-based)
C_ID, C_KIND, C_JOB, C_GC, C_ANCHOR, C_AMT, C_AGE, C_ROW, C_S0 = range(1, 10)
C_WEEK1 = 10  # J
C_AFTER = C_WEEK1 + HORIZON  # W
C_DIFF = C_AFTER + 1  # X
C_SHARE = C_AFTER + 2  # Y


def serial(d: date) -> int:
    return (d - date(1899, 12, 30)).days


def _header(ws, row: int, values: list) -> None:
    for i, v in enumerate(values, start=1):
        c = ws.cell(row=row, column=i, value=v)
        c.fill = HEADER_FILL
        c.font = HEADER_FONT
        c.alignment = Alignment(horizontal="center")


def _inputs(wb: Workbook, res: ForecastResult) -> None:
    ws = wb.active
    ws.title = "Inputs"
    inp = res.inputs
    rows = [
        ("As-of date", serial(res.as_of), DATE_FMT),
        ("Opening cash", inp.opening_cash, MONEY),
        ("Revolver limit", inp.revolver_limit, MONEY),
        ("Revolver outstanding (opening)", inp.revolver_outstanding, MONEY),
        ("Revolver rate (annual)", inp.revolver_rate, "0.00%"),
        ("Minimum cash", inp.min_cash, MONEY),
        ("Minimum liquidity covenant", inp.min_liquidity, MONEY),
        ("Open AR, net of retainage (all open pay apps)", res.open_ar, MONEY2),
        ("Retainage held (unreleased jobs)", res.retainage_held, MONEY2),
        ("Disputed / aged items excluded", res.disputed_total, MONEY2),
        ("Scenario", res.scenario, None),
    ]
    for r, (label, value, fmt) in enumerate(rows, start=1):
        ws.cell(row=r, column=1, value=label).font = Font(bold=True)
        c = ws.cell(row=r, column=2, value=value)
        if fmt:
            c.number_format = fmt
        c.fill = INPUT_FILL
    if res.model.stressed:
        ws.cell(row=12, column=1, value="Stress: GC shifted later").font = Font(bold=True)
        ws.cell(row=12, column=2, value=res.model.stressed)
    ws.column_dimensions["A"].width = 46
    ws.column_dimensions["B"].width = 18


def _lag_sheets(wb: Workbook, res: ForecastResult) -> int:
    """LagPMF (values) and Survival (cumulative-sum formulas); returns the last data row."""
    pmf = wb.create_sheet("LagPMF")
    surv = wb.create_sheet("Survival")
    labels = [*res.model.gc_ids, "POOLED"]
    bins: list = [*range(N_BINS - 1), "beyond"]
    _header(pmf, 1, ["GC", *bins, "n paid", "sum"])
    _header(surv, 1, ["GC", *bins])
    last_col = col(N_BINS + 1)  # AG
    for r, gc in enumerate(labels, start=2):
        pmf.cell(row=r, column=1, value=gc)
        surv.cell(row=r, column=1, value=gc)
        for j in range(N_BINS):
            c = pmf.cell(row=r, column=2 + j, value=float(res.model.pmf[gc][j]))
            c.number_format = "0.0000"
            s = surv.cell(row=r, column=2 + j, value=f"=SUM(LagPMF!{col(2 + j)}{r}:${last_col}{r})")
            s.number_format = "0.0000"
        pmf.cell(row=r, column=N_BINS + 2, value=res.model.n_paid[gc])
        pmf.cell(row=r, column=N_BINS + 3, value=f"=SUM(B{r}:{last_col}{r})")
    pmf.freeze_panes = "B2"
    surv.freeze_panes = "B2"
    return len(labels) + 1


def _open_ar(wb: Workbook, res: ForecastResult, lag_last: int) -> int:
    ws = wb.create_sheet("OpenAR")
    headers = [
        "Item",
        "Kind",
        "Job",
        "GC",
        "Billed / completion",
        "Amount",
        "Age (wks) / release wk",
        "LagPMF row",
        "S(age+1)",
        *range(1, HORIZON + 1),
        "After wk 13",
        "|Sum - amount|",
        "Sub share",
    ]
    _header(ws, 1, headers)
    pmf_rng = f"LagPMF!$B$2:${col(N_BINS + 1)}${lag_last}"
    surv_rng = f"Survival!$B$2:${col(N_BINS + 1)}${lag_last}"
    gc_rng = f"LagPMF!$A$2:$A${lag_last}"
    for r, w in enumerate(res.items, start=2):
        it = w.item
        ws.cell(row=r, column=C_ID, value=it.item_id)
        ws.cell(row=r, column=C_KIND, value=it.kind)
        ws.cell(row=r, column=C_JOB, value=it.job_id)
        ws.cell(row=r, column=C_GC, value=it.gc_id)
        ws.cell(row=r, column=C_ANCHOR, value=serial(it.anchor)).number_format = DATE_FMT
        ws.cell(row=r, column=C_AMT, value=it.amount).number_format = MONEY2
        ws.cell(row=r, column=C_SHARE, value=it.share).number_format = "0.0%"
        a = f"$G{r}"
        if it.kind == "PayApp":
            ws.cell(row=r, column=C_AGE, value=it.age)
            ws.cell(row=r, column=C_ROW, value=f"=MATCH($D{r},{gc_rng},0)")
            ws.cell(row=r, column=C_S0, value=f"=INDEX({surv_rng},$H{r},MIN({a}+1,{BEYOND})+1)")
            for k in range(HORIZON):
                wk = f"{col(C_WEEK1 + k)}$1"
                ws.cell(
                    row=r,
                    column=C_WEEK1 + k,
                    value=(
                        f"=IF($I{r}=0,0,$F{r}*IF({a}+{wk}>{BEYOND - 1},0,"
                        f"INDEX({pmf_rng},$H{r},{a}+{wk}+1))/$I{r})"
                    ),
                )
            ws.cell(
                row=r,
                column=C_AFTER,
                value=(
                    f"=IF($I{r}=0,0,$F{r}*INDEX({surv_rng},$H{r},"
                    f"MIN({a}+{HORIZON + 1},{BEYOND})+1)/$I{r})"
                ),
            )
        else:
            ws.cell(row=r, column=C_AGE, value=it.target_week)
            for k in range(HORIZON):
                wk = f"{col(C_WEEK1 + k)}$1"
                ws.cell(row=r, column=C_WEEK1 + k, value=f"=IF({a}={wk},$F{r},0)")
            ws.cell(row=r, column=C_AFTER, value=f"=IF({a}>{HORIZON},$F{r},0)")
        ws.cell(
            row=r,
            column=C_DIFF,
            value=f"=ABS(SUM({col(C_WEEK1)}{r}:{col(C_AFTER)}{r})-$F{r})",
        )
        for c in range(C_WEEK1, C_AFTER + 1):
            ws.cell(row=r, column=c).number_format = MONEY
    ws.freeze_panes = "E2"
    ws.column_dimensions["A"].width = 14
    return max(2, len(res.items) + 1)


def _disputed(wb: Workbook, res: ForecastResult) -> None:
    ws = wb.create_sheet("Disputed")
    _header(ws, 1, ["Item", "Job", "GC", "Billed", "Net amount", "Age (wks)", "Reason"])
    for r, d in enumerate(res.disputed, start=2):
        ws.cell(row=r, column=1, value=d.item_id)
        ws.cell(row=r, column=2, value=d.job_id)
        ws.cell(row=r, column=3, value=d.gc_id)
        ws.cell(row=r, column=4, value=serial(d.anchor)).number_format = DATE_FMT
        ws.cell(row=r, column=5, value=d.amount).number_format = MONEY2
        ws.cell(row=r, column=6, value=d.age)
        ws.cell(row=r, column=7, value="older than any observed lag for its GC: S(age+1) = 0")


def _disbursements(wb: Workbook, res: ForecastResult, ar_last: int) -> dict[str, int]:
    ws = wb.create_sheet("Disbursements")
    _header(ws, 1, ["Week", *range(1, HORIZON + 1)])
    ws.cell(row=2, column=1, value="Week ending")
    ws.cell(row=3, column=1, value="Week start (exclusive)")
    for k in range(HORIZON):
        c = col(2 + k)
        ws.cell(row=2, column=2 + k, value=f"=Inputs!$B$1+7*{c}$1").number_format = DATE_FMT
        ws.cell(row=3, column=2 + k, value=f"={c}2-7").number_format = DATE_FMT
    r = 5
    first_crew = r
    for crew in res.crews:
        ws.cell(row=r, column=1, value=f"Payroll: {crew}")
        for k in range(HORIZON):
            ws.cell(row=r, column=2 + k, value=res.payroll[crew][k]).number_format = MONEY
        r += 1
    rows = {"payroll": r, "supplier": r + 1, "lower": r + 2, "total": r + 3}
    ws.cell(row=rows["payroll"], column=1, value="Payroll total")
    ws.cell(row=rows["supplier"], column=1, value="Supplier invoices due")
    ws.cell(row=rows["lower"], column=1, value="Lower-tier subcontractor payments")
    ws.cell(row=rows["total"], column=1, value="Total disbursements")
    inv_first = rows["total"] + 4
    inv_n = max(1, len(res.invoices))
    inv_last = inv_first + inv_n - 1
    due = f"$C${inv_first}:$C${inv_last}"
    amt = f"$D${inv_first}:$D${inv_last}"
    share = f"OpenAR!${col(C_SHARE)}$2:${col(C_SHARE)}${ar_last}"
    for k in range(HORIZON):
        c = col(2 + k)
        ws.cell(row=rows["payroll"], column=2 + k, value=f"=SUM({c}{first_crew}:{c}{r - 1})")
        if k == 0:
            sup = f"=SUMPRODUCT(({due}<={c}$2)*{amt})"
            low = 0
        else:
            sup = f"=SUMPRODUCT(({due}<={c}$2)*({due}>{c}$3)*{amt})"
            prev = col(C_WEEK1 + k - 1)
            low = f"=SUMPRODUCT({share},OpenAR!{prev}$2:{prev}${ar_last})"
        ws.cell(row=rows["supplier"], column=2 + k, value=sup)
        ws.cell(row=rows["lower"], column=2 + k, value=low)
        ws.cell(
            row=rows["total"],
            column=2 + k,
            value=f"={c}{rows['payroll']}+{c}{rows['supplier']}+{c}{rows['lower']}",
        )
        for key in rows:
            ws.cell(row=rows[key], column=2 + k).number_format = MONEY
    _header(ws, inv_first - 1, ["Invoice", "Supplier", "Due date", "Amount"])
    if res.invoices:
        for i, inv in enumerate(res.invoices):
            rr = inv_first + i
            ws.cell(row=rr, column=1, value=inv.invoice_id)
            ws.cell(row=rr, column=2, value=inv.supplier)
            ws.cell(row=rr, column=3, value=serial(inv.due_date)).number_format = DATE_FMT
            ws.cell(row=rr, column=4, value=inv.amount).number_format = MONEY2
    else:
        ws.cell(row=inv_first, column=3, value=0)
        ws.cell(row=inv_first, column=4, value=0)
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 26
    return rows


FORECAST_ROWS = {
    "week": 1,
    "week_ending": 2,
    "beginning_cash": 3,
    "receipts": 4,
    "disbursements": 5,
    "net_flow": 6,
    "interest": 7,
    "pre_revolver_cash": 8,
    "draw": 9,
    "repay": 10,
    "ending_cash": 11,
    "outstanding": 12,
    "availability": 13,
    "liquidity": 14,
    "covenant_breach": 15,
}
FORECAST_LABELS = {
    "week_ending": "Week ending",
    "beginning_cash": "Beginning cash",
    "receipts": "Receipts",
    "disbursements": "Disbursements",
    "net_flow": "Net operating flow",
    "interest": "Revolver interest (prior-week balance)",
    "pre_revolver_cash": "Pre-revolver cash",
    "draw": "Revolver draw",
    "repay": "Revolver repayment",
    "ending_cash": "Ending cash",
    "outstanding": "Revolver outstanding",
    "availability": "Revolver availability",
    "liquidity": "Liquidity (cash + availability)",
    "covenant_breach": "Covenant breach (TRUE = below minimum)",
}


def _forecast(wb: Workbook, ar_last: int, dis_rows: dict[str, int]) -> None:
    ws = wb.create_sheet("Forecast")
    _header(ws, 1, ["Week", *range(1, HORIZON + 1), "Total"])
    R = FORECAST_ROWS
    for key, label in FORECAST_LABELS.items():
        ws.cell(row=R[key], column=1, value=label)
    for k in range(HORIZON):
        c = col(2 + k)
        p = col(1 + k)
        ar = col(C_WEEK1 + k)
        first = k == 0
        ws.cell(
            row=R["week_ending"], column=2 + k, value=f"=Inputs!$B$1+7*{c}$1"
        ).number_format = DATE_FMT
        ws.cell(
            row=R["beginning_cash"],
            column=2 + k,
            value="=Inputs!$B$2" if first else f"={p}{R['ending_cash']}",
        )
        ws.cell(row=R["receipts"], column=2 + k, value=f"=SUM(OpenAR!{ar}$2:{ar}${ar_last})")
        ws.cell(
            row=R["disbursements"], column=2 + k, value=f"=Disbursements!{c}{dis_rows['total']}"
        )
        ws.cell(
            row=R["net_flow"], column=2 + k, value=f"={c}{R['receipts']}-{c}{R['disbursements']}"
        )
        prev_out = "Inputs!$B$4" if first else f"{p}{R['outstanding']}"
        prev_avail = "(Inputs!$B$3-Inputs!$B$4)" if first else f"{p}{R['availability']}"
        ws.cell(row=R["interest"], column=2 + k, value=f"=ROUND({prev_out}*Inputs!$B$5/52,2)")
        ws.cell(
            row=R["pre_revolver_cash"],
            column=2 + k,
            value=f"={c}{R['beginning_cash']}+{c}{R['net_flow']}-{c}{R['interest']}",
        )
        pre = f"{c}{R['pre_revolver_cash']}"
        ws.cell(row=R["draw"], column=2 + k, value=f"=MIN({prev_avail},MAX(0,Inputs!$B$6-{pre}))")
        ws.cell(row=R["repay"], column=2 + k, value=f"=MIN({prev_out},MAX(0,{pre}-Inputs!$B$6))")
        ws.cell(
            row=R["ending_cash"],
            column=2 + k,
            value=f"={pre}+{c}{R['draw']}-{c}{R['repay']}",
        )
        ws.cell(
            row=R["outstanding"],
            column=2 + k,
            value=f"={prev_out}+{c}{R['draw']}-{c}{R['repay']}",
        )
        ws.cell(row=R["availability"], column=2 + k, value=f"=Inputs!$B$3-{c}{R['outstanding']}")
        ws.cell(
            row=R["liquidity"], column=2 + k, value=f"={c}{R['ending_cash']}+{c}{R['availability']}"
        )
        ws.cell(row=R["covenant_breach"], column=2 + k, value=f"={c}{R['liquidity']}<Inputs!$B$7")
        for key in FORECAST_LABELS:
            if key not in ("week_ending", "covenant_breach"):
                ws.cell(row=R[key], column=2 + k).number_format = MONEY
    tot = col(HORIZON + 2)
    last = col(HORIZON + 1)
    for key in ("receipts", "disbursements", "net_flow", "interest", "draw", "repay"):
        ws.cell(
            row=R[key], column=HORIZON + 2, value=f"=SUM(B{R[key]}:{last}{R[key]})"
        ).number_format = MONEY
    ws.cell(row=17, column=1, value="Expected receipts after week 13 (memo)")
    ws.cell(
        row=17,
        column=2,
        value=f"=SUM(OpenAR!{col(C_AFTER)}$2:{col(C_AFTER)}${ar_last})",
    ).number_format = MONEY
    ws.cell(
        row=19,
        column=1,
        value=(
            "Receivables run-off with full payroll and AP and no future billings: draws after "
            "about week 8 are not meaningful."
        ),
    )
    ws.freeze_panes = "B2"
    ws.column_dimensions["A"].width = 42
    ws.column_dimensions[tot].width = 14


def _checks(wb: Workbook, ar_last: int, lag_last: int) -> None:
    ws = wb.create_sheet("Checks")
    _header(ws, 1, ["Check", "Pass"])
    F = FORECAST_ROWS
    diff = f"OpenAR!${col(C_DIFF)}$2:${col(C_DIFF)}${ar_last}"
    amt = f"OpenAR!$F$2:$F${ar_last}"
    weeks = f"OpenAR!${col(C_WEEK1)}$2:${col(C_AFTER)}${ar_last}"
    checks = [
        (
            "Every open item: weeks 1-13 plus after-week-13 equal the open amount",
            f"=MAX({diff})<0.01",
        ),
        (
            "Total of open items = open AR + retainage held - disputed",
            f"=ABS(SUM({amt})-(Inputs!$B$8+Inputs!$B$9-Inputs!$B$10))<0.01",
        ),
        (
            "Receipts schedule conserves open items",
            f"=ABS(Forecast!$O${F['receipts']}+Forecast!$B$17-SUM({amt}))<0.01",
        ),
        (
            "Every PMF sums to one",
            f"=AND(MAX(LagPMF!${col(N_BINS + 3)}$2:${col(N_BINS + 3)}${lag_last})<1.000001,"
            f"MIN(LagPMF!${col(N_BINS + 3)}$2:${col(N_BINS + 3)}${lag_last})>0.999999)",
        ),
        ("No negative expected receipt", f"=MIN({weeks})>=0"),
        (
            "Revolver outstanding never exceeds the limit",
            f"=MAX(Forecast!$B${F['outstanding']}:$N${F['outstanding']})<=Inputs!$B$3+0.005",
        ),
        (
            "Ending cash is at least minimum cash whenever availability remains",
            f"=SUMPRODUCT((Forecast!$B${F['ending_cash']}:$N${F['ending_cash']}<Inputs!$B$6-0.005)"
            f"*(Forecast!$B${F['availability']}:$N${F['availability']}>0.005))=0",
        ),
    ]
    for r, (label, formula) in enumerate(checks, start=2):
        ws.cell(row=r, column=1, value=label)
        ws.cell(row=r, column=2, value=formula)
    last = len(checks) + 1
    ws.cell(row=last + 2, column=1, value="All checks pass").font = Font(bold=True)
    ws.cell(row=last + 2, column=2, value=f"=AND(B2:B{last})")
    ws.column_dimensions["A"].width = 70


def build_workbook(res: ForecastResult, path: Path) -> Path:
    wb = Workbook()
    _inputs(wb, res)
    lag_last = _lag_sheets(wb, res)
    ar_last = _open_ar(wb, res, lag_last)
    _disputed(wb, res)
    dis_rows = _disbursements(wb, res, ar_last)
    _forecast(wb, ar_last, dis_rows)
    _checks(wb, ar_last, lag_last)
    wb.calculation.fullCalcOnLoad = True
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
