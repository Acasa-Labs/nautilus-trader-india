import re
from datetime import date
from decimal import Decimal

import pytest
from nautilus_trader.model.enums import OrderSide

from nautilus_india.core.fees import CostModel, UnknownCostRatesError

TODAY = date(2026, 9, 4)


def test_stt_falls_on_the_sell_side_only():
    """Costs are per LEG and summed, never on a net premium. STT is
    sell-side and stamp duty buy-side, so a spread's cost is not a function
    of its net."""
    model = CostModel()
    sell = model.leg_cost(TODAY, OrderSide.SELL, Decimal("100"), 65)
    buy = model.leg_cost(TODAY, OrderSide.BUY, Decimal("100"), 65)
    assert sell.stt > 0
    assert buy.stt == 0


def test_stamp_duty_falls_on_the_buy_side_only():
    model = CostModel()
    assert model.leg_cost(TODAY, OrderSide.BUY, Decimal("100"), 65).stamp > 0
    assert model.leg_cost(TODAY, OrderSide.SELL, Decimal("100"), 65).stamp == 0


def test_brokerage_on_options_is_flat_twenty_not_a_percentage():
    """The familiar 'Rs 20 or 0.03%, whichever is lower' is Dhan's EQUITY
    INTRADAY tariff. For options it is a flat Rs 20 per executed order, with
    no percentage branch. Applying the equity rule under-charges brokerage
    by ~10x on a one-lot trade: 0.03% of a Rs 6,500 premium is Rs 1.95.
    """
    model = CostModel()
    small = model.leg_cost(TODAY, OrderSide.BUY, Decimal("100"), 65)
    large = model.leg_cost(TODAY, OrderSide.BUY, Decimal("1000"), 6500)
    assert small.brokerage == Decimal("20")
    assert large.brokerage == Decimal("20")


def test_gst_does_not_touch_the_sebi_turnover_fee():
    """GST is 18% of brokerage plus exchange charges ONLY.

    Including the SEBI fee overstates GST by 0.000018% of turnover --
    negligible in rupees, but it is the difference between reproducing
    Dhan's calculator exactly and not, and a model that is exact is one you
    can tell is right.
    """
    model = CostModel()
    c = model.leg_cost(TODAY, OrderSide.BUY, Decimal("100"), 65)
    assert c.gst == (c.brokerage + c.exchange) * Decimal("0.18")


def test_the_trade_date_decides_the_rates():
    """Two dated periods, and the older one is not today's rates."""
    model = CostModel()
    old = model.leg_cost(date(2020, 1, 15), OrderSide.SELL, Decimal("100"), 65)
    new = model.leg_cost(TODAY, OrderSide.SELL, Decimal("100"), 65)
    assert old.stt != new.stt


def test_an_uncovered_date_raises_rather_than_borrowing_another_period():
    """Using today's rates for an uncovered period misprices it silently."""
    model = CostModel()
    with pytest.raises(UnknownCostRatesError, match=re.escape("rates.yaml")):
        model.leg_cost(date(1990, 1, 1), OrderSide.BUY, Decimal("100"), 65)


def test_rates_admit_they_are_unverified_and_say_what_was_checked():
    """The rates reproduce Dhan's own calculator to the paisa, but that tool
    carries a disclaimer pointing at the contract note. Only a real contract
    note flips the flag."""
    model = CostModel()
    assert model.rates_are_verified(TODAY) is False
    assert "calculator" in model.reconciled_against(TODAY)


def test_exercise_stt_is_levied_on_intrinsic_and_is_zero_when_out_of_the_money():
    """The charge naive backtests omit. On a small winner it can exceed the
    entire profit."""
    model = CostModel()
    assert model.exercise_cost(TODAY, Decimal("50"), 65) > 0
    assert model.exercise_cost(TODAY, Decimal("0"), 65) == 0
    assert model.exercise_cost(TODAY, Decimal("-10"), 65) == 0


def test_every_component_is_decimal_not_float():
    """A float total cannot reproduce a calculator to the paisa."""
    c = CostModel().leg_cost(TODAY, OrderSide.SELL, Decimal("123.45"), 65)
    for value in (c.stt, c.brokerage, c.exchange, c.sebi, c.stamp, c.gst, c.total):
        assert isinstance(value, Decimal)


def test_a_yaml_rate_is_not_polluted_by_binary_floating_point():
    """`Decimal(0.0015)` is 0.001499999999999999944488848768742172978818...

    YAML parses 0.0015 to a float, so the conversion must go through `str`.
    Straight through `Decimal(float)` the STT on a large turnover drifts,
    and 'reproduces the calculator to the paisa' is the only evidence these
    rates are right.
    """
    model = CostModel()
    turnover = Decimal("1000") * 6500
    stt = model.leg_cost(TODAY, OrderSide.SELL, Decimal("1000"), 6500).stt
    assert stt == turnover * Decimal("0.0015")


# The seven cases reconciled against Dhan's own brokerage calculator on
# 2026-09-04, NIFTY 08 SEP 18900 CE, lot 65.
#
# THIS TABLE MUST BE FILLED IN FROM THE CALCULATOR ITSELF.
# https://dhan.co/calculators/brokerage-calculator/
#
# Populating it from this model's own output would assert only that the
# model agrees with itself, which is the one thing it cannot fail to do.
# Until then the test skips and says so, rather than passing vacuously.
#
# Format: (side, price, lots, expected_total)
RECONCILED_CASES: list[tuple[OrderSide, str, int, str]] = []


@pytest.mark.skipif(
    not RECONCILED_CASES,
    reason="calculator figures not yet transcribed; see the comment above the table",
)
@pytest.mark.parametrize(("side", "price", "lots", "expected_total"), RECONCILED_CASES)
def test_reproduces_dhans_calculator_to_the_paisa(side, price, lots, expected_total):
    cost = CostModel().leg_cost(TODAY, side, Decimal(price), lots * 65)
    assert cost.total.quantize(Decimal("0.01")) == Decimal(expected_total)


def test_the_reconciliation_table_is_the_only_thing_standing_between_us_and_verified():
    """A guard on the guard.

    If someone fills the table in, `rates_are_verified` should still be
    False until a real CONTRACT NOTE is seen -- the calculator carries its
    own disclaimer pointing at one. This test exists so that unskipping the
    reconciliation is not mistaken for verification.
    """
    model = CostModel()
    assert model.rates_are_verified(TODAY) is False, (
        "only a real contract note flips this flag, not the calculator"
    )
