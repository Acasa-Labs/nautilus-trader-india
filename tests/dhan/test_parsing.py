"""The scrip master's four traps, each verified against the live file.

The fixture is captured from
https://images.dhan.co/api-data/api-scrip-master-detailed.csv rather than
written by hand, so it carries the venue's real shapes.
"""

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from nautilus_india.core.enums import InstrumentClass
from nautilus_india.dhan.parsing import (
    parse_expiry,
    parse_row,
    parse_strike,
    tick_size_rupees,
)

FIXTURE = Path(__file__).parent / "fixtures" / "scrip_master_sample.csv"


def rows():
    with FIXTURE.open() as fh:
        return list(csv.DictReader(fh))


# -- trap 1: TICK_SIZE is in PAISE ---------------------------------------


@pytest.mark.parametrize(
    ("raw", "rupees"),
    [
        ("5.0000", Decimal("0.05")),    # NSE F&O, 68188 rows
        ("1.0000", Decimal("0.01")),    # NSE/BSE equity
        ("0.2500", Decimal("0.0025")),  # NSE currency
        ("50.0000", Decimal("0.50")),   # MCX
        ("10.0000", Decimal("0.10")),
    ],
)
def test_tick_size_is_paise_and_must_be_divided_by_a_hundred(raw, rupees):
    """Verified across five exchange/segment combinations on 2026-09-08.

    Used raw, every NSE option would carry a Rs 5.00 tick -- 100x too large
    -- and every order would round to a multiple of five rupees. The failure
    is silent: the number still looks like a tick size.
    """
    assert tick_size_rupees(raw) == rupees


def test_the_nse_fno_tick_agrees_with_what_core_hardcodes():
    """Cross-check against the independently sourced value in core."""
    from nautilus_india.core.instruments import TICK

    assert tick_size_rupees("5.0000") == TICK.as_decimal()


# -- trap 2: expired contracts ship in the master ------------------------


def test_an_expired_contract_parses_to_a_real_past_date():
    """24,351 of 176,996 dated rows were already expired on 2026-09-08 --
    13.8% of the file. Parsing does not filter (it has no clock); the
    provider does, and it needs a real date to compare."""
    assert parse_expiry("2024-08-28") == date(2024, 8, 28)


def test_the_fixture_actually_contains_an_expired_row():
    """Guards the guard: if the capture ever loses its trap rows, the
    provider's filter would be tested against nothing."""
    expired = [r for r in rows() if (d := parse_expiry(r["SM_EXPIRY_DATE"])) and d < date(2026, 9, 8)]
    assert expired, "the fixture no longer carries an expired contract"


# -- trap 3: 0001-01-01 is a null-date sentinel --------------------------


def test_the_year_one_sentinel_is_not_a_date():
    """`0001-01-01` is the master's null expiry. Read literally it makes a
    contract that expired two thousand years ago, which sorts first in every
    listing and looks like data corruption rather than a sentinel."""
    assert parse_expiry("0001-01-01") is None
    assert parse_expiry("") is None
    assert parse_expiry("NA") is None


# -- trap 4: STRIKE_PRICE has three sentinels for non-options ------------


@pytest.mark.parametrize("raw", ["", "-0.01000", "0.00000"])
def test_a_non_option_strike_sentinel_becomes_none_not_a_number(raw):
    """All three appear in the live file on non-option rows. Read naively
    they give a ValueError, a negative strike, or a zero strike -- and zero
    is the dangerous one, because it looks like a real number."""
    assert parse_strike(raw) is None


def test_a_real_strike_is_an_exact_decimal():
    assert parse_strike("24550.00000") == Decimal("24550.00000")
    assert parse_strike("87.50000") == Decimal("87.50000")


# -- rows -> ContractKey -------------------------------------------------


def test_every_fixture_row_either_parses_or_is_declined_explicitly():
    """No row may parse into something half-built. Either we understand it
    or we return None."""
    for row in rows():
        parsed = parse_row(row)
        assert parsed is None or parsed.key is not None


