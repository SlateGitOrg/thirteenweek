"""Recalculate the workbook with ``formulas`` and compare it to the Python reference."""

from __future__ import annotations

import os
import re
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import load_workbook

from .models import HORIZON, Dataset
from .reference import ForecastResult, compute_forecast
from .workbook import ALLOWED_FUNCTIONS, FORECAST_ROWS

TOLERANCE = 1.0  # dollars
_KEY = re.compile(r"^'\[(?P<book>.+?)\](?P<sheet>.+?)'!(?P<ref>[A-Z]+[0-9]+)$")
COMPARED_LINES = [k for k in FORECAST_ROWS if k not in ("week", "week_ending")]


def _formulas():
    """Import ``formulas`` quietly (it ships a syntax the newest interpreters warn about)."""
    os.environ.setdefault("TQDM_DISABLE", "1")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import formulas
        from formulas.functions import get_functions

        get_functions()
    return formulas


def recalculate(path: Path) -> dict[tuple[str, str], object]:
    """Calculate every formula; returns ``{(SHEET, "B4"): value}`` for single cells."""
    formulas = _formulas()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        solution = formulas.ExcelModel().loads(str(path)).finish().calculate()
    values: dict[tuple[str, str], object] = {}
    for key, cell in solution.items():
        m = _KEY.match(key)
        if m:
            values[(m["sheet"].upper(), m["ref"])] = cell.value[0, 0]
    return values


def error_cells(values: dict[tuple[str, str], object]) -> list[tuple[str, str, str]]:
    """Every recalculated cell holding an Excel error value (``#DIV/0!``, ``#N/A``, ...)."""
    _formulas()
    from formulas.functions import XlError

    return [
        (sheet, ref, str(v))
        for (sheet, ref), v in values.items()
        if isinstance(v, XlError) or (isinstance(v, str) and v.startswith("#"))
    ]


def formula_functions(path: Path) -> set[str]:
    """Names of all functions used by formulas in the workbook."""
    wb = load_workbook(path)
    used: set[str] = set()
    for ws in wb:
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if isinstance(v, str) and v.startswith("="):
                    used.update(re.findall(r"([A-Z][A-Z0-9.]*)\(", v))
    return used


def forbidden_functions(path: Path) -> set[str]:
    return formula_functions(path) - ALLOWED_FUNCTIONS


def count_formulas(path: Path) -> int:
    wb = load_workbook(path)
    return sum(
        1
        for ws in wb
        for row in ws.iter_rows()
        for c in row
        if isinstance(c.value, str) and c.value.startswith("=")
    )


@dataclass
class Difference:
    line: str
    week: int
    workbook: float
    reference: float

    @property
    def diff(self) -> float:
        return abs(self.workbook - self.reference)


@dataclass
class VerifyReport:
    as_of: date
    scenario: str
    cells_compared: int = 0
    max_diff: float = 0.0
    differences: list[Difference] = field(default_factory=list)
    errors: list[tuple[str, str, str]] = field(default_factory=list)
    checks_pass: bool = False

    @property
    def ok(self) -> bool:
        return not self.differences and not self.errors and self.checks_pass


def workbook_scenario(path: Path) -> tuple[date, str]:
    ws = load_workbook(path, data_only=False)["Inputs"]
    value = ws["B1"].value
    if isinstance(value, datetime):
        as_of = value.date()
    else:
        as_of = date(1899, 12, 30) + timedelta(days=int(value))
    return as_of, str(ws["B11"].value)


def compare(values: dict, ref: ForecastResult) -> VerifyReport:
    report = VerifyReport(as_of=ref.as_of, scenario=ref.scenario)
    for line in COMPARED_LINES:
        for k in range(HORIZON):
            cell = f"{chr(ord('B') + k)}{FORECAST_ROWS[line]}"
            got = float(values[("FORECAST", cell)])
            want = float(ref.lines[line][k])
            report.cells_compared += 1
            d = abs(got - want)
            report.max_diff = max(report.max_diff, d)
            if d > TOLERANCE:
                report.differences.append(Difference(line, k + 1, got, want))
    return report


def verify_workbook(path: Path, ds: Dataset) -> VerifyReport:
    as_of, scenario = workbook_scenario(path)
    ref = compute_forecast(ds, as_of, stress=scenario == "stress")
    values = recalculate(path)
    report = compare(values, ref)
    report.errors = error_cells(values)
    report.checks_pass = bool(values.get(("CHECKS", "B10")))
    return report
