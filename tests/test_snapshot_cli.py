from __future__ import annotations

import json
from pathlib import Path

import pytest
from openpyxl import load_workbook
from typer.testing import CliRunner

from thirteenweek.cli import app
from thirteenweek.reference import snapshot_hash
from thirteenweek.snapshot import verify_snapshot

runner = CliRunner()


@pytest.fixture(scope="module")
def workdir(tmp_path_factory):
    d = tmp_path_factory.mktemp("cli")
    r = runner.invoke(app, ["generate", "--seed", "7", "--out", str(d / "data")])
    assert r.exit_code == 0, r.output
    return d


def build_args(d: Path, *extra: str) -> list[str]:
    return [
        "build",
        "--as-of",
        "2026-03-06",
        "--data",
        str(d / "data"),
        "--out",
        str(d / "out" / "cash13.xlsx"),
        "--snapshots",
        str(d / "snaps"),
        *extra,
    ]


def test_build_writes_workbook_and_frozen_snapshot_and_refuses_overwrite(workdir):
    r = runner.invoke(app, build_args(workdir))
    assert r.exit_code == 0, r.output
    assert "Covenant" in r.output and "2026-04-17" in r.output
    snap = workdir / "snaps" / "2026-03-06.json"
    data = json.loads(snap.read_text())
    assert verify_snapshot(snap)
    assert data["sha256"] == snapshot_hash({k: v for k, v in data.items() if k != "sha256"})
    assert set(data["forecast"]) >= {"receipts", "disbursements", "draw", "liquidity"}
    again = runner.invoke(app, build_args(workdir))
    assert again.exit_code == 1 and "frozen" in again.output
    forced = runner.invoke(app, build_args(workdir, "--force"))
    assert forced.exit_code == 0


def test_tampering_with_a_snapshot_is_detected(workdir):
    runner.invoke(app, build_args(workdir, "--force"))
    snap = workdir / "snaps" / "2026-03-06.json"
    data = json.loads(snap.read_text())
    data["forecast"]["receipts"][0] += 1.0
    snap.write_text(json.dumps(data))
    assert not verify_snapshot(snap)
    runner.invoke(app, build_args(workdir, "--force"))


def test_verify_passes_on_a_fresh_workbook_and_fails_when_an_input_is_edited(workdir):
    book = workdir / "out" / "cash13.xlsx"
    ok = runner.invoke(app, ["verify", str(book), "--data", str(workdir / "data")])
    assert ok.exit_code == 0, ok.output
    assert "OK: workbook matches the reference" in ok.output
    wb = load_workbook(book)
    wb["Inputs"]["B2"].value = 5_000_000
    bad_path = workdir / "out" / "edited.xlsx"
    wb.save(bad_path)
    bad = runner.invoke(app, ["verify", str(bad_path), "--data", str(workdir / "data")])
    assert bad.exit_code == 1 and "DIFF" in bad.output


def test_stress_build_is_named_separately(workdir):
    r = runner.invoke(app, build_args(workdir, "--stress"))
    assert r.exit_code == 0, r.output
    assert (workdir / "snaps" / "2026-03-06-stress.json").exists()
    assert "BREACH" in r.output


def test_lags_backtest_and_drift_commands(workdir):
    data = str(workdir / "data")
    lags = runner.invoke(app, ["lags", "--as-of", "2026-03-06", "--data", data])
    assert lags.exit_code == 0 and "POOLED" in lags.output and "G09" in lags.output
    bt = runner.invoke(app, ["backtest", "--weeks", "4", "--data", data])
    assert bt.exit_code == 0 and "WAPE terms" in bt.output
    dr = runner.invoke(app, ["drift", "--as-of", "2026-03-06", "--data", data])
    assert dr.exit_code == 0 and "flagged: G09" in dr.output


def test_missing_data_directory_is_a_clean_error(tmp_path):
    r = runner.invoke(app, ["drift", "--data", str(tmp_path / "nope")])
    assert r.exit_code == 2


def test_bad_date_is_rejected(workdir):
    r = runner.invoke(app, ["lags", "--as-of", "03/06/2026", "--data", str(workdir / "data")])
    assert r.exit_code != 0
