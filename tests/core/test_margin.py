from datetime import date
from decimal import Decimal

import pytest
from nautilus_trader.model.currencies import INR
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.objects import Money, Price, Quantity

from nautilus_india.core.enums import Exchange, InstrumentClass
from nautilus_india.core.instruments import option_contract
from nautilus_india.core.margin import IndianOptionMarginModel
from nautilus_india.core.symbology import ContractKey

CALL = ContractKey("NIFTY", InstrumentClass.OPTION, date(2026, 8, 4), Decimal("24550"), "CE")
LEVERAGE = Decimal(1)


@pytest.fixture
def contract():
    return option_contract(CALL, Exchange.NSE)


def test_a_long_option_blocks_nothing(contract):
    """The premium already left the account as cash. Charging margin on top
    double-counts it."""
    model = IndianOptionMarginModel()
    margin = model.calculate_margin_maint(
        contract, PositionSide.LONG, Quantity.from_int(1), Price.from_str("100"), LEVERAGE
    )
    assert margin == Money(0, INR)


def test_a_short_option_blocks_span_plus_exposure_on_notional(contract):
    """A sold option carries unbounded risk, so the exchange blocks a
    fraction of NOTIONAL -- not of the premium received."""
    model = IndianOptionMarginModel()
    margin = model.calculate_margin_maint(
        contract, PositionSide.SHORT, Quantity.from_int(1), Price.from_str("100"), LEVERAGE
    )
    premium = Decimal("100") * 65
    assert margin.as_decimal() > premium * 10


def test_an_unbound_model_uses_the_strike_as_spot(contract):
    """Exact at the money, drifting with moneyness. A fallback, not the
    design: a deep OTM short would otherwise be scored on a notional the
    exchange never used."""
    model = IndianOptionMarginModel()
    margin = model.calculate_margin_maint(
        contract, PositionSide.SHORT, Quantity.from_int(1), Price.from_str("100"), LEVERAGE
    )
    expected = Decimal("24550") * 65 * model.rates.total_pct
    assert margin.as_decimal() == expected.quantize(margin.as_decimal())


def test_margin_scales_with_lots(contract):
    model = IndianOptionMarginModel()
    one = model.calculate_margin_maint(
        contract, PositionSide.SHORT, Quantity.from_int(1), Price.from_str("100"), LEVERAGE
    )
    two = model.calculate_margin_maint(
        contract, PositionSide.SHORT, Quantity.from_int(2), Price.from_str("100"), LEVERAGE
    )
    assert two.as_decimal() == one.as_decimal() * 2


def test_margin_init_charges_the_short_rate_because_it_is_not_told_the_side(contract):
    """`calculate_margin_init` receives no side, so it cannot tell a long
    from a short. Charging the short rate is the conservative choice for the
    case that reaches it."""
    model = IndianOptionMarginModel()
    init = model.calculate_margin_init(
        contract, Quantity.from_int(1), Price.from_str("100"), LEVERAGE
    )
    short = model.calculate_margin_maint(
        contract, PositionSide.SHORT, Quantity.from_int(1), Price.from_str("100"), LEVERAGE
    )
    assert init == short


def test_the_result_is_inr_and_decimal(contract):
    model = IndianOptionMarginModel()
    margin = model.calculate_margin_maint(
        contract, PositionSide.SHORT, Quantity.from_int(1), Price.from_str("100"), LEVERAGE
    )
    assert margin.currency == INR
    assert isinstance(margin.as_decimal(), Decimal)


def test_the_rates_carry_their_calibration_provenance():
    """A margin rate with no source is a guess, and a guess in the sizing
    path vetoes trades the exchange would have allowed."""
    model = IndianOptionMarginModel()
    assert model.rates.calibrated_on
    assert model.rates.error_pct > 0
    assert model.rates.observations > 0


def test_a_per_instrument_model_overcharges_a_multi_leg_short(contract):
    """The exchange charges SPAN per PORTFOLIO: a short straddle is roughly
    1.2x a naked short, not 2x. Summed per instrument it is 2x.

    This test pins the OVER-charge, which is the safe direction. It is meant
    to be seen, not to pass quietly -- see docs/UPSTREAM_GAPS.md for why a
    portfolio-aware model must not ship before margin re-evaluation lands.
    """
    put = option_contract(
        ContractKey("NIFTY", InstrumentClass.OPTION, date(2026, 8, 4), Decimal("24550"), "PE"),
        Exchange.NSE,
    )
    model = IndianOptionMarginModel()
    call_leg = model.calculate_margin_maint(
        contract, PositionSide.SHORT, Quantity.from_int(1), Price.from_str("100"), LEVERAGE
    )
    put_leg = model.calculate_margin_maint(
        put, PositionSide.SHORT, Quantity.from_int(1), Price.from_str("100"), LEVERAGE
    )
    straddle = call_leg.as_decimal() + put_leg.as_decimal()
    naked = call_leg.as_decimal()
    assert straddle == naked * 2
    # The exchange would charge roughly 1.2x. We charge 2x, and say so.
    assert straddle > naked * Decimal("1.2")


def test_there_is_no_bind_book():
    """The portfolio-aware path is deliberately absent.

    It is implementable as an incremental charge that sums to the portfolio
    figure, but Nautilus asks once at open and never re-asks on close, so
    legging out would leave the survivor holding an increment to a structure
    that no longer exists. Trading a safe over-charge for an unsafe
    under-charge is not an improvement. This test stops it being added back
    without reading docs/UPSTREAM_GAPS.md.
    """
    assert not hasattr(IndianOptionMarginModel, "bind_book")
