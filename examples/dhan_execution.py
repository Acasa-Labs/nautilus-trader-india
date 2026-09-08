"""Place one order on Dhan through a NautilusTrader node.

    export DHAN_CLIENT_ID=...
    export DHAN_ACCESS_TOKEN=...
    python examples/dhan_execution.py            # denied, and shows you why
    NAUTILUS_INDIA_LIVE_ORDERS=1 \
    python examples/dhan_execution.py --live     # places a REAL order

RUN IT ONCE WITHOUT `--live` FIRST. It will be denied, and the denial names
both switches -- which is the fastest way to see the gate work before you
trust it with money.

THIS PLACES A REAL ORDER WITH REAL MONEY when both switches are set. There is
no paper mode here: Dhan's sandbox is a separate host and this example points
at production. The order below is a LIMIT far below the market so it rests
rather than fills, and the node cancels it on shutdown -- but read it before
you run it, because a resting buy is still an order the exchange can hit if
the market moves to it.

NOTHING IN THIS FILE HAS EVER BEEN RUN AGAINST A LIVE ACCOUNT. Placing an
order needs a whitelisted static IP, which the machine this package was
written on does not have. See docs/DHAN_API_NOTES.md.
"""

from __future__ import annotations

import os
import sys

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import (
    InstrumentProviderConfig,
    LoggingConfig,
    StrategyConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy

from nautilus_india.dhan import (
    DhanDataClientConfig,
    DhanExecClientConfig,
    DhanLiveDataClientFactory,
    DhanLiveExecClientFactory,
)


class OneOrder(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    limit_price: str


class PlaceOneOrder(Strategy):
    """Submit a single resting limit order and report what comes back."""

    def __init__(self, config: OneOrder) -> None:
        super().__init__(config)
        self.instrument_id = config.instrument_id
        self.limit_price = config.limit_price

    def on_start(self) -> None:
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            self.log.error(f"{self.instrument_id} is not in the instrument master")
            self.stop()
            return

        order = self.order_factory.limit(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            # ONE LOT. Nautilus counts contracts and Dhan counts units; the
            # adapter multiplies by the instrument's lot size on the way out,
            # so this is 1 contract and not 1 share.
            quantity=Quantity.from_int(1),
            # LIMIT, DELIBERATELY. Dhan turns an API MARKET order into a limit
            # with market-protection pricing, so a "market" order fills at a
            # price you did not choose and cannot see. Name your own.
            price=Price.from_str(self.limit_price),
            # Dhan's order endpoint takes DAY and IOC only. A GTC order is
            # sent as DAY, because NSE rests nothing overnight here -- the
            # GTT equivalent is a forever order, a different endpoint.
            time_in_force=TimeInForce.DAY,
            # Dhan's correlationId takes 25 characters (measured; the docs
            # say 30) and a default Nautilus id is 27, so the adapter sends a
            # deterministic hash instead. Passing a short id of your own keeps
            # it readable in Dhan's own order book.
            client_order_id=ClientOrderId("example-1"),
        )
        self.submit_order(order)
        self.log.info(f"submitted {order.client_order_id}", color=LogColor.BLUE)

    def on_order_denied(self, event) -> None:
        """Nothing was sent. The reason says what to change."""
        self.log.error(f"DENIED -- nothing reached Dhan: {event.reason}")
        self.stop()

    def on_order_accepted(self, event) -> None:
        self.log.info(f"ACCEPTED at Dhan as {event.venue_order_id}",
                      color=LogColor.GREEN)

    def on_order_rejected(self, event) -> None:
        """The venue said no to this order. Nothing is working."""
        self.log.warning(f"REJECTED by the venue: {event.reason}")

    def on_order_filled(self, event) -> None:
        self.log.info(f"FILLED {event.last_qty} @ {event.last_px}",
                      color=LogColor.GREEN)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        pass

    def on_stop(self) -> None:
        # An order left resting is an order the exchange can still hit.
        self.cancel_all_orders(self.instrument_id)


def main() -> None:
    live = "--live" in sys.argv
    missing = [k for k in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN")
               if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"set {' and '.join(missing)} first")

    # A contract that exists today. Change it: this one expires.
    instrument_id = InstrumentId.from_str("NIFTY260929002455000CE.NSE")

    config = TradingNodeConfig(
        trader_id="EXAMPLE-001",
        logging=LoggingConfig(log_level="INFO"),
        data_clients={
            "DHAN": DhanDataClientConfig(
                instrument_provider=InstrumentProviderConfig(
                    load_all=True, filters={"underlyings": {"NIFTY"}}),
            ),
        },
        exec_clients={
            "DHAN": DhanExecClientConfig(
                # SWITCH ONE. The other is NAUTILUS_INDIA_LIVE_ORDERS=1 in the
                # environment, and both are required. Two rather than one
                # because a stray import cannot set an environment variable
                # and a stray environment variable cannot construct a client.
                live_orders=live,
                instrument_provider=InstrumentProviderConfig(
                    load_all=True, filters={"underlyings": {"NIFTY"}}),
            ),
        },
    )

    node = TradingNode(config=config)
    node.add_data_client_factory("DHAN", DhanLiveDataClientFactory)
    node.add_exec_client_factory("DHAN", DhanLiveExecClientFactory)
    node.build()
    node.trader.add_strategy(PlaceOneOrder(OneOrder(
        instrument_id=instrument_id,
        # Far below the market so it rests rather than fills. Check it against
        # the real price before running: "far below" is only true today.
        limit_price="1.00",
    )))
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
