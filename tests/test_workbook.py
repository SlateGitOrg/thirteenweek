from __future__ import annotations

import pytest
from openpyxl import load_workbook

from thirteenweek.models import HORIZON
from thirteenweek.reference import compute_forecast
from thirteenweek.verify import (
    COMPARED_LINES,
    compare,
    count_formulas,
    error_cells,
    forbidden_functions,
    formula_functions,
)
from thirteenweek.workbook import ALLOWED_FUNCTIONS, FORECAST_ROWS

SHEETS = [
    "Inputs",
    "LagPMF",
    "Survival",
    "OpenAR",
    "Disputed",
    "Disbursements",
    "Forecast",
    "Checks",
]


def test_workbook_has_the_specified_sheets(base_book):
    assert load_workbook(base_book).sheetnames == SHEETS


def test_formulas_use_only_whitelisted_functions(base_book, stress_book):
    for book in (base_book, stress_book):
        used = formula_functions(book)
        assert used and used <= ALLOWED_FUNCTIONS, used - ALLOWED_FUNCTIONS
        assert not forbidden_functions(book)
    assert {"INDEX", "MATCH", "SUMPRODUCT"} <= formula_functions(base_book)


def test_no_volatile_or_banned_text_anywhere(base_book):
    wb = load_workbook(base_book)
    banned = ("OFFSET", "INDIRECT", "TODAY", "TABLE(")
    for ws in wb:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    assert not any(b in c.value.upper() for b in banned), (ws.title, c.coordinate)


def test_formula_count_stays_modest(base_book):
    assert 800 < count_formulas(base_book) < 3000


def test_forecast_matches_reference_in_every_cell(base, base_values):
    report = compare(base_values, base)
    assert report.cells_compared == len(COMPARED_LINES) * HORIZON
    assert report.differences == []
    assert report.max_diff < 1.0


def test_stress_forecast_matches_reference_in_every_cell(stress, stress_values):
    report = compare(stress_values, stress)
    assert report.differences == [] and report.max_diff < 1.0


def test_no_recalculated_cell_is_an_error(base_values, stress_values):
    assert error_cells(base_values) == []
    assert error_cells(stress_values) == []


def test_checks_sheet_all_pass(base_values, stress_values):
    for values in (base_values, stress_values):
        for row in range(2, 9):
            assert bool(values[("CHECKS", f"B{row}")]) is True, row
        assert bool(values[("CHECKS", "B10")]) is True


def test_workbook_conservation_per_open_item(base, base_values):
    for r in range(2, len(base.items) + 2):
        total = sum(
            float(values)
            for values in (base_values[("OPENAR", f"{c}{r}")] for c in "JKLMNOPQRSTUVW")
        )
        amount = float(base_values.get(("OPENAR", f"F{r}"), 0) or 0)
        assert total == pytest.approx(amount, abs=0.01)


def test_disputed_sheet_lists_j17_with_its_amount_and_no_formulas(base, base_book, truth):
    ws = load_workbook(base_book)["Disputed"]
    assert ws["A2"].value == truth["disputed_app"]["app_id"]
    assert ws["B2"].value == "J17"
    assert ws["E2"].value == pytest.approx(truth["disputed_app"]["net"])
    assert not any(
        isinstance(c.value, str) and c.value.startswith("=") for row in ws.iter_rows() for c in row
    )
    ids = {load_workbook(base_book)["OpenAR"].cell(row=r, column=1).value for r in range(2, 80)}
    assert truth["disputed_app"]["app_id"] not in ids


def test_survival_sheet_is_formulas_over_lagpmf(base_book):
    ws = load_workbook(base_book)["Survival"]
    assert ws["B2"].value.startswith("=SUM(LagPMF!B2:")
    assert ws["AG2"].value.startswith("=SUM(LagPMF!AG2:")


def test_stress_workbook_shows_breach_flags(stress_values):
    cols = "BCDEFG"
    row = FORECAST_ROWS["covenant_breach"]
    flags = [bool(stress_values[("FORECAST", f"{c}{row}")]) for c in cols]
    assert flags == [False, False, False, False, False, True]


def test_base_workbook_draws_in_week_six(base_values):
    row = FORECAST_ROWS["draw"]
    assert [float(base_values[("FORECAST", f"{c}{row}")]) for c in "BCDEF"] == [0.0] * 5
    assert float(base_values[("FORECAST", f"G{row}")]) > 0


def test_forecast_recomputed_from_scratch_is_stable(ds, base):
    again = compute_forecast(ds, ds.as_of)
    assert again.lines == base.lines
