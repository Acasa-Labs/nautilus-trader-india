"""The Dhan adapter: configs and factories.

Register with a `TradingNode` the ordinary way::

    node.add_data_client_factory("DHAN", DhanLiveDataClientFactory)
    node.add_exec_client_factory("DHAN", DhanLiveExecClientFactory)

REGISTERING THE EXECUTION CLIENT DOES NOT ARM IT. Submission needs
`live_orders=True` on the config AND `NAUTILUS_INDIA_LIVE_ORDERS=1` in the
environment. Without both, orders are denied before anything is sent.

Nothing here imports the `dhanhq` SDK -- see CLAUDE.md for the three measured
reasons why.
"""

from nautilus_india.dhan.config import DhanDataClientConfig, DhanExecClientConfig
from nautilus_india.dhan.factories import (
    DhanLiveDataClientFactory,
    DhanLiveExecClientFactory,
)

__all__ = [
    "DhanDataClientConfig",
    "DhanExecClientConfig",
    "DhanLiveDataClientFactory",
    "DhanLiveExecClientFactory",
]
