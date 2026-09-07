# nautilus-trader-india

NautilusTrader adapters for **Zerodha Kite** and **Dhan** — NSE, BSE and MCX.

An independent MIT package that installs alongside upstream `nautilus-trader`.
Not a fork.

---

> ## ⚠️ ALPHA — trade carefully
>
> This is alpha software that is intended to place real orders with real money.
> It has never been run against a live account. Treat every number it produces
> as unverified until you have checked it yourself.
>
> Specifically, and in the direction that costs you money:
>
> | Known gap | Effect |
> | --- | --- |
> | Charge rates are `verified: false` | They reproduce Dhan's *calculator* to the paisa, but no real **contract note** has been checked. Your actual charges may differ. |
> | Exercise STT is not charged at all | A position **held to expiry is under-costed**. On a small ITM winner the real charge can exceed the entire profit. See [`docs/UPSTREAM_GAPS.md`](docs/UPSTREAM_GAPS.md). |
> | Margin is per-instrument, not per-portfolio | A multi-leg short is **over**-charged ~1.66×. Safe for sizing, wrong for research — it can veto trades the exchange would have allowed. |
> | Historical rates before 2024-10-01 | Estimated, not measured. A backtest over that period may mis-charge STT. |
> | Live execution is untestable in CI | No socket is ever opened in the test suite. The order path has unit tests and **no integration coverage**. |
>
> No execution client exists yet, for either broker — this package cannot
> place an order at all today. When one lands, live order submission will be
> gated behind two switches (see below), and that gate is not a formality.
>
> **The API will change without deprecation before 1.0.** Pin an exact version.
>
> MIT means this comes with no warranty. You are responsible for every order
> your system sends.

---

## Status

| Component | State |
| --- | --- |
| `nautilus_india.core` — symbology, instruments, lots, calendar, fees, margin | **shipped**, 80 tests |
| `nautilus_india.dhan` — instruments + market data | **shipped**, 124 tests |
| `nautilus_india.dhan` — execution | not started |
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

Dhan market data registers the ordinary way:

```python
from nautilus_india.dhan import DhanDataClientConfig, DhanLiveDataClientFactory

node.add_data_client_factory("DHAN", DhanLiveDataClientFactory)
```

See [`examples/dhan_market_data.py`](examples/dhan_market_data.py) for a
runnable node.

**Known gap:** Dhan publishes no market-feed segment code for NSE commodity,
so its 23,870 `OPTFUT` contracts are listed but not subscribable. Dhan's own
SDK cannot address them either — it enumerates segments 0–5, 7 and 8, leaving
6 unassigned. Guessing would be worse than refusing: a wrong segment names a
different instrument, and Dhan answers HTTP 200 with empty data for one that
does not exist, so it would look like a quiet market rather than an error.

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
