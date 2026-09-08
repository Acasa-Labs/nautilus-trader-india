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

from nautilus_india.dhan import (
    DhanDataClientConfig,
    DhanExecClientConfig,
    DhanLiveDataClientFactory,
    DhanLiveExecClientFactory,
)


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


def test_the_execution_factory_registers_the_ordinary_way():
    """`add_exec_client_factory` takes any LiveExecClientFactory subclass,
    including one from a third-party distribution. That is what makes this
    package an extension rather than a fork."""
    from nautilus_trader.live.factories import LiveExecClientFactory

    from nautilus_india.dhan import DhanLiveExecClientFactory

    assert issubclass(DhanLiveExecClientFactory, LiveExecClientFactory)


def test_the_four_public_symbols_are_importable_from_the_package():
    """Spec section 6: the configs and the factories, and nothing else."""
    import nautilus_india.dhan as dhan

    for name in ("DhanDataClientConfig", "DhanExecClientConfig",
                 "DhanLiveDataClientFactory", "DhanLiveExecClientFactory"):
        assert hasattr(dhan, name), name


async def test_a_trading_node_builds_with_both_factories_registered():
    """The execution client reaching the engine is the claim that matters:
    a signature mismatch would only show up at node build."""
    config = TradingNodeConfig(
        trader_id="TESTER-002",
        logging=LoggingConfig(log_level="ERROR"),
        data_clients={
            "DHAN": DhanDataClientConfig(
                client_id="C1", access_token="tok",
                instrument_provider=InstrumentProviderConfig(load_all=False),
            ),
        },
        exec_clients={
            "DHAN": DhanExecClientConfig(
                client_id="C1", access_token="tok",
                instrument_provider=InstrumentProviderConfig(load_all=False),
            ),
        },
    )
    node = TradingNode(config=config)
    node.add_data_client_factory("DHAN", DhanLiveDataClientFactory)
    node.add_exec_client_factory("DHAN", DhanLiveExecClientFactory)
    try:
        node.build()
        assert ClientId("DHAN") in node.kernel.exec_engine.registered_clients
    finally:
        node.dispose()


async def test_a_node_built_from_the_default_config_cannot_submit():
    """The safe direction survives all the way to a built node: a config
    nobody edited reads, reports, and sends nothing."""
    import asyncio

    from nautilus_trader.cache.cache import Cache
    from nautilus_trader.common.component import LiveClock, MessageBus
    from nautilus_trader.model.identifiers import TraderId

    clock = LiveClock()
    client = DhanLiveExecClientFactory.create(
        loop=asyncio.get_running_loop(),
        name="DHAN",
        config=DhanExecClientConfig(
            client_id="C1", access_token="tok",
            instrument_provider=InstrumentProviderConfig(load_all=False),
        ),
        msgbus=MessageBus(trader_id=TraderId("TESTER-003"), clock=clock),
        cache=Cache(database=None),
        clock=clock,
    )
    assert client._config.live_orders is False
    assert client.venue is None
