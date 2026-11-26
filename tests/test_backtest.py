from __future__ import annotations

from datetime import timedelta

import pytest

from thirteenweek.backtest import origins, realised_receipts, run_backtest
from thirteenweek.models import HORIZON


@pytest.fixture(scope="module")
def scores(ds):
    return run_backtest(ds, ds.as_of, weeks=26)


def test_origins_are_26_weekly_dates_ending_13_weeks_before_the_last_data_date(ds):
    o = origins(ds.as_of, 26)
    assert len(o) == 26 and o[-1] == ds.as_of - timedelta(weeks=13)
    assert all((b - a).days == 7 for a, b in zip(o, o[1:], strict=False))


def test_pay_lag_beats_terms_on_wape_at_every_horizon_one_to_eight(scores):
    for s in scores[:8]:
        assert s.wape_lag < s.wape_terms, s.horizon


def test_terms_over_forecast_is_a_week_one_effect_and_comes_from_past_due_items(scores):
    # Per horizon, the terms method over-forecasts only in week 1 ...
    assert scores[0].bias_terms > 1.0
    assert all(s.bias_terms < 0 for s in scores[1:])
    # ... and that spike is past-due accumulation: leave past-due items out and it disappears.
    assert scores[0].bias_terms_ex < 0
    assert scores[0].wape_terms_ex < scores[0].wape_terms / 5
    for s in scores[1:]:
        assert s.bias_terms_ex == pytest.approx(s.bias_terms)


def test_cumulative_terms_bias_through_week_eight_is_above_ten_percent(scores):
    # A cumulative statement, not a per-horizon one: early over-forecast outweighs later misses.
    assert scores[7].cum_bias_terms > 0.10
    assert abs(scores[7].cum_bias_lag) < 0.05


def test_pay_lag_also_beats_the_terms_variant_without_past_due_items(scores):
    for s in scores[:8]:
        assert s.wape_lag < s.wape_terms_ex, s.horizon


def test_pay_lag_method_is_roughly_unbiased_early(scores):
    for s in scores[:4]:
        assert abs(s.bias_lag) < 0.10


def test_scores_cover_all_thirteen_horizons(scores):
    assert [s.horizon for s in scores] == list(range(1, HORIZON + 1))
    assert all(s.actual > 0 for s in scores)


def test_realised_receipts_count_only_items_open_at_the_origin(ds):
    origin = ds.as_of - timedelta(weeks=20)
    got = realised_receipts(ds, origin)
    known = ds.known_at(origin)
    open_ids = {a.app_id for a in known.apps if a.paid is None}
    horizon_end = origin + timedelta(days=91)
    expected = sum(
        a.net for a in ds.apps if a.app_id in open_ids and a.paid and a.paid <= horizon_end
    )
    held: dict[str, float] = {}
    for a in known.apps:
        held[a.job_id] = held.get(a.job_id, 0.0) + a.retainage
    released = {j.job_id: j.retainage_paid for j in ds.jobs}
    unreleased = {j.job_id for j in known.jobs if j.retainage_paid is None}
    expected += sum(
        round(v, 2)
        for job, v in held.items()
        if job in unreleased and released[job] and released[job] <= horizon_end
    )
    assert sum(got) == pytest.approx(expected)
    # an app billed after the origin never counts
    later = {a.app_id for a in ds.apps if a.billed > origin}
    assert later and not (later & open_ids)


def test_backtest_can_freeze_snapshots(ds, tmp_path):
    run_backtest(ds, ds.as_of, weeks=3, snapshot_dir=tmp_path)
    assert len(list(tmp_path.glob("*.json"))) == 3
    with pytest.raises(FileExistsError):
        run_backtest(ds, ds.as_of, weeks=3, snapshot_dir=tmp_path)
    run_backtest(ds, ds.as_of, weeks=3, snapshot_dir=tmp_path, force=True)
