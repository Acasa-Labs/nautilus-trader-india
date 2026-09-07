"""Dhan's scrip master, and the four things in it that silently lie.

The master is a 32 MB CSV of about 200,000 rows covering NSE, BSE and MCX.
Every trap below was verified against the live file on 2026-09-08.

TRAP 1 -- `TICK_SIZE` IS IN PAISE. `5.0000` means Rs 0.05, not Rs 5.00.
Confirmed across five exchange/segment combinations: NSE equity 1.0000 ->
Rs 0.01, NSE F&O 5.0000 -> Rs 0.05 (which matches the independently sourced
value in `core.instruments.TICK`), NSE currency 0.2500 -> Rs 0.0025, MCX
50.0000 -> Rs 0.50. Used raw, every NSE option carries a hundredfold tick and
every order rounds to a multiple of five rupees.

TRAP 2 -- EXPIRED CONTRACTS SHIP IN THE MASTER. 24,351 of 176,996 dated rows
were already expired on 2026-09-08, 13.8% of the file. The provider filters
them; parsing does not, because "expired" is a question about today and this
module has no clock.

TRAP 3 -- `0001-01-01` IS THE NULL EXPIRY. Read literally it makes a contract
that expired two thousand years ago, which sorts first in every listing and
looks like data corruption rather than a sentinel.

TRAP 4 -- `STRIKE_PRICE` HAS THREE SENTINELS on non-option rows: the empty
string, `-0.01000`, and `0.00000`. All three occur. Read naively they give a
ValueError, a negative strike, or a zero strike -- and zero is the dangerous
one, because it looks like a number a caller might use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from nautilus_india.core.enums import Exchange, InstrumentClass
from nautilus_india.core.symbology import ContractKey

# Dhan's INSTRUMENT column -> what kind of contract it is.
INSTRUMENT_CLASS: dict[str, InstrumentClass] = {
    "EQUITY": InstrumentClass.EQUITY,
    "INDEX": InstrumentClass.INDEX,
    "FUTIDX": InstrumentClass.FUTURE,
    "FUTSTK": InstrumentClass.FUTURE,
    "FUTCUR": InstrumentClass.FUTURE,
    "FUTCOM": InstrumentClass.FUTURE,
    "OPTIDX": InstrumentClass.OPTION,
    "OPTSTK": InstrumentClass.OPTION,
    "OPTCUR": InstrumentClass.OPTION,
    # MCX lists options ON FUTURES rather than on spot. They are options for
    # every purpose this adapter has, and the distinction lives in the
    # underlying, not in the contract's own shape.
    "OPTFUT": InstrumentClass.OPTION,
}

EXCHANGE_BY_ID = {"NSE": Exchange.NSE, "BSE": Exchange.BSE, "MCX": Exchange.MCX}

# The master's null expiry. See trap 3.
_NULL_EXPIRY = {"", "NA", "0001-01-01"}
# The master's "no strike here" values. See trap 4.
_NULL_STRIKE = {"", "NA", "-0.01000", "0.00000"}
_RIGHTS = {"CE", "PE"}

_PAISE_PER_RUPEE = Decimal(100)

# What an index prints to, when the master declines to say. See `parse_row`.
_INDEX_TICK = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class ScripRow:
    """One tradeable contract, as the master describes it."""

    key: ContractKey
    exchange: Exchange
    security_id: str
    segment: str
    lot_size: int
    tick_size: Decimal
    expiry: date | None


def tick_size_rupees(raw: str) -> Decimal:
    """`TICK_SIZE` in rupees. See trap 1 -- the column is PAISE."""
    return Decimal(raw) / _PAISE_PER_RUPEE


def parse_expiry(raw: str) -> date | None:
    """The expiry, or None for the master's several nulls. See trap 3."""
    value = (raw or "").strip()
    if value in _NULL_EXPIRY:
        return None
    try:
        return date.fromisoformat(value.split()[0])
    except ValueError:
        return None


def parse_strike(raw: str) -> Decimal | None:
    """The strike, or None for the master's three sentinels. See trap 4."""
    value = (raw or "").strip()
    if value in _NULL_STRIKE:
        return None
    try:
        strike = Decimal(value)
    except InvalidOperation:
        return None
    # A real strike is positive. A negative one is a sentinel we have not
    # catalogued, and guessing at it would mint a contract nobody lists.
    return strike if strike > 0 else None


def parse_row(row: dict[str, str]) -> ScripRow | None:
    """One master row as a `ScripRow`, or None when we do not understand it.

    Returning None rather than raising is deliberate: the master carries
    ~200,000 rows and gains instrument types without warning. One unknown row
    must not stop a provider loading the other 199,999 -- but it also must not
    become a half-built contract, so there is no middle outcome here.
    """
    instrument_class = INSTRUMENT_CLASS.get(row.get("INSTRUMENT", ""))
    exchange = EXCHANGE_BY_ID.get(row.get("EXCH_ID", ""))
    if instrument_class is None or exchange is None:
        return None

    security_id = (row.get("SECURITY_ID") or "").strip()
    if not security_id:
        return None

    underlying = (row.get("UNDERLYING_SYMBOL") or row.get("SYMBOL_NAME") or "").strip().upper()
    if not underlying:
        return None

    expiry = parse_expiry(row.get("SM_EXPIRY_DATE", ""))
    strike = parse_strike(row.get("STRIKE_PRICE", ""))
    right: str | None = (row.get("OPTION_TYPE") or "").strip().upper()

    if instrument_class is InstrumentClass.OPTION:
        if expiry is None or strike is None or right not in _RIGHTS:
            return None
    elif instrument_class is InstrumentClass.FUTURE:
        if expiry is None:
            return None
        strike, right = None, None
    else:
        expiry, strike, right = None, None, None

    try:
        lot_size = int(float(row.get("LOT_SIZE") or 0))
        tick_size = tick_size_rupees(row.get("TICK_SIZE") or "0")
    except (ValueError, InvalidOperation):
        return None

    if instrument_class is InstrumentClass.INDEX:
        # An index is a reference for signals, not a tradeable contract, so
        # it has no meaningful tick and the master sometimes says so with a
        # zero -- GIFTNIFTY does. Declining on that would silently drop an
        # index a strategy may want to subscribe to, which is the one thing
        # an index row is FOR. Fall back to the paisa an index prints to.
        tick_size = tick_size or _INDEX_TICK
        lot_size = lot_size or 1

    if lot_size <= 0 or tick_size <= 0:
        return None

    key = ContractKey(
        underlying=underlying,
        instrument_class=instrument_class,
        expiry=expiry,
        strike=strike,
        right=right or None,
    )
    return ScripRow(
        key=key,
        exchange=exchange,
        security_id=security_id,
        segment=(row.get("SEGMENT") or "").strip(),
        lot_size=lot_size,
        tick_size=tick_size,
        expiry=expiry,
    )
