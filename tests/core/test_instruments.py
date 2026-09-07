from datetime import date, datetime, time
from decimal import Decimal

import pytest
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model.currencies import INR
from nautilus_trader.model.enums import OptionKind
from nautilus_trader.model.objects import Price, Quantity

from nautilus_india.core.calendar import IST
from nautilus_india.core.enums import Exchange, InstrumentClass
from nautilus_india.core.instruments import (
    equity,
    futures_contract,
    index_instrument,
    option_contract,
)
from nautilus_india.core.symbology import ContractKey

NIFTY_CALL = ContractKey("NIFTY", InstrumentClass.OPTION, date(2026, 8, 4), Decimal("24550"), "CE")


def test_the_multiplier_carries_the_lot_and_quantity_is_in_lots():
    """Nautilus prices a fill as qty * multiplier * price.

    Passing the lot size as BOTH squares the position -- a NIFTY 24550 CE
    round trip came back at Rs 78,585 against a true Rs 1,209, because 65
    lots of a 65-multiplier contract is 4,225 units. So `lot_size` is 1:
    one contract is one lot, and the 65 lives in `multiplier` alone.
    """
    contract = option_contract(NIFTY_CALL, Exchange.NSE)
    assert contract.multiplier == Quantity.from_int(65)
    assert contract.lot_size == Quantity.from_int(1)


def test_an_option_is_priced_in_inr_at_a_five_paise_tick():
    contract = option_contract(NIFTY_CALL, Exchange.NSE)
    # The constructor takes `currency=`; the instance exposes it as
    # `quote_currency`, and settles in the same one.
    assert contract.quote_currency == INR
    assert contract.get_settlement_currency() == INR
    assert contract.price_increment == Price.from_str("0.05")
    assert contract.price_precision == 2


def test_expiration_is_the_1530_close_not_midnight():
    """Midnight would keep a dead contract alive for the whole expiry
    afternoon, and every position check that afternoon would be wrong."""
    contract = option_contract(NIFTY_CALL, Exchange.NSE)
    expected = dt_to_unix_nanos(datetime.combine(date(2026, 8, 4), time(15, 30), tzinfo=IST))
    assert contract.expiration_ns == expected


def test_the_right_becomes_the_option_kind():
    call = option_contract(NIFTY_CALL, Exchange.NSE)
    put = option_contract(
        ContractKey("NIFTY", InstrumentClass.OPTION, date(2026, 8, 4), Decimal("24550"), "PE"),
        Exchange.NSE,
    )
    assert call.option_kind == OptionKind.CALL
    assert put.option_kind == OptionKind.PUT


def test_the_strike_is_exact_not_coerced_through_a_double():
    """`Price(value, precision)` takes a C double. A strike must arrive
    through `from_str` or it routes through binary floating point."""
    key = ContractKey("NIFTY", InstrumentClass.OPTION, date(2026, 8, 4), Decimal("24550.05"), "CE")
    contract = option_contract(key, Exchange.NSE)
    assert contract.strike_price.as_decimal() == Decimal("24550.05")


def test_margin_defaults_to_zero_rather_than_a_plausible_percentage():
    """The venue's margin model is the authority. A default that looks
    reasonable is how a run silently reports a margin nobody chose."""
    contract = option_contract(NIFTY_CALL, Exchange.NSE)
    assert contract.margin_init == Decimal("0")
    assert contract.margin_maint == Decimal("0")


def test_provenance_travels_with_the_contract():
    """A run that leaned on an interpolated lot size must be able to say so."""
    contract = option_contract(NIFTY_CALL, Exchange.NSE)
    assert contract.info["lot_size"] == 65
    assert "lot_size_source" in contract.info


def test_a_future_carries_the_same_lot_in_its_multiplier():
    contract = futures_contract(
        ContractKey("NIFTY", InstrumentClass.FUTURE, date(2026, 6, 30)), Exchange.NSE
    )
    assert contract.multiplier == Quantity.from_int(65)
    assert contract.lot_size == Quantity.from_int(1)


def test_an_equity_is_a_one_paisa_tick_and_needs_no_expiry():
    stock = equity("RELIANCE", Exchange.NSE)
    assert stock.price_increment == Price.from_str("0.01")
    assert stock.id.symbol.value == "RELIANCE"
    assert stock.id.venue.value == "NSE"


def test_an_index_is_a_reference_not_a_tradeable_contract():
    """It exists so a signal can subscribe to spot. Nothing routes to it."""
    nifty = index_instrument("NIFTY", Exchange.NSE)
    assert nifty.id.symbol.value == "NIFTY"
    assert nifty.price_increment == Price.from_str("0.01")


def test_the_squaring_trap_shown_in_rupees():
    """What the multiplier convention is worth, arithmetically.

    One lot of NIFTY at a premium of 100 is 65 units, so Rs 6,500. Pass the
    lot size as the QUANTITY as well and Nautilus prices 65 contracts of a
    65-multiplier instrument -- 4,225 units, Rs 422,500, exactly 65x too
    much. That is the error that made a true Rs 1,209 round trip read as
    Rs 78,585.

    Nothing downstream catches it: the number is plausible, the sign is
    right, and every trade is wrong by the same factor.
    """
    contract = option_contract(NIFTY_CALL, Exchange.NSE)
    correct = contract.notional_value(Quantity.from_int(1), Price.from_str("100"))
    squared = contract.notional_value(Quantity.from_int(65), Price.from_str("100"))
    assert correct.as_decimal() == Decimal("6500.00")
    assert squared.as_decimal() == Decimal("422500.00")
    assert squared.as_decimal() == correct.as_decimal() * 65


def test_a_caller_may_supply_a_lot_size_our_table_does_not_have():
    """Our table is the historical record and covers only the underlyings
    whose bhavcopy has been harvested. A broker's live instrument master
    lists the lot the exchange is using TODAY, for every contract.

    Without this an adapter could mint NIFTY contracts and nothing else --
    which is exactly what happened, silently, until a provider test caught
    it: every option and future was dropped because `lot_size` raised.
    """
    from nautilus_india.core.lots import UnknownLotSizeError

    key = ContractKey("RELIANCE", InstrumentClass.OPTION, date(2026, 9, 24), Decimal("1400"), "CE")
    with pytest.raises(UnknownLotSizeError):
        option_contract(key, Exchange.NSE)

    supplied = option_contract(key, Exchange.NSE, lot_size=500)
    assert supplied.multiplier == Quantity.from_int(500)
    assert supplied.info["lot_size"] == 500
    assert supplied.info["lot_size_source"] == "supplied by the caller"


def test_the_supplied_lot_size_says_it_was_supplied():
    """Provenance survives the override, so a run that used a broker's
    number rather than the archive's can say so."""
    contract = option_contract(NIFTY_CALL, Exchange.NSE, lot_size=65)
    assert contract.info["lot_size_source"] == "supplied by the caller"
    assert option_contract(NIFTY_CALL, Exchange.NSE).info["lot_size_source"] != (
        "supplied by the caller"
    )
