"""Command line interface: generate, lags, build, verify, backtest, drift."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import typer

from .backtest import run_backtest
from .dataio import load_dataset
from .drift import MIN_SHIFT_DAYS, Q, run_drift
from .generate import generate_to
from .lags import build_model, pmf_median_bin
from .models import BEYOND, HORIZON, Dataset
from .reference import ForecastResult, compute_forecast, make_snapshot
from .snapshot import SnapshotExistsError, snapshot_name, write_snapshot
from .verify import verify_workbook
from .workbook import build_workbook

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="13-week direct cash forecast with pay-lag-conditioned receipts.",
)

DATA_OPT = typer.Option(Path("data"), "--data", help="Directory written by `generate`.")
ASOF_OPT = typer.Option(None, "--as-of", help="As-of date, YYYY-MM-DD (default: the data's own).")


def _as_of(value: str | None, ds: Dataset) -> date:
    if value is None:
        return ds.as_of
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"not an ISO date: {value}") from exc


def _load(data: Path) -> Dataset:
    try:
        return load_dataset(data)
    except FileNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc


def summary_table(res: ForecastResult) -> str:
    cols = (
        ("receipts", "Receipts"),
        ("disbursements", "Disburse"),
        ("net_flow", "Net"),
        ("draw", "Draw"),
        ("repay", "Repay"),
        ("ending_cash", "End cash"),
        ("liquidity", "Liquidity"),
    )
    head = (
        f"{'Wk':>2}  {'Ending':<10}" + "".join(f"{label:>12}" for _, label in cols) + "  Covenant"
    )
    lines = [head]
    for k in range(HORIZON):
        row = f"{k + 1:>2}  {res.week_end(k + 1).isoformat():<10}"
        row += "".join(f"{res.lines[key][k]:>12,.0f}" for key, _ in cols)
        row += "  " + ("BREACH" if res.lines["covenant_breach"][k] else "ok")
        lines.append(row)
    return "\n".join(lines)


@app.command()
def generate(
    seed: int = typer.Option(7, help="Random seed."),
    out: Path = typer.Option(Path("data"), help="Output directory."),
) -> None:
    """Write the synthetic dataset (CSV files, inputs.json, truth.json)."""
    truth = generate_to(seed, out)
    c = truth["counts"]
    typer.echo(
        f"wrote {out}/: {c['gcs']} GCs, {c['jobs']} jobs, {c['pay_apps']} pay apps, "
        f"{c['invoices']} supplier invoices (seed {seed})"
    )


@app.command()
def lags(
    as_of: str | None = ASOF_OPT,
    data: Path = DATA_OPT,
    stress: bool = typer.Option(False, "--stress", help="Apply the stress lag shift."),
) -> None:
    """Per-GC weekly pay-lag distributions from payments known at the as-of date."""
    ds = _load(data)
    when = _as_of(as_of, ds)
    model = (
        build_model(ds, when, ds.stress.gc_id, ds.stress.extra_weeks)
        if stress
        else build_model(ds, when)
    )
    typer.echo(f"pay-lag PMFs as of {when} (weekly bins 0-30 plus beyond)")
    typer.echo(
        f"{'GC':<8}{'paid':>5}{'open':>5}{'weight':>8}{'median wk':>10}{'P(<=4w)':>9}"
        f"{'P(<=8w)':>9}{'P(<=12w)':>10}{'beyond':>8}"
    )
    for gc in [*model.gc_ids, "POOLED"]:
        p = model.pmf[gc]
        w = "" if gc == "POOLED" else f"{model.weight[gc]:.2f}"
        typer.echo(
            f"{gc:<8}{model.n_paid[gc]:>5}{model.n_open[gc]:>5}{w:>8}{pmf_median_bin(p):>10}"
            f"{p[:5].sum():>9.2f}{p[:9].sum():>9.2f}{p[:13].sum():>10.2f}{p[BEYOND]:>8.3f}"
        )
    typer.echo(
        "weight = share of the GC's own PMF; GCs with fewer than 8 paid apps are shrunk "
        "toward POOLED"
    )


@app.command()
def build(
    as_of: str | None = ASOF_OPT,
    data: Path = DATA_OPT,
    out: Path = typer.Option(Path("out/cash13.xlsx"), help="Workbook to write."),
    snapshots: Path = typer.Option(Path("snapshots"), help="Directory for frozen snapshots."),
    stress: bool = typer.Option(False, "--stress", help="Use the stress input set."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing snapshot."),
) -> None:
    """Write the live-formula workbook and freeze a snapshot of the forecast."""
    ds = _load(data)
    when = _as_of(as_of, ds)
    res = compute_forecast(ds, when, stress=stress)
    snap = make_snapshot(res)
    target = snapshots / snapshot_name(snap)
    if target.exists() and not force:
        typer.echo(f"error: {target} is frozen; pass --force to replace it", err=True)
        raise typer.Exit(1)
    build_workbook(res, out)
    try:
        path = write_snapshot(snap, snapshots, force=force)
    except SnapshotExistsError as exc:  # pragma: no cover - guarded above
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"workbook  {out}")
    typer.echo(f"snapshot  {path}  sha256 {snap['sha256'][:16]}")
    typer.echo(f"scenario {res.scenario}, as of {when}")
    typer.echo(
        f"open items {len(res.items)} (+{len(res.disputed)} disputed, "
        f"${res.disputed_total:,.0f} excluded); expected receipts after week 13 "
        f"${res.receipts_after:,.0f}\n"
    )
    typer.echo(summary_table(res))


@app.command()
def verify(
    workbook: Path = typer.Argument(..., help="Workbook written by `build`."),
    data: Path = DATA_OPT,
) -> None:
    """Recalculate the workbook and compare every Forecast cell to the Python reference."""
    ds = _load(data)
    report = verify_workbook(workbook, ds)
    typer.echo(
        f"{report.scenario} scenario as of {report.as_of}: compared {report.cells_compared} "
        f"Forecast cells, largest difference ${report.max_diff:,.4f}"
    )
    for d in report.differences[:20]:
        typer.echo(
            f"  DIFF {d.line} week {d.week}: workbook {d.workbook:,.2f} "
            f"reference {d.reference:,.2f}"
        )
    for sheet, ref, value in report.errors[:20]:
        typer.echo(f"  ERROR {sheet}!{ref} = {value}")
    typer.echo(f"Checks sheet: {'all pass' if report.checks_pass else 'FAILING'}")
    if not report.ok:
        raise typer.Exit(1)
    typer.echo("OK: workbook matches the reference within $1")


@app.command()
def backtest(
    as_of: str | None = ASOF_OPT,
    data: Path = DATA_OPT,
    weeks: int = typer.Option(26, help="Number of weekly origins."),
    snapshots: Path | None = typer.Option(None, help="Also freeze each origin's snapshot here."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing snapshots."),
) -> None:
    """WAPE and bias of weekly receipts by horizon: pay-lag method vs contract terms."""
    ds = _load(data)
    when = _as_of(as_of, ds)
    try:
        scores = run_backtest(ds, when, weeks, snapshots, force)
    except SnapshotExistsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"{weeks} weekly origins, the last {HORIZON} weeks before {when}")
    typer.echo(
        f"{'Horizon':>7}{'Actual $k':>11}{'WAPE lag':>10}{'Bias lag':>10}"
        f"{'WAPE terms':>12}{'Bias terms':>12}{'WAPE t-ex':>11}{'Bias t-ex':>11}"
    )
    for s in scores:
        typer.echo(
            f"{s.horizon:>7}{s.actual / 1e3:>11,.0f}{s.wape_lag:>10.1%}{s.bias_lag:>+10.1%}"
            f"{s.wape_terms:>12.1%}{s.bias_terms:>+12.1%}{s.wape_terms_ex:>11.1%}"
            f"{s.bias_terms_ex:>+11.1%}"
        )
    typer.echo(
        "t-ex = contract terms with past-due items left out; cumulative bias through "
        f"horizon 8: lag {scores[7].cum_bias_lag:+.1%}, terms {scores[7].cum_bias_terms:+.1%}"
    )


@app.command()
def drift(as_of: str | None = ASOF_OPT, data: Path = DATA_OPT) -> None:
    """Which GCs have started paying later? Mann-Whitney per GC, BH-adjusted."""
    ds = _load(data)
    when = _as_of(as_of, ds)
    rows = run_drift(ds, when)
    typer.echo(
        f"pay-lag drift as of {when}: apps billed 13-26 weeks ago vs 27-78 weeks ago "
        f"(one-sided Mann-Whitney, Benjamini-Hochberg q = {Q}, median shift >= "
        f"{MIN_SHIFT_DAYS:.0f} days)"
    )
    typer.echo(
        f"{'GC':<5}{'n recent':>9}{'n ref':>7}{'median recent':>15}{'median ref':>12}"
        f"{'shift (d)':>11}{'p':>10}{'p adj':>10}  flag"
    )
    for r in sorted(rows, key=lambda r: r.p_adjusted):
        typer.echo(
            f"{r.gc_id:<5}{r.n_recent:>9}{r.n_reference:>7}{r.median_recent:>15.1f}"
            f"{r.median_reference:>12.1f}{r.shift_days:>+11.1f}{r.p_value:>10.4f}"
            f"{r.p_adjusted:>10.4f}  {'DRIFT' if r.flagged else ''}"
        )
    flagged = [r.gc_id for r in rows if r.flagged]
    typer.echo(f"flagged: {', '.join(flagged) if flagged else 'none'}")


def main() -> None:  # pragma: no cover
    app()
