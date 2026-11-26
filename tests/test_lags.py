from __future__ import annotations

import numpy as np
import pytest

from thirteenweek.lags import (
    LagModel,
    build_model,
    estimate_pmf,
    lag_bin,
    shift_pmf,
    survival,
)
from thirteenweek.models import BEYOND, N_BINS
from thirteenweek.receipts import pay_app_weights


def fixture_model() -> LagModel:
    pmf = np.zeros(N_BINS)
    pmf[:4] = [0.1, 0.2, 0.3, 0.4]
    return LagModel(["X"], {"X": pmf, "POOLED": pmf}, {"X": 9}, {"X": 0}, {"X": 1.0})


def test_survival_fixture_matches_hand_computed_weights():
    weights, after = pay_app_weights(fixture_model(), "X", age=1, amount=700.0)
    assert weights[0] == pytest.approx(300.0)  # 3/7
    assert weights[1] == pytest.approx(400.0)  # 4/7
    assert all(w == 0.0 for w in weights[2:])
    assert after == pytest.approx(0.0, abs=1e-9)


def test_app_older_than_the_support_has_no_weight():
    assert pay_app_weights(fixture_model(), "X", age=4, amount=100.0) is None


def test_survival_is_the_tail_sum():
    pmf = np.zeros(N_BINS)
    pmf[:4] = [0.1, 0.2, 0.3, 0.4]
    s = survival(pmf)
    assert s[0] == pytest.approx(1.0) and s[2] == pytest.approx(0.7) and s[4] == 0.0


def test_lag_bins_round_up_to_whole_weeks():
    assert [lag_bin(d) for d in (0, 1, 7, 8, 14, 15)] == [0, 1, 1, 2, 2, 3]
    assert lag_bin(10_000) == BEYOND


def test_kaplan_meier_uses_open_apps_as_censored_observations():
    # two paid at bin 1 and 3, one unpaid aged 1 week: it is at risk in bins 0 and 1 only
    pmf = estimate_pmf([1, 3], [1])
    assert pmf is not None
    # hazard bin 1 = 1/3 (three at risk), then the remaining mass 2/3 is all at bin 3 (last paid)
    assert pmf[1] == pytest.approx(1 / 3)
    assert pmf[3] == pytest.approx(2 / 3)
    assert pmf.sum() == pytest.approx(1.0)
    assert estimate_pmf([], [3]) is None


def test_censoring_moves_mass_to_longer_lags():
    naive = estimate_pmf([1, 1, 2], [])
    censored = estimate_pmf([1, 1, 2], [5, 5, 5])
    assert censored[1] < naive[1] and censored[2] > naive[2]


def test_shift_pmf_pushes_mass_into_beyond():
    pmf = np.zeros(N_BINS)
    pmf[2] = 0.5
    pmf[29] = 0.5
    shifted = shift_pmf(pmf, 4)
    assert shifted[6] == 0.5 and shifted[BEYOND] == 0.5 and shifted.sum() == pytest.approx(1.0)


def test_small_gcs_are_shrunk_toward_the_pooled_pmf(ds):
    model = build_model(ds, ds.as_of)
    for gc in model.gc_ids:
        n = model.n_paid[gc]
        expected = 1.0 if n >= 8 else n / (n + 8)
        assert model.weight[gc] == pytest.approx(expected)
    small = [g for g in model.gc_ids if model.n_paid[g] < 8]
    assert small, "the seeded data should contain GCs below the shrinkage threshold"
    gc = small[0]
    jobs = {j.job_id for j in ds.jobs if j.gc_id == gc}
    apps = [a for a in ds.apps if a.job_id in jobs]
    paid = [lag_bin((a.paid - a.billed).days) for a in apps if a.paid]
    open_ages = [(ds.as_of - a.billed).days // 7 for a in apps if not a.paid]
    own = estimate_pmf(paid, open_ages)
    w = len(paid) / (len(paid) + 8)
    expected = w * own + (1 - w) * model.pmf["POOLED"]
    assert model.pmf[gc] == pytest.approx(expected)


def test_every_pmf_sums_to_one(ds):
    model = build_model(ds, ds.as_of)
    for pmf in model.pmf.values():
        assert pmf.sum() == pytest.approx(1.0)


def test_g09_median_lag_is_longer_with_the_stress_shift(ds):
    plain = build_model(ds, ds.as_of)
    stressed = build_model(ds, ds.as_of, "G09", 4)
    mean = lambda p: float(np.dot(np.arange(N_BINS), p))  # noqa: E731
    assert mean(stressed.pmf["G09"]) == pytest.approx(mean(plain.pmf["G09"]) + 4, abs=1e-6)
    assert stressed.pmf["G01"].tolist() == plain.pmf["G01"].tolist()
