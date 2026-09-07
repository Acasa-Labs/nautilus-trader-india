from datetime import date
from decimal import Decimal

import pytest
from nautilus_trader.model.identifiers import InstrumentId

from nautilus_india.core.enums import Exchange, InstrumentClass
from nautilus_india.core.symbology import (
    ContractKey,
    SymbolFormatError,
    parse_symbol,
    to_instrument_id,
    to_symbol,
)

NIFTY_CALL = ContractKey(
    underlying="NIFTY",
    instrument_class=InstrumentClass.OPTION,
    expiry=date(2026, 8, 4),
    strike=Decimal("24550"),
    right="CE",
)


def test_an_option_encodes_underlying_expiry_strike_in_paise_and_right():
    """Strike is in PAISE, zero-padded to nine digits.

    Paise so a half-strike cannot collide with its neighbour -- 87.50 and
    87.55 are distinct integers but round to the same rupee. Zero-padded so
    symbols sort by strike.
    """
    assert to_symbol(NIFTY_CALL) == "NIFTY260804002455000CE"


def test_a_half_strike_does_not_collide_with_its_neighbour():
    a = ContractKey("USDINR", InstrumentClass.OPTION, date(2026, 8, 26), Decimal("87.50"), "CE")
    b = ContractKey("USDINR", InstrumentClass.OPTION, date(2026, 8, 26), Decimal("87.55"), "CE")
    assert to_symbol(a) != to_symbol(b)


def test_a_future_carries_its_expiry():
    key = ContractKey("NIFTY", InstrumentClass.FUTURE, date(2026, 9, 24), None, None)
    assert to_symbol(key) == "NIFTY260924FUT"


def test_cash_and_index_are_the_listed_name():
    equity = ContractKey("RELIANCE", InstrumentClass.EQUITY, None, None, None)
    index = ContractKey("NIFTY", InstrumentClass.INDEX, None, None, None)
    assert to_symbol(equity) == "RELIANCE"
    assert to_symbol(index) == "NIFTY"


@pytest.mark.parametrize(
    "key",
    [
        NIFTY_CALL,
        ContractKey("NIFTY", InstrumentClass.OPTION, date(2026, 8, 4), Decimal("24550"), "PE"),
        ContractKey("USDINR", InstrumentClass.OPTION, date(2026, 8, 26), Decimal("87.50"), "CE"),
        ContractKey("NIFTY", InstrumentClass.FUTURE, date(2026, 9, 24), None, None),
        ContractKey("CRUDEOIL", InstrumentClass.FUTURE, date(2026, 9, 18), None, None),
        ContractKey("RELIANCE", InstrumentClass.EQUITY, None, None, None),
    ],
)
def test_every_symbol_round_trips(key):
    """symbol -> key -> symbol is the identity, per instrument class.

    This is the assertion the whole broker-neutral design rests on. If a
    symbol does not round-trip, two brokers will disagree about which
    contract an InstrumentId names, and the disagreement will surface as a
    fill on the wrong strike.
    """
    assert parse_symbol(to_symbol(key)) == key


def test_the_strike_survives_as_an_exact_decimal():
    """87.50 does not survive a round trip through binary floating point.

    Parsing back to a float would give 87.50000000000001 on some values,
    and an equality check against the chain would silently miss.
    """
    key = ContractKey("USDINR", InstrumentClass.OPTION, date(2026, 8, 26), Decimal("87.50"), "CE")
    assert parse_symbol(to_symbol(key)).strike == Decimal("87.50")


def test_a_float_strike_is_refused():
    with pytest.raises(SymbolFormatError, match="Decimal"):
        to_symbol(
            ContractKey("NIFTY", InstrumentClass.OPTION, date(2026, 8, 4), 24550.0, "CE")
        )


def test_a_strike_finer_than_a_paisa_is_refused_rather_than_rounded():
    """Rounding here would map two distinct contracts onto one symbol, which
    is the collision this encoding exists to prevent."""
    with pytest.raises(SymbolFormatError, match="paise"):
        to_symbol(
            ContractKey(
                "NIFTY", InstrumentClass.OPTION, date(2026, 8, 4), Decimal("24550.005"), "CE"
            )
        )


def test_an_option_without_a_strike_is_refused():
    with pytest.raises(SymbolFormatError):
        to_symbol(ContractKey("NIFTY", InstrumentClass.OPTION, date(2026, 8, 4), None, "CE"))


def test_a_bad_right_is_refused():
    with pytest.raises(SymbolFormatError, match="CE or PE"):
        to_symbol(
            ContractKey("NIFTY", InstrumentClass.OPTION, date(2026, 8, 4), Decimal("24550"), "XX")
        )


def test_the_instrument_id_carries_the_exchange_not_the_broker():
    assert to_instrument_id(NIFTY_CALL, Exchange.NSE) == InstrumentId.from_str(
        "NIFTY260804002455000CE.NSE"
    )


def test_the_same_contract_gets_the_same_id_whichever_broker_supplied_it():
    """Broker-neutrality, asserted.

    Kite would call this NIFTY26804245500CE and Dhan would call it 44321.
    Both must resolve to one InstrumentId or a strategy cannot move.
    """
    from_kite = ContractKey(
        "NIFTY", InstrumentClass.OPTION, date(2026, 8, 4), Decimal("24550"), "CE"
    )
    from_dhan = ContractKey(
        "NIFTY", InstrumentClass.OPTION, date(2026, 8, 4), Decimal("24550.00"), "CE"
    )
    assert to_instrument_id(from_kite, Exchange.NSE) == to_instrument_id(from_dhan, Exchange.NSE)


@pytest.mark.parametrize("ticker", ["RELIANCE", "GLANCE", "FORCE", "ALLIANCE", "PIPE", "SCOPE"])
def test_a_cash_ticker_ending_in_ce_or_pe_is_not_an_option(ticker):
    """RELIANCE ends in "CE".

    Dispatching on the last two characters alone classifies one of India's
    largest stocks as an option. The round-trip test caught it; this pins
    it, because the bug is silent -- an "option" with a garbage strike
    would be looked up, missed, and reported as an unknown contract rather
    than as a parsing defect.
    """
    key = parse_symbol(ticker)
    assert key.instrument_class is InstrumentClass.EQUITY
    assert key.underlying == ticker
    assert key.strike is None


def test_a_cash_ticker_ending_in_fut_is_not_a_future():
    """Same shape of bug on the futures branch."""
    key = parse_symbol("SOMEFUT")
    assert key.instrument_class is InstrumentClass.EQUITY
    assert key.underlying == "SOMEFUT"


def test_a_ce_suffix_with_digits_but_no_readable_date_is_still_cash():
    """Nine digits is not enough; the six before them must be a real date."""
    key = parse_symbol("ABC999999123456789CE")
    assert key.instrument_class is InstrumentClass.EQUITY


def test_an_option_shape_with_an_empty_underlying_is_not_an_option():
    """Every listed contract has an underlying. A symbol that is only a date
    and a strike names nothing."""
    key = parse_symbol("260804002455000CE")
    assert key.instrument_class is InstrumentClass.EQUITY
