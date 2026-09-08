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
> | **The order path has never run against production** | The client has been driven end to end against Dhan's [sandbox](https://sandbox.dhan.co/v2/) — placing, modifying, cancelling, looking an order up by client order id, and resting a forever order — but never on a live account, which needs a whitelisted static IP the machine this was written on does not have. The sandbox is not a faithful mirror (`GET /v2/holdings` answers `200 []` there and `500` in production), so it raises confidence without settling it. |
> | **No order has ever filled** | The sandbox has no matching engine: orders rest at `PENDING` for ever, whatever the price. So `generate_fill_reports` and `generate_position_status_reports` have never produced a report from real data, and every fixture behind them is Dhan's documentation. This is the largest untested surface in the package. See [`docs/DHAN_API_NOTES.md`](docs/DHAN_API_NOTES.md). |
> | **Client order ids are hashed, not passed through** | Dhan's `correlationId` accepts 25 characters — measured; the docs say 30 — and a default Nautilus `ClientOrderId` is 27, so it cannot fit. Anything too long is sent as an 18-character `blake2s` digest, which is deterministic and maps back by recomputation. The id in Dhan's own order book is therefore **not human-readable**. |
> | A `MARKET` order does not fill at the market | Dhan converts an API `MARKET` order into a limit order with **market-protection pricing**, so it fills at a limit neither you nor this adapter chose. The order is sent as asked and the adapter logs a warning when it does. Send a `LIMIT` order to name your own price. |
> | `GTC` is sent as `DAY` | NSE rests nothing overnight on this endpoint — every order dies at the close whatever is asked for. Dhan's GTT equivalent is `/v2/forever/orders`, which this adapter does not use. |
> | Fills arrive by polling, not by socket | `generate_fill_reports` reads `GET /v2/trades`. Dhan's order-update WebSocket is not wired up yet, so a fill is seen at the next reconciliation rather than the instant it happens. |
> | Commission is reported as zero | Dhan does not send one: `GET /v2/trades` carries no charge and the margin calculator returns `brokerage: 0.0`. `core.fees` models the charge; putting that estimate into a broker record would launder our own number as the venue's. |
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
| `nautilus_india.dhan` — instruments + market data | **shipped**, 183 tests |
| `nautilus_india.dhan` — execution | **shipped**, 177 tests. Orders, super orders and forever orders. Exercised against Dhan's **sandbox**; **never run against a live account.** |
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

Execution registers the same way, and registering it does **not** arm it:

```python
from nautilus_india.dhan import DhanExecClientConfig, DhanLiveExecClientFactory

node.add_exec_client_factory("DHAN", DhanLiveExecClientFactory)
# and in the node config:
#     exec_clients={"DHAN": DhanExecClientConfig(live_orders=True)}
# and in the environment:
#     NAUTILUS_INDIA_LIVE_ORDERS=1
```

**Both switches, always.** With either one missing every order is denied
before anything is sent — which is the point: a stray import cannot set an
environment variable, and a stray environment variable cannot construct a
client. A config nobody edited reads, reports and sends nothing.

**An order whose fate is unknown produces no event.** A submission that
times out, is reset, or comes back unreadable may be working at the
exchange, so the adapter says nothing and leaves it to
`generate_order_status_reports`. It never reports such an order rejected:
that would tell the engine an order is dead while it is live, and the
position that follows is one nobody chose.

All nine endpoints on [Dhan's order page](https://dhanhq.co/docs/v2/orders/)
are covered — place, modify, cancel, slice, the order book, an order by
Dhan's id or by your own `correlationId`, the trade book, and the trades of
one order — with all four documented order types (`LIMIT`, `MARKET`,
`STOP_LOSS`, `STOP_LOSS_MARKET`), all six product types, both validities,
the disclosed quantity and the after-market window.

Two of those are opt-in on the config, because each turns your order into
something structurally different:

| Option | What it changes |
| --- | --- |
| `slice_over_freeze_limit=True` | Routes to `POST /v2/orders/slicing`, which splits a quantity over the F&O freeze limit into **several orders**, each with its own id and its own fills. |
| `after_market_order=True` | Sends the order for release at `amo_time` (`PRE_OPEN`, `OPEN`, `OPEN_30`, `OPEN_60`) rather than now. |

### Super orders and forever orders

Both are covered in full, and each holds a relationship the ordinary order
path cannot.

**A bracket goes out as one super order.** Submit a Nautilus order list whose
shape is entry + target + stop and it becomes a single `POST /v2/super/orders`.
Sent as three separate orders there would be no OCO between them, so a filled
target leaves the stop working and the next move opens a position nobody chose.

One `orderId` covers all three legs, so reports give each leg the composite id
`{orderId}:{legName}` — keyed on `orderId` alone, two of the three would
collide and vanish. `cancel_super_order_leg(order_id, leg)` takes the pair
Dhan takes. **Cancelling a target or stop leg on its own cannot be undone** —
Dhan will not let the same leg be added again — so the client logs a warning
before it does; cancel `ENTRY_LEG` to cancel all three.

**Forever orders rest past the close**, which `/v2/orders` cannot do at all.
`submit_forever_order(command, product_type="CNC")` sends one; pass
`second_leg=` for an OCO pair where either firing cancels the other. It is a
separate call rather than a route for `GTC` on purpose: Nautilus has no
Good-Till-Triggered concept, and quietly turning "rest at the exchange" into
"rest at the broker behind a trigger" would be a different order from the one
asked for. A forever order **requires** a trigger price — that is what the
*triggered* in Good-Till-Triggered means.

> Dhan's forever-order page contradicts itself on the list path: the endpoint
> table says `GET /forever/orders`, the cURL sample says `GET /v2/forever/all`.
> Probed 2026-09-08 — the first works, the second answers **404**. This adapter
> uses the first, and the 404 body is captured in the corpus because it is a
> fifth error shape and not Dhan's own.

The behaviours worth knowing before you trust any of this — including the
three places Dhan's documentation is wrong in ways that silently break an
order — are written up in [`docs/DHAN_API_NOTES.md`](docs/DHAN_API_NOTES.md),
each with how and when it was measured.

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
