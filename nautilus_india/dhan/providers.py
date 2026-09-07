"""Dhan's instrument universe, filtered to what can actually be traded.

THE MASTER SHIPS DEAD CONTRACTS. 24,161 of its rows were already expired on
2026-09-08 -- 13.8% of the file. Offering them gives Nautilus instruments it
will accept orders for and the exchange will reject. The clock is INJECTED
rather than read, so the filter is testable; a provider whose correctness
depends on the wall clock cannot be tested at all.

THE MASTER WINS ON TICK AND LOT. `core.lots` is the historical record, built
from NSE's bhavcopy archive; the master is what the exchange lists today.
They disagree -- NIFTY 2030-06-25 is 65 in the master and 75 in our table,
while seventeen other expiries agree -- and for anything about to be traded
the live listing is the authority.

DHAN'S ID IS NOT THE IDENTITY. `securityId` rides in `Instrument.info` and in
this provider's two maps. The `InstrumentId` stays `NIFTY...CE.NSE`, so the
same strategy runs on Kite. Putting the broker's id in the identity would
give one contract two names.

A FAILED DOWNLOAD RAISES. An empty provider is indistinguishable from a
market with no instruments, and every later subscription then fails with
"unknown instrument" rather than naming the real cause.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument

from nautilus_india.core.enums import InstrumentClass
from nautilus_india.core.instruments import (
    equity,
    futures_contract,
    index_instrument,
    option_contract,
)
from nautilus_india.dhan.constants import SCRIP_MASTER_URL
from nautilus_india.dhan.parsing import ScripRow, parse_row

# Dhan's (EXCH_ID, SEGMENT) -> the numeric code the binary feed header uses.
# Enumerated from the live master, which carries exactly ten combinations.
_SEGMENT_CODE: dict[tuple[str, str], int | None] = {
    ("NSE", "E"): 1, ("NSE", "D"): 2, ("NSE", "C"): 3, ("NSE", "I"): 0,
    ("BSE", "E"): 4, ("BSE", "C"): 7, ("BSE", "D"): 8, ("BSE", "I"): 0,
    ("MCX", "M"): 5,
    # NSE COMMODITY (23,870 OPTFUT rows) HAS NO PUBLISHED FEED CODE. Dhan's
    # own SDK enumerates 0,1,2,3,4,5,7,8 and defines no NSE-commodity
    # constant -- 6 is simply unassigned. So dhanhq cannot subscribe to
    # these either.
    #
    # Mapped to None rather than guessed at 6. A wrong segment code names a
    # different instrument, and Dhan answers 200 with empty data for one
    # that does not exist -- so the guess would not fail, it would look like
    # a market with no trades. These contracts are still built and listed,
    # because they are real and a caller may want their specification; they
    # are simply not routable, and `instrument_id_for` will not find them.
    ("NSE", "M"): None,
}

# What `Instrument.info["segment_code"]` carries when the feed code is not
# known. Negative so it can never be mistaken for IDX, which is 0.
UNKNOWN_SEGMENT_CODE = -1

DOWNLOAD_TIMEOUT = httpx.Timeout(120.0, connect=10.0)

# Money conventionally prints to the paisa, so nothing goes below two places
# even when the tick would allow it -- an MCX tick of Rs 0.50 needs one, and
# quoting gold to one decimal would look like a defect.
_MINIMUM_PRICE_PRECISION = 2


def price_precision_for(tick_size: Decimal) -> int:
    """How many decimals a price needs, derived from its own tick.

    NOT a constant. `core` hardcodes two places, which is right for equity
    and F&O and WRONG for currency: NSE currency derivatives tick at
    Rs 0.0025, and formatting that to two decimals gives "0.00" -- a zero
    tick, which Nautilus rejects outright. It was caught by loading the real
    master, where USDINR is the first row.

    The tick is normalised first because the master's own value carries
    trailing zeros: 0.2500 / 100 is 0.002500, whose exponent claims six
    places rather than four.
    """
    places = -tick_size.normalize().as_tuple().exponent
    return max(_MINIMUM_PRICE_PRECISION, int(places))



class DhanInstrumentProvider(InstrumentProvider):
    """Loads Dhan's scrip master and mints Nautilus instruments from it."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        config: InstrumentProviderConfig | None = None,
        *,
        url: str = SCRIP_MASTER_URL,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(config=config)
        self._client = client or httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT)
        self._url = url
        self._now = now or (lambda: datetime.now(UTC))
        self.now_ns = dt_to_unix_nanos(self._now())
        self._security_ids: dict[InstrumentId, str] = {}
        self._by_security: dict[tuple[str, int], InstrumentId] = {}

    # -- the map ---------------------------------------------------------

    def security_id_for(self, instrument_id: InstrumentId) -> str:
        """Dhan's id for a contract. Raises rather than guessing."""
        try:
            return self._security_ids[instrument_id]
        except KeyError:
            raise LookupError(
                f"{instrument_id} was not loaded from Dhan's scrip master, so "
                "this adapter has no securityId for it. Refusing to guess: "
                "Dhan answers 200 with empty data for an unknown id, which "
                "reads as a quiet market rather than as an error."
            ) from None

    def instrument_id_for(self, security_id: str, segment: int) -> InstrumentId | None:
        """The reverse lookup, for routing an inbound packet."""
        return self._by_security.get((str(security_id), int(segment)))

    # -- loading ---------------------------------------------------------

    async def load_all_async(self, filters: dict | None = None) -> None:
        text = await self._download()
        self.now_ns = dt_to_unix_nanos(self._now())
        today = self._now().date()

        for row in csv.DictReader(io.StringIO(text)):
            parsed = parse_row(row)
            if parsed is None:
                continue
            if parsed.expiry is not None and parsed.expiry < today:
                continue  # see the module docstring on dead contracts
            if filters and not self._matches(parsed, filters):
                continue
            instrument = self._build(parsed)
            if instrument is None:
                continue
            self.add(instrument)
            self._security_ids[instrument.id] = parsed.security_id
            code = _segment_code_for(parsed)
            if code is not None:
                self._by_security[(parsed.security_id, code)] = instrument.id

    async def _download(self) -> str:
        try:
            response = await self._client.get(self._url)
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"could not download Dhan's scrip master from {self._url}: {exc}"
            ) from exc
        if response.status_code != 200:
            raise RuntimeError(
                f"Dhan's scrip master answered {response.status_code}. Refusing to "
                "continue with no instruments: an empty provider looks exactly like "
                "a market with no contracts, and every later subscription would fail "
                "with 'unknown instrument' instead of naming this."
            )
        return response.text

    @staticmethod
    def _matches(parsed: ScripRow, filters: dict) -> bool:
        underlyings = filters.get("underlyings")
        if underlyings and parsed.key.underlying not in underlyings:
            return False
        classes = filters.get("instrument_classes")
        return not (classes and parsed.key.instrument_class not in classes)

    def _build(self, parsed: ScripRow) -> Instrument | None:
        """A Nautilus instrument carrying the master's own tick and lot."""
        cls = parsed.key.instrument_class
        # The master carries the lot for every contract it lists, so it is
        # passed in rather than looked up. `core.lots` covers only the
        # underlyings whose bhavcopy has been harvested -- letting it decide
        # here would drop every derivative except NIFTY, which is precisely
        # what an earlier version of this method did, silently.
        if cls is InstrumentClass.OPTION:
            built = option_contract(
                parsed.key, parsed.exchange, ts_init=self.now_ns, lot_size=parsed.lot_size
            )
        elif cls is InstrumentClass.FUTURE:
            built = futures_contract(
                parsed.key, parsed.exchange, ts_init=self.now_ns, lot_size=parsed.lot_size
            )
        elif cls is InstrumentClass.EQUITY:
            built = equity(parsed.key.underlying, parsed.exchange, ts_init=self.now_ns)
        else:
            built = index_instrument(parsed.key.underlying, parsed.exchange, ts_init=self.now_ns)
        return _with_master_values(built, parsed)


