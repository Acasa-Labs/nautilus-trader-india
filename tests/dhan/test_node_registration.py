"""The out-of-tree hook, exercised against a real TradingNode.

This is the assertion that the whole "extension, not a fork" claim rests
on: `add_data_client_factory` accepts a factory from a third-party
distribution and the engine ends up holding our client. Everything else in
the adapter could be perfect and this could still fail on a signature
mismatch, which would only show up at node build.

No socket is opened -- the provider is configured not to load.

The tests are async because `TradingNode.__init__` calls
`asyncio.get_event_loop()`, and a sync test under pytest-asyncio's auto
mode has no current loop. An async test has a running one.
"""

from nautilus_trader.config import (
    InstrumentProviderConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import ClientId

from nautilus_india.dhan import DhanDataClientConfig, DhanLiveDataClientFactory


def _node() -> TradingNode:
    config = TradingNodeConfig(
        trader_id="TESTER-001",
        logging=LoggingConfig(log_level="ERROR"),
        data_clients={
            "DHAN": DhanDataClientConfig(
                client_id="C1",
                access_token="tok",
                instrument_provider=InstrumentProviderConfig(load_all=False),
            ),
        },
        exec_clients={},
    )
    node = TradingNode(config=config)
    node.add_data_client_factory("DHAN", DhanLiveDataClientFactory)
    return node


async def test_a_trading_node_builds_with_our_factory_registered():
    node = _node()
    try:
        node.build()
        assert ClientId("DHAN") in node.kernel.data_engine.registered_clients
    finally:
        node.dispose()


async def test_the_client_is_not_bound_to_a_single_venue():
    """One Dhan connection serves NSE, BSE and MCX. Binding the client to a
    venue would need three clients for one socket, and the instruments
    already carry their own venue.

    Built through the factory rather than read back off the engine:
    `LiveDataEngine` exposes no public client accessor, and reaching into
    `_clients` would couple this test to Nautilus internals that can move
    between releases.
    """
    import asyncio

    from nautilus_trader.cache.cache import Cache
    from nautilus_trader.common.component import LiveClock, MessageBus
    from nautilus_trader.model.identifiers import TraderId

    clock = LiveClock()
    client = DhanLiveDataClientFactory.create(
        loop=asyncio.get_running_loop(),
        name="DHAN",
        config=DhanDataClientConfig(
            client_id="C1",
            access_token="tok",
            instrument_provider=InstrumentProviderConfig(load_all=False),
        ),
        msgbus=MessageBus(trader_id=TraderId("TESTER-001"), clock=clock),
        cache=Cache(database=None),
        clock=clock,
    )
    assert client.venue is None
    assert client.id == ClientId("DHAN")


def test_no_order_can_be_placed_by_this_plan():
    """Plan 2 ships no execution client. Nothing here can send an order,
    and that is deliberate -- the order path gets its own review gate."""
    import nautilus_india.dhan as dhan

    assert not hasattr(dhan, "DhanLiveExecClientFactory")
    assert not hasattr(dhan, "DhanExecClientConfig")
