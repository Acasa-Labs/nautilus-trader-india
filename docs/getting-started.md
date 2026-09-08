# Getting started

Ten minutes: install, price a real NIFTY option contract, then decide how far
you want to go from there.

**This is alpha software that places real orders with real money.** Nothing
below places one — the first two sections need no account at all — but read
[the README's gap table](../README.md) before the point where you do.

---

## Install

Python 3.12–3.14.

```bash
pip install nautilus-trader-india
```

Or from source, which is what you want if you intend to change anything:

```bash
git clone https://github.com/Acasa-Labs/nautilus-trader-india
cd nautilus-trader-india
uv sync --extra dev     # or: pip install -e ".[dev]"
uv run pytest           # 546 tests, a couple of seconds, no network
```

`nautilus-trader` itself comes along as a dependency. This package is **not a
fork** — it installs alongside upstream and registers through the ordinary
factory hooks, so `pip install -U nautilus-trader` keeps working.

**Pin an exact version.** The API will change without deprecation before 1.0.

---

## One minute: is it working?

No account, no network, no credentials.

```python
from datetime import date
from decimal import Decimal

from nautilus_india.core.enums import Exchange, InstrumentClass
from nautilus_india.core.instruments import option_contract
from nautilus_india.core.symbology import ContractKey

nifty_call = option_contract(
    ContractKey("NIFTY", InstrumentClass.OPTION, date(2026, 8, 4), Decimal("24550"), "CE"),
    Exchange.NSE,
)

print(nifty_call.id)               # NIFTY260804002455000CE.NSE
print(nifty_call.multiplier)       # 65   <- the lot lives here
print(nifty_call.lot_size)         # 1    <- one contract is one lot
print(nifty_call.price_increment)  # 0.05
print(nifty_call.quote_currency)   # INR
```

That is a stock Nautilus `OptionContract`, priced in INR, ticking at 5 paise,
expiring 15:30 IST, with the NSE lot size of 65 in `multiplier`.

**Two things to absorb before going further**, because both cost money if you
get them wrong:

**The venue is the exchange, not the broker.** `...NSE`, never `...DHAN`. A
strategy names where the contract trades, so the same strategy runs on either
broker unchanged. The broker is the `ClientId`.

**Quantity is in lots.** Nautilus prices a fill as `qty × multiplier × price`.
One lot of that call at a premium of ₹100 is 65 units — ₹6,500. Passing `65`
as the quantity prices 4,225 units at ₹422,500: exactly 65× too much.

---

## Five minutes: what a trade costs

Still no account. This is the part of the package most people can use today.

```python
from datetime import date
from decimal import Decimal
from nautilus_trader.model.enums import OrderSide

from nautilus_india.core.fees import CostModel

costs = CostModel()

# Selling one lot (65 units) of a NIFTY option at a premium of 100.
breakdown = costs.leg_cost(date(2026, 8, 4), OrderSide.SELL, Decimal("100.00"), 65)

print(breakdown.stt)        # 9.750000
print(breakdown.brokerage)  # 20.0
print(breakdown.total)      # 36.08165100000
```

Every table in this package reports how much it trusts itself, which matters
more than the number:

```python
print(costs.rates_are_verified(date(2026, 8, 4)))   # False
print(costs.reconciled_against(date(2026, 8, 4)))
# https://dhan.co/calculators/brokerage-calculator/ on 2026-09-04, ...
```

`False` means these rates reproduce Dhan's own calculator to the paisa across
seven cases — and have never been checked against a real contract note. That
is the honest state, and it is why the flag exists rather than being assumed.

There is more in [Core without a broker](core-without-a-broker.md): lot sizes
with provenance, the market calendar, and the margin model.

---

## Credentials, when you want a broker

Two environment variables:

```bash
cp .env.example .env      # then fill it in; .env is git-ignored
export DHAN_CLIENT_ID=...
export DHAN_ACCESS_TOKEN=...
```

**The access token lives 24 hours from the moment it is minted** — `exp` is
exactly `iat + 86400`, measured against `GET /v2/profile`. Not from a fixed
hour, so a token minted during market hours dies during market hours. Plan for
renewal rather than pasting one in and forgetting.

Leave `client_id` and `access_token` as `None` on the client configs. They then
resolve from the environment at connect time, which keeps the token out of a
serialised config — and a Nautilus config *is* serialisable, so a token in one
is a token in whatever wrote it out.

---

## Ten minutes: a node

```python
from nautilus_trader.config import InstrumentProviderConfig, TradingNodeConfig
from nautilus_trader.live.node import TradingNode

from nautilus_india.dhan import DhanDataClientConfig, DhanLiveDataClientFactory

config = TradingNodeConfig(
    trader_id="EXAMPLE-001",
    data_clients={
        "DHAN": DhanDataClientConfig(
            instrument_provider=InstrumentProviderConfig(
                load_all=True,
                # The master carries ~200,000 contracts. Narrowing it is the
                # difference between a two-second start and holding every
                # Indian contract in the cache.
                filters={"underlyings": {"NIFTY"}},
            ),
        ),
    },
    exec_clients={},
)

node = TradingNode(config=config)
node.add_data_client_factory("DHAN", DhanLiveDataClientFactory)
node.build()
```

That is the out-of-tree registration hook, and it is the whole reason this can
be an MIT package alongside an LGPL engine rather than a fork.

> **`node.run()` will not stream yet.** `DhanDataClient` has no `_connect`, so
> it raises `NotImplementedError` the moment the node starts. The instrument
> master, binary decoder, feed protocol and tick parsers are all built and
> tested; the client that joins them is not. This is
> [milestone 0.2](../ROADMAP.md) and [Market data](market-data.md) says exactly
> what does and does not exist.

## Where to go next

- [Core without a broker](core-without-a-broker.md) — the part that works today
- [Execution](execution.md) — orders, the two switches, and what has never been
  observed
- [Configuration](configuration.md) — every field and environment variable
- [Troubleshooting](troubleshooting.md) — when something fails
