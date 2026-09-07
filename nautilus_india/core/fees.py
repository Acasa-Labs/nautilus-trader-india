"""Indian transaction costs, dated and charged per leg, in Decimal throughout.

COSTS ARE PER LEG AND SUMMED, never computed on a net premium: STT falls on
the sell side only and stamp duty on the buy side only, so a spread's cost is
not a function of its net.

TWO THINGS ARE EASY TO GET WRONG, and both were, until they were measured
against Dhan's own brokerage calculator on 2026-09-04:

* **Brokerage on options is a flat Rs 20 per executed order.** The familiar
  "Rs 20 or 0.03% of trade value, whichever is lower" is the EQUITY INTRADAY
  and MTF rule. Applying it to options under-charges by ~10x on a one-lot
  trade, because 0.03% of a Rs 6,500 premium is Rs 1.95.
* **GST does not apply to the SEBI turnover fee.** It is 18% of brokerage
  plus exchange charges only. Including the SEBI fee overstates GST by
  0.000018% of turnover -- negligible in rupees, but it is the difference
  between reproducing the calculator exactly and not, and a model that is
  exact is one you can tell is right.

EVERYTHING IS `Decimal`, AND YAML FLOATS CONVERT THROUGH `str`. A float total
cannot reproduce a calculator to the paisa, and "reproduces it to the paisa"
is the only evidence these rates are right. `Decimal(0.0015)` is
0.001499999999999999944488848768742172978818416595458984375;
`Decimal(str(0.0015))` is exactly 0.0015, which is what the rate means.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path

import yaml
from nautilus_trader.backtest.models import FeeModel
from nautilus_trader.core.datetime import unix_nanos_to_dt
from nautilus_trader.model.currencies import INR
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.objects import Money

from nautilus_india.core.calendar import IST

_RATES_PATH = Path(__file__).parent / "data" / "rates.yaml"


class UnknownCostRatesError(Exception):
    """No dated rates cover this date.

    Raised rather than defaulted: using today's rates for an uncovered
    period misprices it silently.
    """


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    stt: Decimal
    brokerage: Decimal
    exchange: Decimal
    sebi: Decimal
    stamp: Decimal
    gst: Decimal

    @property
    def total(self) -> Decimal:
        return self.stt + self.brokerage + self.exchange + self.sebi + self.stamp + self.gst


@cache
def _load(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text())["options"]


def _dec(value) -> Decimal:
    """A YAML number as an exact Decimal. See the module docstring."""
    return Decimal(str(value))


class CostModel:
    def __init__(self, rates_path: Path | None = None) -> None:
        self._path = rates_path or _RATES_PATH

    def _rates(self, on: date) -> dict:
        for row in _load(self._path):
            if date.fromisoformat(row["from"]) <= on <= date.fromisoformat(row["to"]):
                return row
        raise UnknownCostRatesError(
            f"No cost rates cover {on}. Add the period to {self._path.name} "
            "with its source, rather than letting another period's rates apply."
        )

    def rates_are_verified(self, on: date) -> bool:
        """True only once the period was reconciled to a real contract note."""
        return bool(self._rates(on).get("verified", False))

    def reconciled_against(self, on: date) -> str:
        """What this period's rates have been checked against, if anything."""
        return str(self._rates(on).get("reconciled_against") or "none")

    def leg_cost(self, on: date, side: OrderSide, price: Decimal, units: int) -> CostBreakdown:
        r = self._rates(on)
        turnover = Decimal(price) * units

        stt = turnover * _dec(r["stt_sell"]) if side == OrderSide.SELL else Decimal(0)
        stamp = turnover * _dec(r["stamp_buy"]) if side == OrderSide.BUY else Decimal(0)
        # Flat per executed order, with no percentage branch. See docstring.
        brokerage = _dec(r["brokerage_flat"])
        exchange = turnover * _dec(r["exchange"])
        sebi = turnover * _dec(r["sebi"])
        # GST is a tax on services, so it never touches STT, which is itself
        # a transaction tax. Measured: it does not touch the SEBI turnover
        # fee either -- only brokerage and exchange charges.
        gst = _dec(r["gst"]) * (brokerage + exchange)
        return CostBreakdown(
            stt=stt, brokerage=brokerage, exchange=exchange,
            sebi=sebi, stamp=stamp, gst=gst,
        )

    def exercise_cost(self, on: date, intrinsic: Decimal, units: int) -> Decimal:
        """STT on an ITM option exercised at expiry, levied on INTRINSIC value.

        The charge naive backtests omit, at a rate far above the premium
        rate. On a small winner it can exceed the entire profit.
        """
        if intrinsic <= 0:
            return Decimal(0)
        return Decimal(intrinsic) * units * _dec(self._rates(on)["stt_exercise"])


class IndianOptionFeeModel(FeeModel):
    """NSE option charges, per fill, in INR.

    UNITS, NOT LOTS. Nautilus fills a `quantity` of CONTRACTS and prices them
    through `instrument.multiplier`. Turnover is `price * qty * multiplier`;
    passing `qty` alone under-charges every cost by the lot size -- 65x on
    current NIFTY.

    DOES NOT COVER `exercise_cost`. Nautilus settles an expiring contract
    through its own path rather than through `get_commission`, so that charge
    needs the settlement hook and is deliberately absent here rather than
    approximated. A run held to expiry is under-charged until it lands; the
    gap is named in docs/UPSTREAM_GAPS.md rather than left to be noticed.
    """

    def __init__(self, costs: CostModel | None = None) -> None:
        super().__init__()
        self._costs = costs or CostModel()

    def get_commission(self, order, fill_qty, fill_px, instrument) -> Money:
        units = int(fill_qty) * int(instrument.multiplier)
        breakdown = self._costs.leg_cost(
            self._trade_date(order), order.side, Decimal(str(fill_px)), units
        )
        total = breakdown.total
        if int(order.filled_qty) > 0:
            # A later slice of an order that already paid the flat brokerage.
            total -= breakdown.brokerage
        return Money(total, INR)

    @staticmethod
    def _trade_date(order) -> date:
        """The IST date this fill happened on.

        `ts_last` is the order's most recent event, which at commission time
        is the fill being priced -- so a bracket child that rested for a week
        is charged at the rates of the day it FILLED. It falls back to
        `ts_init` so an order with no events yet is dated by its creation
        rather than by the epoch: 1970 is outside every period in rates.yaml
        and would raise.
        """
        stamp = order.ts_last or order.ts_init
        return unix_nanos_to_dt(stamp).astimezone(IST).date()
