from __future__ import annotations

import pytest

from thirteenweek.generate import generate
from thirteenweek.reference import compute_forecast
from thirteenweek.verify import recalculate
from thirteenweek.workbook import build_workbook


@pytest.fixture(scope="session")
def bundle():
    ds, truth = generate(7)
    return ds, truth


@pytest.fixture(scope="session")
def ds(bundle):
    return bundle[0]


@pytest.fixture(scope="session")
def truth(bundle):
    return bundle[1]


@pytest.fixture(scope="session")
def base(ds):
    return compute_forecast(ds, ds.as_of)


@pytest.fixture(scope="session")
def stress(ds):
    return compute_forecast(ds, ds.as_of, stress=True)


@pytest.fixture(scope="session")
def base_book(base, tmp_path_factory):
    return build_workbook(base, tmp_path_factory.mktemp("book") / "cash13.xlsx")


@pytest.fixture(scope="session")
def stress_book(stress, tmp_path_factory):
    return build_workbook(stress, tmp_path_factory.mktemp("book") / "stress.xlsx")


@pytest.fixture(scope="session")
def base_values(base_book):
    return recalculate(base_book)


@pytest.fixture(scope="session")
def stress_values(stress_book):
    return recalculate(stress_book)
