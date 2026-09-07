"""Contract identity, derived from the contract -- never from a broker string.

THIS IS WHAT MAKES ONE STRATEGY RUN ON EITHER BROKER. Kite identifies a
contract by `instrument_token` (an int) and `tradingsymbol`; Dhan by
`securityId` (a string) and `exchangeSegment`. Neither is usable as a shared
identity. So the canonical symbol is built from what the contract IS --
underlying, expiry, strike, right -- and each adapter keeps its own broker id
in `Instrument.info` and in its provider's map.

STRIKE IS ENCODED IN PAISE, ZERO-PADDED TO NINE DIGITS. Paise because a
half-strike must not collide with its neighbour: USDINR 87.50 and 87.55 are
distinct contracts that round to the same rupee. Zero-padded so a symbol list
sorts by strike. Nine digits covers every listed Indian strike with room --
the largest today is BSE SENSEX in the 90,000s, which is 9,000,000 paise,
seven digits.

STRIKES ARE `Decimal`, NEVER `float`. `Decimal("87.50") * 100` is exactly
8750; `87.50 * 100` is 8750.000000000001 on some values, and rounding it
would map two contracts onto one symbol -- the exact collision this encoding
exists to prevent. A float strike is refused at the door.

PARSING CANNOT TELL AN EQUITY FROM AN INDEX, and deliberately does not try.
Both are the bare listed name, so `parse_symbol("NIFTY")` returns EQUITY. The
provider knows the class from the instrument master and passes it explicitly;
no caller parses a symbol to learn it. Adding a suffix to disambiguate would
change every index symbol for the benefit of a parse nobody performs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from nautilus_trader.model.identifiers import InstrumentId, Symbol

from nautilus_india.core.enums import Exchange, InstrumentClass

_RIGHTS = ("CE", "PE")
_FUTURE_SUFFIX = "FUT"
_EXPIRY_FORMAT = "%y%m%d"
_EXPIRY_LEN = 6
_STRIKE_DIGITS = 9


class SymbolFormatError(ValueError):
    """A contract could not be encoded, or a symbol could not be read."""


@dataclass(frozen=True, slots=True)
class ContractKey:
    """What a contract IS, independent of who is quoting it."""

    underlying: str
    instrument_class: InstrumentClass
    expiry: date | None = None
    strike: Decimal | None = None
    right: str | None = None


def _strike_to_paise(strike: object) -> int:
    if not isinstance(strike, Decimal):
        raise SymbolFormatError(
            f"strike must be a Decimal, got {type(strike).__name__}. A float "
            "strike cannot be encoded exactly, and rounding one maps two "
            "contracts onto one symbol."
        )
    paise = strike * 100
    if paise != paise.to_integral_value():
        raise SymbolFormatError(
            f"strike {strike} is finer than a paise. Refusing to round: two "
            "distinct contracts would collide on one symbol."
        )
    value = int(paise)
    if value < 0 or len(str(value)) > _STRIKE_DIGITS:
        raise SymbolFormatError(
            f"strike {strike} does not fit {_STRIKE_DIGITS} digits of paise"
        )
    return value


def to_symbol(key: ContractKey) -> str:
    """The canonical symbol for this contract."""
    cls = key.instrument_class

    if cls in (InstrumentClass.EQUITY, InstrumentClass.INDEX):
        return key.underlying.upper()

    if key.expiry is None:
        raise SymbolFormatError(f"a {cls.value} needs an expiry")

    stamp = f"{key.expiry:{_EXPIRY_FORMAT}}"

    if cls is InstrumentClass.FUTURE:
        return f"{key.underlying.upper()}{stamp}{_FUTURE_SUFFIX}"

    if key.right not in _RIGHTS:
        raise SymbolFormatError(f"right must be CE or PE, got {key.right!r}")
    if key.strike is None:
        raise SymbolFormatError("an OPTION needs a strike")
    paise = _strike_to_paise(key.strike)
    return f"{key.underlying.upper()}{stamp}{paise:0{_STRIKE_DIGITS}d}{key.right}"


def _try_date(stamp: str) -> date | None:
    """The expiry if `stamp` is one, else None. Used to TEST a shape."""
    try:
        return datetime.strptime(stamp, _EXPIRY_FORMAT).date()  # noqa: DTZ007
    except ValueError:
        return None


def _as_option(symbol: str) -> ContractKey | None:
    """An OPTION only if the WHOLE shape matches, not just the suffix.

    RELIANCE ends in "CE". Dispatching on the last two characters alone
    classifies one of India's largest stocks as an option -- caught by the
    round-trip test, and the reason this checks structure: right, then nine
    digits of strike, then a readable six-digit expiry, then a non-empty
    underlying. Anything less is a cash symbol that happens to end in CE.
    """
    right = symbol[-2:]
    if right not in _RIGHTS:
        return None
    head = symbol[:-2]
    digits = head[-_STRIKE_DIGITS:]
    if len(digits) < _STRIKE_DIGITS or not digits.isdigit():
        return None
    head = head[:-_STRIKE_DIGITS]
    expiry = _try_date(head[-_EXPIRY_LEN:]) if len(head) > _EXPIRY_LEN else None
    if expiry is None:
        return None
    return ContractKey(
        underlying=head[:-_EXPIRY_LEN],
        instrument_class=InstrumentClass.OPTION,
        expiry=expiry,
        strike=Decimal(int(digits)) / 100,
        right=right,
    )


def _as_future(symbol: str) -> ContractKey | None:
    """A FUTURE only if the whole shape matches. See `_as_option`."""
    if not symbol.endswith(_FUTURE_SUFFIX):
        return None
    head = symbol[: -len(_FUTURE_SUFFIX)]
    expiry = _try_date(head[-_EXPIRY_LEN:]) if len(head) > _EXPIRY_LEN else None
    if expiry is None:
        return None
    return ContractKey(
        underlying=head[:-_EXPIRY_LEN],
        instrument_class=InstrumentClass.FUTURE,
        expiry=expiry,
    )


def parse_symbol(symbol: str) -> ContractKey:
    """The inverse of `to_symbol`. Round-tripped by test, not by eye.

    Falls through to EQUITY, which is also what an INDEX looks like -- see
    the module docstring on why that is deliberate.
    """
    if not symbol:
        raise SymbolFormatError("empty symbol")

    return (
        _as_option(symbol)
        or _as_future(symbol)
        or ContractKey(underlying=symbol, instrument_class=InstrumentClass.EQUITY)
    )


def to_instrument_id(key: ContractKey, exchange: Exchange) -> InstrumentId:
    """The Nautilus id.

    The venue is the EXCHANGE, so a strategy written against it runs on
    either broker unchanged.
    """
    return InstrumentId(Symbol(to_symbol(key)), exchange.venue())
