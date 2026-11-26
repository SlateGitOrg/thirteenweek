from __future__ import annotations

import pytest

from thirteenweek.models import HORIZON


def test_base_case_draws_in_week_six_and_not_before(base):
    draw = base.lines["draw"]
    assert all(d == 0 for d in draw[:5])
    assert draw[5] > 0
    assert base.lines["pre_revolver_cash"][5] < base.inputs.min_cash
    assert min(base.lines["pre_revolver_cash"][:5]) >= base.inputs.min_cash


def test_switchgear_invoice_is_in_the_week_six_supplier_bucket(base):
    assert base.supplier[5] >= 1_200_000
    assert sum(base.supplier[:5]) < base.supplier[5]


@pytest.mark.parametrize("scenario", ["base", "stress"])
def test_revolver_invariants(scenario, base, stress):
    res = base if scenario == "base" else stress
    lim, mn = res.inputs.revolver_limit, res.inputs.min_cash
    for k in range(HORIZON):
        assert res.lines["outstanding"][k] <= lim + 1e-6
        assert res.lines["outstanding"][k] >= -1e-6
        if res.lines["availability"][k] > 0.005:
            assert res.lines["ending_cash"][k] >= mn - 0.005
        assert res.lines["draw"][k] == 0 or res.lines["repay"][k] == 0


def test_interest_accrues_on_the_prior_week_balance(base, stress):
    r = stress.inputs.revolver_rate / 52
    assert stress.lines["interest"][0] == pytest.approx(
        round(stress.inputs.revolver_outstanding * r, 2)
    )
    for k in range(1, HORIZON):
        assert stress.lines["interest"][k] == pytest.approx(
            round(stress.lines["outstanding"][k - 1] * r, 2)
        )


def test_cash_rolls_forward(base):
    for k in range(1, HORIZON):
        assert base.lines["beginning_cash"][k] == base.lines["ending_cash"][k - 1]
    assert base.lines["beginning_cash"][0] == base.inputs.opening_cash


def test_stress_breaches_the_covenant_in_week_six_only_among_weeks_one_to_five(stress):
    flags = stress.lines["covenant_breach"]
    assert flags[5] == 1.0
    assert all(f == 0.0 for f in flags[:5])
    assert stress.lines["availability"][5] == pytest.approx(0.0)
    assert stress.inputs.opening_cash < 1_000_000


def test_base_case_never_breaches_the_covenant(base):
    assert not any(base.lines["covenant_breach"])


def test_lower_tier_payments_lag_receipts_by_one_week(base):
    assert base.lower_tier[0] == 0.0
    for k in range(1, HORIZON):
        want = sum(w.item.share * w.weeks[k - 1] for w in base.items)
        assert base.lower_tier[k] == pytest.approx(want)
