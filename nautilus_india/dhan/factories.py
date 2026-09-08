"""The out-of-tree registration hook.

`TradingNode.add_data_client_factory(name, factory)` and its execution
counterpart take any `LiveDataClientFactory` / `LiveExecClientFactory`
subclass, including ones from a third-party distribution. That is what makes
this package an extension rather than a fork.

REGISTERING THE EXECUTION FACTORY DOES NOT ARM IT. The client it builds still
needs `live_orders=True` and `NAUTILUS_INDIA_LIVE_ORDERS=1` before it will
send anything -- see `config.submission_refusal`.
"""

from __future__ import annotations

import asyncio

from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.live.factories import LiveDataClientFactory, LiveExecClientFactory

from nautilus_india.dhan.config import DhanDataClientConfig, DhanExecClientConfig
from nautilus_india.dhan.data import DhanDataClient
from nautilus_india.dhan.execution import DhanExecutionClient


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


class DhanLiveExecClientFactory(LiveExecClientFactory):
    @staticmethod
    def create(
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: DhanExecClientConfig,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
    ) -> DhanExecutionClient:
        return DhanExecutionClient(
            loop=loop, name=name, config=config, msgbus=msgbus, cache=cache, clock=clock
        )
