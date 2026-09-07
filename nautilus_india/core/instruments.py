"""Indian contracts, as NautilusTrader instruments.

WHY `multiplier` CARRIES THE LOT AND `quantity` IS IN LOTS. Nautilus prices a
fill as `qty * multiplier * price`. Passing the lot size as BOTH -- the
obvious first mistake, and one this codebase's ancestor actually made --
squares it: a NIFTY 24550 CE round trip came back at Rs 78,585 against a true
Rs 1,209, because 65 lots of a 65-multiplier contract is 4,225 units. So
`lot_size` is 1 (one contract is one lot) and the 65 lives in `multiplier`.

LOT SIZE IS LOOKED UP, NEVER INLINED. NIFTY has been 50, 25, 75 and 65.
`lots.py` owns the table and its provenance, and the lot travels
per-instrument here so a contract cannot be priced at another series' lot.

MARGIN DEFAULTS TO ZERO, not to a plausible-looking percentage. The venue's
margin model is the authority; a default that looks reasonable is how a run
silently reports a margin nobody chose.

PRICES ARE BUILT WITH `from_str`. `Price(value, precision)` takes a C double,
so passing a Decimal positionally still routes the strike through binary
floating point. `from_str` is the only exact path.
"""

from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal

from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model.currencies import INR
from nautilus_trader.model.enums import AssetClass, OptionKind
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import (
    Equity,
    FuturesContract,
    IndexInstrument,
    OptionContract,
)
from nautilus_trader.model.objects import Price, Quantity

from nautilus_india.core.calendar import IST
from nautilus_india.core.enums import Exchange, InstrumentClass
from nautilus_india.core.lots import lot_size, lot_size_source
from nautilus_india.core.symbology import ContractKey, to_instrument_id, to_symbol

# Source: NSE F&O contract specification, tick size Rs 0.05. Declared with
# its source rather than inlined: a wrong tick silently rounds every fill.
TICK = Price.from_str("0.05")
# Cash and indices print to the paisa.
CASH_TICK = Price.from_str("0.01")
INDEX_TICK = Price.from_str("0.01")
PRICE_PRECISION = 2

# Derivatives stop trading at the 15:30 close, not at midnight -- midnight
# would keep a dead contract alive for the whole expiry afternoon.
_EXPIRY_TIME = time(15, 30)
_ACTIVATION_TIME = time(9, 15)

_KIND = {"CE": OptionKind.CALL, "PE": OptionKind.PUT}


def _window(key: ContractKey) -> tuple[int, int]:
    activation = datetime.combine(key.expiry, _ACTIVATION_TIME, tzinfo=IST)
    expiration = datetime.combine(key.expiry, _EXPIRY_TIME, tzinfo=IST)
    return dt_to_unix_nanos(activation), dt_to_unix_nanos(expiration)


def _lot_info(key: ContractKey, override: int | None = None) -> tuple[int, dict]:
    """The lot and where it came from.

    `override` is for a caller holding a better source than our table -- a
    broker's live instrument master, which lists the lot the exchange is
    using TODAY. Our table is the historical record and covers only the
    underlyings whose bhavcopy has been harvested, so without this an
    adapter could mint contracts for NIFTY and nothing else.
    """
    if override is not None:
        return override, {"lot_size": override, "lot_size_source": "supplied by the caller"}
    lots = lot_size(key.underlying, key.expiry)
    return lots, {
        "lot_size": lots,
        "lot_size_source": lot_size_source(key.underlying, key.expiry),
    }


def option_contract(
    key: ContractKey,
    exchange: Exchange,
    *,
    ts_init: int = 0,
    margin_init: Decimal = Decimal("0"),
    lot_size: int | None = None,
) -> OptionContract:
    """One listed option series."""
    if key.instrument_class is not InstrumentClass.OPTION:
        raise ValueError(f"expected an OPTION, got {key.instrument_class.value}")
    lots, info = _lot_info(key, lot_size)
    activation_ns, expiration_ns = _window(key)
    return OptionContract(
        instrument_id=to_instrument_id(key, exchange),
        raw_symbol=Symbol(to_symbol(key)),
        asset_class=AssetClass.INDEX,
        currency=INR,
        price_precision=PRICE_PRECISION,
        price_increment=TICK,
        multiplier=Quantity.from_int(lots),
        lot_size=Quantity.from_int(1),  # one contract = one lot; see docstring
        underlying=key.underlying,
        option_kind=_KIND[key.right],
        # `Price(value, precision)` takes a C double, so a Decimal would be
        # coerced through binary floating point on the way in. `from_str`
        # is the only exact path -- see the decimal-never-float rule.
        strike_price=Price.from_str(f"{key.strike:.{PRICE_PRECISION}f}"),
        activation_ns=activation_ns,
        expiration_ns=expiration_ns,
        ts_event=ts_init,
        ts_init=ts_init,
        margin_init=margin_init,
        margin_maint=margin_init,
        exchange=exchange.value,
        info=info,
    )


def futures_contract(
    key: ContractKey,
    exchange: Exchange,
    *,
    ts_init: int = 0,
    margin_init: Decimal = Decimal("0"),
    lot_size: int | None = None,
) -> FuturesContract:
    """One listed futures series."""
    if key.instrument_class is not InstrumentClass.FUTURE:
        raise ValueError(f"expected a FUTURE, got {key.instrument_class.value}")
    lots, info = _lot_info(key, lot_size)
    activation_ns, expiration_ns = _window(key)
    return FuturesContract(
        instrument_id=to_instrument_id(key, exchange),
        raw_symbol=Symbol(to_symbol(key)),
        asset_class=AssetClass.INDEX,
        currency=INR,
        price_precision=PRICE_PRECISION,
        price_increment=TICK,
        multiplier=Quantity.from_int(lots),
        lot_size=Quantity.from_int(1),
        underlying=key.underlying,
        activation_ns=activation_ns,
        expiration_ns=expiration_ns,
        ts_event=ts_init,
        ts_init=ts_init,
        margin_init=margin_init,
        margin_maint=margin_init,
        exchange=exchange.value,
        info=info,
    )


def equity(underlying: str, exchange: Exchange, *, ts_init: int = 0) -> Equity:
    """One listed cash-market share."""
    key = ContractKey(underlying, InstrumentClass.EQUITY)
    return Equity(
        instrument_id=to_instrument_id(key, exchange),
        raw_symbol=Symbol(to_symbol(key)),
        currency=INR,
        price_precision=PRICE_PRECISION,
        price_increment=CASH_TICK,
        lot_size=Quantity.from_int(1),
        ts_event=ts_init,
        ts_init=ts_init,
    )


def index_instrument(underlying: str, exchange: Exchange, *, ts_init: int = 0) -> IndexInstrument:
    """The index itself: a reference for signals, not a tradeable contract."""
    key = ContractKey(underlying, InstrumentClass.INDEX)
    return IndexInstrument(
        instrument_id=to_instrument_id(key, exchange),
        raw_symbol=Symbol(to_symbol(key)),
        currency=INR,
        price_precision=PRICE_PRECISION,
        size_precision=0,
        price_increment=INDEX_TICK,
        size_increment=Quantity.from_int(1),
        ts_event=ts_init,
        ts_init=ts_init,
    )
