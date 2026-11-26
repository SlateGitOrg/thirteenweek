from __future__ import annotations

import pytest

from thirteenweek.models import HORIZON


def test_every_open_item_conserves_its_amount(base):
    for w in base.items:
        total = sum(w.weeks) + w.after
        assert total == pytest.approx(w.item.amount, abs=0.01), w.item.item_id


def test_total_equals_open_ar_plus_retainage_minus_disputed(base):
    total = sum(sum(w.weeks) + w.after for w in base.items)
    assert total == pytest.approx(
        base.open_ar + base.retainage_held - base.disputed_total, abs=0.01
    )
    assert base.disputed_total == pytest.approx(sum(d.amount for d in base.disputed))


def test_disputed_j17_app_is_excluded_everywhere(base, truth):
    d = truth["disputed_app"]
    assert [x.item_id for x in base.disputed] == [d["app_id"]]
    assert base.disputed[0].amount == pytest.approx(d["net"])
    assert d["app_id"] not in {w.item.item_id for w in base.items}


def test_receipts_line_is_the_sum_of_item_weeks(base):
    for k in range(HORIZON):
        assert base.lines["receipts"][k] == pytest.approx(sum(w.weeks[k] for w in base.items))


def test_old_open_apps_are_forecast_from_the_tail_not_the_mode(base):
    aged = [
        w
        for w in base.items
        if w.item.kind == "PayApp" and 6 <= w.item.age <= 12 and w.weeks[0] > 0
    ]
    assert aged
    w = max(aged, key=lambda x: x.item.age)
    pmf = base.model.pmf[w.item.gc_id]
    surv = base.model.surv(w.item.gc_id)
    a = w.item.age
    # week-1 share is the unconditional bin a+1 divided by the survival S(a+1)
    assert w.weeks[0] / w.item.amount == pytest.approx(pmf[a + 1] / surv[a + 1])
    assert surv[a + 1] < 1.0
    assert w.weeks[0] / w.item.amount > pmf[a + 1]


def test_retainage_releases_for_horizon_jobs_fall_inside_the_horizon(base, truth):
    rows = {w.item.job_id: w.item for w in base.items if w.item.kind == "Retainage"}
    for job in truth["retainage_release_jobs"]:
        assert rows[job].target_week is not None and rows[job].target_week <= HORIZON
    beyond = [i for i in rows.values() if i.target_week > HORIZON]
    assert beyond, "retainage on open jobs goes to 'after week 13'"
    held_after = sum(w.after for w in base.items if w.item.kind == "Retainage")
    assert held_after == pytest.approx(sum(i.amount for i in beyond))


def test_retainage_release_is_one_receipt_in_one_week(base):
    for w in base.items:
        if w.item.kind == "Retainage" and w.item.target_week <= HORIZON:
            assert sum(1 for x in w.weeks if x) == 1
            assert w.weeks[w.item.target_week - 1] == w.item.amount
