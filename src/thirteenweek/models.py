"""Plain data records shared by every module, and the as-of truncation rule."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date

N_BINS = 32  # lag bins 0..30 (whole weeks, rounded up) plus "beyond"
BEYOND = 31  # index of the "beyond" bin
HORIZON = 13  # forecast weeks
WINDOW_WEEKS = 78  # pay apps billed in this many weeks before the as-of date feed the PMFs
SHRINK_K = 8  # a GC with n paid apps gets weight n / (n + SHRINK_K) on its own PMF
DEFAULT_RETAINAGE_LAG_DAYS = 45.0  # used only when no job has ever released retainage


def week_of(d: date, as_of: date) -> int:
    """Forecast week (1-based) containing ``d``; anything on or before the as-of date is week 1."""
    return max(1, math.ceil((d - as_of).days / 7))


@dataclass(frozen=True)
class GC:
    gc_id: str
    name: str
    terms_days: int


@dataclass(frozen=True)
class Job:
    job_id: str
    gc_id: str
    contract_value: float
    start_date: date
    substantial_completion: date
    lower_tier_share: float
    retainage_paid: date | None


@dataclass(frozen=True)
class PayApp:
    app_id: str
    job_id: str
    billed: date
    gross: float
    retainage: float
    paid: date | None

    @property
    def net(self) -> float:
        """Cash due on the application: gross less the retainage the GC holds back."""
        return round(self.gross - self.retainage, 2)


@dataclass(frozen=True)
class Invoice:
    invoice_id: str
    supplier: str
    invoice_date: date
    due_date: date
    amount: float
    paid: date | None


@dataclass(frozen=True)
class PayrollRow:
    week_ending: date
    crew: str
    amount: float


@dataclass(frozen=True)
class Inputs:
    opening_cash: float
    revolver_limit: float
    revolver_outstanding: float
    revolver_rate: float
    min_cash: float
    min_liquidity: float


@dataclass(frozen=True)
class Stress:
    gc_id: str
    extra_weeks: int
    opening_cash: float


@dataclass(frozen=True)
class Dataset:
    gcs: tuple[GC, ...]
    jobs: tuple[Job, ...]
    apps: tuple[PayApp, ...]
    invoices: tuple[Invoice, ...]
    payroll: tuple[PayrollRow, ...]
    inputs: Inputs
    stress: Stress
    as_of: date

    def job_map(self) -> dict[str, Job]:
        return {j.job_id: j for j in self.jobs}

    def known_at(self, as_of: date) -> Dataset:
        """Return only what a person could have known on ``as_of``.

        Later billings and invoices are dropped and later payment dates are blanked. The payroll
        plan is forward-looking by nature and is kept whole.
        """

        def blank(d: date | None) -> date | None:
            return None if d is not None and d > as_of else d

        return replace(
            self,
            jobs=tuple(replace(j, retainage_paid=blank(j.retainage_paid)) for j in self.jobs),
            apps=tuple(replace(a, paid=blank(a.paid)) for a in self.apps if a.billed <= as_of),
            invoices=tuple(
                replace(i, paid=blank(i.paid)) for i in self.invoices if i.invoice_date <= as_of
            ),
        )
