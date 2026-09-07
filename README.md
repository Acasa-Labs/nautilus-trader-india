# nautilus-trader-india

NautilusTrader adapters for **Zerodha Kite** and **Dhan** — NSE, BSE and MCX.

An independent MIT package that installs alongside upstream `nautilus-trader`.
Not a fork.

## Status

| Component | State |
| --- | --- |
| `nautilus_india.core` — symbology, instruments, lots, calendar, fees, margin | **shipped**, 80 tests |
| `nautilus_india.dhan` — data + execution adapter | not started |
| `nautilus_india.kite` — data + execution adapter | not started |

The core is useful on its own: it turns Indian contracts into Nautilus
instruments and prices their transaction costs, whether or not you use either
adapter.

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
# NIFTY260804002455000CE.NSE, INR, 0.05 tick, multiplier 65, expiring 15:30 IST
```

Once the adapters land, they register the ordinary way:

```python
node.add_data_client_factory("DHAN", DhanLiveDataClientFactory)
node.add_exec_client_factory("DHAN", DhanLiveExecClientFactory)
```

## Design notes worth knowing before you use it

**The venue is the exchange, not the broker.** An `InstrumentId` is
`NIFTY260804002455000CE.NSE`, never `...KITE`. A strategy names where the
contract trades, so the same strategy runs on either broker unchanged; the
broker is the `ClientId`.

**Quantity is in lots; the lot lives in `multiplier`.** Nautilus prices a fill
as `qty × multiplier × price`. One lot of NIFTY at a premium of 100 is 65
units — ₹6,500. Passing 65 as the quantity prices 4,225 units at ₹422,500,
exactly 65× too much.

**Costs are dated, and the tables raise rather than default.** An uncovered
date or unknown underlying raises instead of borrowing another period's rates,
because a wrong rate is a silent, uniform mispricing that no downstream
assertion catches.

**Every table says how much it trusts itself.** Lot sizes report whether they
were observed in NSE's bhavcopy archive or collapsed from a regime; charge
rates report what they have been reconciled against and stay `verified: false`
until a real contract note is seen; margin rates carry the worst relative
error of their fit, not the average.

## Live orders are gated behind two switches

Submission will require **both** `live_orders=True` on the config **and**
`NAUTILUS_INDIA_LIVE_ORDERS=1` in the environment. Two switches rather than
one, because a stray import cannot set an environment variable and a stray
environment variable cannot construct a client.

## Known gaps

Two NautilusTrader behaviours are worked around rather than fixed, and both
err in the unsafe direction if you ignore them: portfolio margin is asked once
at open, and settlement bypasses `get_commission` so exercise STT is not
charged. Both are documented with their direction of error and their measured
size in [`docs/UPSTREAM_GAPS.md`](docs/UPSTREAM_GAPS.md). Read it before
holding a position to expiry or legging out of a multi-leg short.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

Contributor and agent rules — including why the vendor SDKs are never imported
at runtime — are in [`CLAUDE.md`](CLAUDE.md).

## Credits

Built at [Acasa Labs](https://github.com/Acasa-Labs) by Pritesh Kanani.

The parts that were expensive to learn — the Dhan binary feed decoder, the
error taxonomy behind Dhan's HTTP-200-on-failure behaviour, the charge model
reconciled against Dhan's own calculator, and the margin calibration — come
from measuring live behaviour rather than from reading documentation. Where a
number could not be verified, the table says so.

## Licence

MIT. NautilusTrader itself is LGPL-3.0; this package links against it and
contains none of its code.
