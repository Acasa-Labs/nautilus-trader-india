"""The out-of-tree registration hook.

`TradingNode.add_data_client_factory(name, factory)` takes any
`LiveDataClientFactory` subclass, including one from a third-party
distribution. That is what makes this package an extension rather than a
fork.
"""

from __future__ import annotations

import asyncio

from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.live.factories import LiveDataClientFactory

from nautilus_india.dhan.config import DhanDataClientConfig
from nautilus_india.dhan.data import DhanDataClient


class DhanLiveDataClientFactory(LiveDataClientFactory):
    @staticmethod
    def create(
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: DhanDataClientConfig,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
    ) -> DhanDataClient:
        return DhanDataClient(
            loop=loop, name=name, config=config, msgbus=msgbus, cache=cache, clock=clock
        )
