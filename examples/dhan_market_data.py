"""Stream live NSE data from Dhan into a NautilusTrader node.

    export DHAN_CLIENT_ID=...
    export DHAN_ACCESS_TOKEN=...
    python examples/dhan_market_data.py

THIS OPENS A REAL SOCKET and downloads a 32 MB instrument master, so CI does
not run it. Everything it exercises has unit tests that open nothing.

It places no orders. Plan 2 ships no execution client at all -- see
CLAUDE.md on the two switches that will gate one when it lands.
"""

from __future__ import annotations

import os

from nautilus_trader.config import (
    InstrumentProviderConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode

from nautilus_india.dhan import DhanDataClientConfig, DhanLiveDataClientFactory


def main() -> None:
    missing = [k for k in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"set {' and '.join(missing)} first")

    config = TradingNodeConfig(
        trader_id="EXAMPLE-001",
        logging=LoggingConfig(log_level="INFO"),
        data_clients={
            "DHAN": DhanDataClientConfig(
                # Left as None they resolve from the environment at connect
                # time, which keeps the token out of a serialised config.
                client_id=None,
                access_token=None,
                instrument_provider=InstrumentProviderConfig(
                    load_all=True,
                    # The master carries ~200,000 contracts. Narrowing it is
                    # the difference between a two-second start and holding
                    # every Indian contract in the cache.
                    filters={"underlyings": {"NIFTY"}},
                ),
            ),
        },
        exec_clients={},
    )

    node = TradingNode(config=config)
    node.add_data_client_factory("DHAN", DhanLiveDataClientFactory)
    node.build()
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
