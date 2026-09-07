"""The Dhan market-data client, and the pure converters underneath it.

EVERY PRICE GOES THROUGH `to_price`. Dhan puts prices on the wire as IEEE
float32, so 368.15 arrives as 368.1499938964844 before this package sees it.
`Decimal(str(raw))` would carry that noise into the book and make every
comparison against a strike miss by a ten-thousandth.

A ZERO PRICE IS "NO ORDER HERE", not a bid of zero. Dhan fills unused depth
levels with zeros. Emitting one would put a zero bid in the book and cross
every spread that looked at it, so a packet whose top of book is empty -- or
one-sided, which is common at the open and on illiquid strikes -- yields no
quote at all. Inventing the missing side would fabricate a spread that never
existed.

`ts_event` COMES FROM THE PACKET, `ts_init` FROM THE CLOCK. `ltt` is the
exchange's own stamp. Using arrival time for both would replay every recorded
run at the wrong instant, and the error is invisible live -- it only shows up
in a backtest that no longer matches.
"""

from __future__ import annotations

import asyncio

from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.model.data import QuoteTick, TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import ClientId, TradeId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Quantity

from nautilus_india.dhan.binary import to_price
from nautilus_india.dhan.config import DhanDataClientConfig
from nautilus_india.dhan.providers import DhanInstrumentProvider

_NANOS_PER_SECOND = 1_000_000_000


def _event_ns(packet: dict) -> int:
    """The exchange's own stamp, in nanoseconds."""
    return int(packet.get("ltt") or 0) * _NANOS_PER_SECOND


def trade_from_ticker(packet: dict, instrument: Instrument, ts_init: int) -> TradeTick | None:
    """A ticker or quote packet as a `TradeTick`, or None if it has no trade."""
    ltp = packet.get("ltp")
    if not ltp or ltp <= 0:
        # A zero last price is Dhan saying nothing has traded, not a trade
        # at zero.
        return None
    quantity = int(packet.get("ltq") or 0)
    return TradeTick(
        instrument_id=instrument.id,
        price=to_price(ltp, instrument.price_precision),
        size=Quantity.from_int(max(quantity, 1)),
        # Dhan does not say which side lifted. Claiming one would put a
        # fabricated aggressor into every recorded tape.
        aggressor_side=AggressorSide.NO_AGGRESSOR,
        trade_id=TradeId(f"{packet['security_id']}-{packet.get('ltt', 0)}-{quantity}"),
        ts_event=_event_ns(packet),
        ts_init=ts_init,
    )


def quote_from_full(packet: dict, instrument: Instrument, ts_init: int) -> QuoteTick | None:
    """The top of book from a full packet, or None when the book is empty."""
    depth = packet.get("depth") or []
    if not depth:
        return None
    top = depth[0]
    bid, ask = top.get("bid_price") or 0.0, top.get("ask_price") or 0.0
    if bid <= 0 or ask <= 0:
        # See the module docstring: zero means "no order here", and half a
        # book is not a quote.
        return None
    precision = instrument.price_precision
    return QuoteTick(
        instrument_id=instrument.id,
        bid_price=to_price(bid, precision),
        ask_price=to_price(ask, precision),
        bid_size=Quantity.from_int(max(int(top.get("bid_qty") or 0), 1)),
        ask_size=Quantity.from_int(max(int(top.get("ask_qty") or 0), 1)),
        ts_event=_event_ns(packet),
        ts_init=ts_init,
    )


class DhanDataClient(LiveMarketDataClient):
    """Streams Dhan's binary feed into the Nautilus message bus."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: DhanDataClientConfig,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
    ) -> None:
        provider = DhanInstrumentProvider(
            config=config.instrument_provider, url=config.scrip_master_url
        )
        super().__init__(
            loop=loop,
            client_id=ClientId(name),
            # This client serves NSE, BSE and MCX at once, so it is not
            # bound to one venue -- the instruments carry theirs.
            venue=None,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            config=config,
        )
        self._config = config
        self._provider = provider
