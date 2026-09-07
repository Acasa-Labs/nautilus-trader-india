"""The FeeModel adapter: units, not lots, and brokerage once per order."""

from datetime import datetime
from decimal import Decimal

from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model.currencies import INR
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.objects import Money, Price, Quantity

from nautilus_india.core.calendar import IST
from nautilus_india.core.fees import CostModel, IndianOptionFeeModel


class _FakeOrder:
    """A stand-in for a Nautilus order, carrying only what the model reads."""

    def __init__(self, side, ts_last, filled_qty=0):
        self.side = side
        self.ts_last = ts_last
        self.ts_init = ts_last
        self.filled_qty = Quantity.from_int(filled_qty)


class _FakeInstrument:
    def __init__(self, multiplier):
        self.multiplier = Quantity.from_int(multiplier)


def _nanos(y, m, d) -> int:
    return dt_to_unix_nanos(datetime(y, m, d, 10, 0, tzinfo=IST))


def test_turnover_uses_units_not_lots():
    """Nautilus fills a quantity of CONTRACTS and prices them through
    `multiplier`. Turnover is price * qty * multiplier; passing qty alone
    under-charges every cost by the lot size -- 65x on current NIFTY."""
    model = IndianOptionFeeModel()
    order = _FakeOrder(OrderSide.SELL, _nanos(2026, 9, 4))
    fee = model.get_commission(
        order, Quantity.from_int(1), Price.from_str("100"), _FakeInstrument(65)
    )

    expected = CostModel().leg_cost(
        datetime(2026, 9, 4, tzinfo=IST).date(), OrderSide.SELL, Decimal("100"), 65
    )
    assert fee == Money(expected.total, INR)


def test_charging_lots_instead_of_units_would_undercharge_by_the_lot_size():
    """The failure the units rule prevents, shown as a ratio."""
    costs = CostModel()
    on = datetime(2026, 9, 4, tzinfo=IST).date()
    units = costs.leg_cost(on, OrderSide.SELL, Decimal("100"), 65)
    lots = costs.leg_cost(on, OrderSide.SELL, Decimal("100"), 1)
    # Brokerage is flat, so the ratio is not exactly 65 -- but the
    # turnover-driven components are.
    assert units.stt == lots.stt * 65
    assert units.exchange == lots.exchange * 65
    assert units.total > lots.total


def test_brokerage_is_charged_once_per_order_not_once_per_fill():
    """A partially filled order must not pay the flat Rs 20 twice."""
    model = IndianOptionFeeModel()
    first = model.get_commission(
        _FakeOrder(OrderSide.BUY, _nanos(2026, 9, 4), filled_qty=0),
        Quantity.from_int(1), Price.from_str("100"), _FakeInstrument(65),
    )
    second = model.get_commission(
        _FakeOrder(OrderSide.BUY, _nanos(2026, 9, 4), filled_qty=1),
        Quantity.from_int(1), Price.from_str("100"), _FakeInstrument(65),
    )
    assert first.as_decimal() - second.as_decimal() == Decimal("20")


def test_the_fill_is_dated_by_its_own_event_not_by_the_epoch():
    """A bracket child that rests for a week is charged at the rates of the
    day it FILLED. And ts_last of 0 would date it to 1970, which is outside
    every period in rates.yaml and would raise."""
    model = IndianOptionFeeModel()
    order = _FakeOrder(OrderSide.BUY, 0)
    order.ts_init = _nanos(2026, 9, 4)
    fee = model.get_commission(
        order, Quantity.from_int(1), Price.from_str("100"), _FakeInstrument(65)
    )
    assert fee.as_decimal() > 0
