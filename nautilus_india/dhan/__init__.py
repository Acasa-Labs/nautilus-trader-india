"""The Dhan adapter: configs and factories.

Register with a `TradingNode` the ordinary way::

    node.add_data_client_factory("DHAN", DhanLiveDataClientFactory)

Nothing here imports the `dhanhq` SDK -- see CLAUDE.md for the three measured
reasons why.
"""

from nautilus_india.dhan.config import DhanDataClientConfig
from nautilus_india.dhan.factories import DhanLiveDataClientFactory

__all__ = ["DhanDataClientConfig", "DhanLiveDataClientFactory"]