def _segment_code_for(parsed: ScripRow) -> int | None:
    """The feed's numeric segment, or None when Dhan publishes none."""
    return _SEGMENT_CODE.get((parsed.exchange.value, parsed.segment))


def _with_master_values(built: Instrument, parsed: ScripRow) -> Instrument:
    """Rebuild `built` with the exchange's own tick and lot, plus provenance.

    Nautilus instruments are immutable, so this reconstructs through
    `to_dict`/`from_dict` -- the only supported way to make a modified copy,
    since the constructors differ per class. Reconstructing rather than
    mutating also keeps the invariant that an instrument is valid the moment
    it exists.

    Only keys already present are replaced. `Equity` and `IndexInstrument`
    carry no `multiplier`, and adding one would build a dict their
    constructor does not accept.
    """
    kind = type(built)
    data = kind.to_dict(built)

    precision = price_precision_for(parsed.tick_size)
    data["price_precision"] = precision
    data["price_increment"] = f"{parsed.tick_size:.{precision}f}"
    if "multiplier" in data:
        data["multiplier"] = str(parsed.lot_size)

    info = dict(built.info or {})
    info.update(
        security_id=parsed.security_id,
        segment=parsed.segment,
        segment_code=_segment_code_for(parsed) if _segment_code_for(parsed) is not None
        else UNKNOWN_SEGMENT_CODE,
        lot_size_from_master=parsed.lot_size,
        tick_size_from_master=str(parsed.tick_size.normalize()),
    )
    data["info"] = info
    return kind.from_dict(data)
