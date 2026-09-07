# nautilus-trader-india

NautilusTrader adapters for **Zerodha Kite** and **Dhan** — NSE, BSE and MCX.

> **Status: design approved, implementation not started.**
> See [the design spec](docs/superpowers/specs/2026-09-07-nautilus-trader-india-design.md).

An independent MIT package that installs alongside upstream `nautilus-trader`.
Not a fork.

```python
from nautilus_india.dhan import DhanLiveDataClientFactory, DhanLiveExecClientFactory

node.add_data_client_factory("DHAN", DhanLiveDataClientFactory)
node.add_exec_client_factory("DHAN", DhanLiveExecClientFactory)
```

## Live orders are gated behind two switches

Submission requires **both** `live_orders=True` on the config **and**
`NAUTILUS_INDIA_LIVE_ORDERS=1` in the environment. Two switches rather than
one, because a stray import cannot set an environment variable and a stray
environment variable cannot construct a client.

## Licence

MIT. NautilusTrader itself is LGPL-3.0; this package links against it and
contains none of its code.