def test_an_index_option_row_becomes_an_option_contract_key():
    row = next(r for r in rows() if r["INSTRUMENT"] == "OPTIDX")
    parsed = parse_row(row)
    assert parsed.key.instrument_class is InstrumentClass.OPTION
    assert parsed.key.right in ("CE", "PE")
    assert parsed.key.strike is not None
    assert parsed.security_id == row["SECURITY_ID"]


def test_an_equity_row_has_no_expiry_and_no_strike():
    row = next(r for r in rows() if r["INSTRUMENT"] == "EQUITY")
    parsed = parse_row(row)
    assert parsed.key.instrument_class is InstrumentClass.EQUITY
    assert parsed.key.expiry is None
    assert parsed.key.strike is None


def test_a_future_row_carries_an_expiry_but_no_strike():
    row = next(r for r in rows() if r["INSTRUMENT"] == "FUTIDX")
    parsed = parse_row(row)
    assert parsed.key.instrument_class is InstrumentClass.FUTURE
    assert parsed.key.expiry is not None
    assert parsed.key.strike is None
    assert parsed.key.right is None


def test_mcx_options_on_futures_are_options():
    """MCX lists OPTFUT -- options ON futures rather than on spot. They are
    options for every purpose this adapter has."""
    row = next(r for r in rows() if r["INSTRUMENT"] == "OPTFUT")
    assert parse_row(row).key.instrument_class is InstrumentClass.OPTION


def test_the_lot_size_comes_from_the_master_not_from_our_table():
    """The master is authoritative for LIVE trading; our own lots.py table is
    the historical record.

    They disagree: on 2026-09-08 the master gave NIFTY 2030-06-25 a lot of 65
    while our bhavcopy-derived table says 75. Seventeen of eighteen NIFTY
    expiries agreed, so this is a real divergence and not a parsing error. A
    live order must use what the exchange lists today.
    """
    row = next(r for r in rows() if r["INSTRUMENT"] == "OPTIDX")
    parsed = parse_row(row)
    assert parsed.lot_size == int(float(row["LOT_SIZE"]))
    assert parsed.lot_size > 0


def test_an_unknown_instrument_type_is_declined_not_guessed():
    """The master gains instrument types without warning."""
    row = dict(rows()[0])
    row["INSTRUMENT"] = "SOMETHINGNEW"
    assert parse_row(row) is None


def test_a_row_with_no_security_id_is_declined():
    """Dhan answers 200 with empty data for an unknown id, so a contract we
    cannot address would read as a quiet market rather than as an error."""
    row = dict(next(r for r in rows() if r["INSTRUMENT"] == "EQUITY"))
    row["SECURITY_ID"] = ""
    assert parse_row(row) is None


def test_an_index_with_a_zero_tick_is_still_offered():
    """GIFT Nifty carries TICK_SIZE 0.0000 in the live master.

    An index is a reference for signals, not a tradeable contract, so a zero
    tick means "not applicable" rather than "invalid". Declining it would
    silently drop an index a strategy may want to subscribe to -- which is
    the one thing an index row is for. Found by running the parser over all
    200,493 live rows: it was the only row declined.
    """
    row = {
        "EXCH_ID": "NSE", "SEGMENT": "I", "SECURITY_ID": "5024",
        "INSTRUMENT": "INDEX", "UNDERLYING_SYMBOL": "GIFTNIFTY",
        "SYMBOL_NAME": "GIFTNIFTY", "LOT_SIZE": "1.0",
        "TICK_SIZE": "0.0000", "SM_EXPIRY_DATE": "0001-01-01",
        "STRIKE_PRICE": "", "OPTION_TYPE": "",
    }
    parsed = parse_row(row)
    assert parsed is not None
    assert parsed.key.underlying == "GIFTNIFTY"
    assert parsed.tick_size == Decimal("0.01")


def test_a_tradeable_contract_with_a_zero_tick_is_still_declined():
    """The fallback is for indices only. A tradeable contract with no tick
    cannot be priced, and inventing one would round every order."""
    row = dict(next(r for r in rows() if r["INSTRUMENT"] == "OPTIDX"))
    row["TICK_SIZE"] = "0.0000"
    assert parse_row(row) is None
