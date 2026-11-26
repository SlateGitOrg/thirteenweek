from __future__ import annotations

import pytest

from thirteenweek.drift import benjamini_hochberg, run_drift


def test_benjamini_hochberg_known_values():
    adj = benjamini_hochberg([0.01, 0.04, 0.03, 0.005])
    assert adj == pytest.approx([0.02, 0.04, 0.04, 0.02])


def test_benjamini_hochberg_is_capped_and_monotone():
    adj = benjamini_hochberg([0.9, 0.95, 0.99])
    assert max(adj) <= 1.0 and adj == pytest.approx([0.99, 0.99, 0.99])


def test_drift_flags_g09_and_no_other_gc(ds):
    rows = run_drift(ds, ds.as_of)
    assert [r.gc_id for r in rows if r.flagged] == ["G09"]
    g09 = next(r for r in rows if r.gc_id == "G09")
    assert g09.shift_days == pytest.approx(21, abs=5)
    assert g09.p_adjusted < 0.001


def test_median_shift_floor_blocks_small_shifts(ds):
    rows = run_drift(ds, ds.as_of)
    for r in rows:
        if r.flagged:
            assert r.shift_days >= 10 and r.p_adjusted <= 0.05
        else:
            assert r.p_adjusted > 0.05 or r.shift_days < 10


def test_drift_needs_data_in_both_groups(ds):
    rows = run_drift(ds, ds.as_of)
    assert all(r.n_recent >= 5 and r.n_reference >= 5 for r in rows)
    assert len(rows) < 14  # GCs with too few apps are not tested
