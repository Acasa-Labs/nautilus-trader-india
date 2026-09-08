# Execution

> **This places real orders with real money.** Every endpoint on
> [Dhan's order page](https://dhanhq.co/docs/v2/orders/) is implemented and has
> been driven end to end against [Dhan's sandbox](https://sandbox.dhan.co/v2/).
> **It has never run against a live account**, and **no order it placed has
> ever filled** — the sandbox has no matching engine, so orders rest at
> `PENDING` for ever whatever the price.
>
> So `generate_fill_reports` and `generate_position_status_reports` have never
> produced a report from real data, and every fixture behind them is Dhan's
> documentation. That is the largest untested surface in this package. Read
> [the README's gap table](../README.md) before trusting any of it.

---

## Registering, which does not arm it

```python
from nautilus_india.dhan import DhanExecClientConfig, DhanLiveExecClientFactory

node.add_exec_client_factory("DHAN", DhanLiveExecClientFactory)

# and in the node config:
exec_clients={"DHAN": DhanExecClientConfig(live_orders=True)}
```

Registering the factory does not arm anything. A config nobody edited reads,
reports and sends nothing.

## The two switches

Submission requires **both**:

| Switch | Where |
| --- | --- |
| `live_orders=True` | on `DhanExecClientConfig` |
| `NAUTILUS_INDIA_LIVE_ORDERS=1` | in the environment |

Two rather than one, because **a stray import cannot set an environment
variable and a stray environment variable cannot construct a client.** Neither
accident alone can put a real order on an exchange.

The environment value is compared exactly. `true`, `yes` and `0` are all
things somebody types meaning something, and a switch that accepts an
approximation of itself is a switch that can be flipped by accident.

With either missing, every order is denied before anything is sent, and the
denial names both switches:

```
live order submission is disabled: `live_orders=True` on DhanExecClientConfig
and NAUTILUS_INDIA_LIVE_ORDERS=1 in the environment is not set. Both are
required and neither may be collapsed into the other -- ...
```

**Run `examples/dhan_execution.py` once without `--live` first.** It will be
denied, and watching the gate work is the cheapest way to trust it before
money is involved.

---

## The rule that matters most: silence when unsure

Dhan answers **HTTP 200 for failures**. An unknown `securityId` returns 200
with empty arrays, byte-identical to a holiday. So the body is the evidence and
the status code is never the answer.

| Evidence | Event emitted |
| --- | --- |
| Proof it was never sent | `OrderDenied` |
| A definitive venue rejection | `OrderRejected` |
| **It may have reached the venue** — timeout, reset, unparseable body | **none** |

The third row is the important one. A submission that times out may be working
at the exchange, so the client emits **nothing** and leaves it to
`generate_order_status_reports`. It logs loudly:

```
order O-... did not complete and may have reached Dhan (...).
NO EVENT EMITTED; it may be working.
```

**It never reports such an order rejected.** That would tell the engine an
order is dead while it is live, and the position that follows is one nobody
chose.

---

## Placing an order

Ordinary Nautilus. The adapter converts on the way out:

```python
order = self.order_factory.limit(
    instrument_id=InstrumentId.from_str("NIFTY260929002455000CE.NSE"),
    order_side=OrderSide.BUY,
    quantity=Quantity.from_int(1),        # ONE LOT -- multiplied by the lot size for Dhan
    price=Price.from_str("100.00"),
    time_in_force=TimeInForce.DAY,
    client_order_id=ClientOrderId("my-order-1"),   # keep it short; see below
)
self.submit_order(order)
```

All four documented order types (`LIMIT`, `MARKET`, `STOP_LOSS`,
`STOP_LOSS_MARKET`), all six product types, both validities, the disclosed
quantity and the after-market window are covered.

### Four behaviours that will surprise you

**A `MARKET` order does not fill at the market.** Dhan converts an API
`MARKET` order into a limit order with *market-protection pricing*, so it
fills at a limit neither you nor this adapter chose. The order is sent as
asked and the adapter logs a warning when it does. **Send a `LIMIT` order to
name your own price.**

**`GTC` is sent as `DAY`.** `/v2/orders` takes `DAY` and `IOC` and nothing
else; NSE rests nothing overnight on this endpoint. To rest past the close,
use a forever order — a different endpoint, and an explicit call.

**Client order ids are hashed, not passed through.** `correlationId` accepts
**25 characters** — measured; the docs say 30 — and a default Nautilus
`ClientOrderId` is 27, so it cannot fit. Anything too long is sent as an
18-character `blake2s` digest, deterministic and recoverable by recomputation.
The id in Dhan's own order book is therefore not human-readable. **Pass a
short id of your own** to keep it readable.

**Commission is reported as zero.** Dhan does not send one: `GET /v2/trades`
carries no charge and the margin calculator returns `brokerage: 0.0`.
`core.fees` models the charge, but putting that estimate into a broker record
would launder our own number as the venue's. Use
[`CostModel`](core-without-a-broker.md) for what a fill cost.

### Two options that change the order's shape

Both are off by default, because each turns your order into something
structurally different:

| Option | What it changes |
| --- | --- |
| `slice_over_freeze_limit=True` | Routes to `POST /v2/orders/slicing`, splitting a quantity over the F&O freeze limit into **several orders**, each with its own id and its own fills |
| `after_market_order=True` | Sends the order for release at `amo_time` (`PRE_OPEN`, `OPEN`, `OPEN_30`, `OPEN_60`) rather than now |

---

## Cancelling and modifying

`cancel_order`, `modify_order` and `cancel_all_orders` work the ordinary way.

A cancel needs a `VenueOrderId`: Dhan addresses a cancel by its own order id
and offers no other handle, and guessing one cancels somebody else's order. A
cancel with no venue id is rejected rather than attempted.

`_batch_cancel_orders` is **not** implemented and falls through to the base
class. It is on [the roadmap](../ROADMAP.md).

---

## Brackets go out as one super order

Submit a Nautilus order list shaped entry + target + stop and it becomes a
single `POST /v2/super/orders`.

Sent as three separate orders there would be no OCO between them, so a filled
target leaves the stop working and the next move opens a position nobody
chose.

One `orderId` covers all three legs, so reports give each leg the composite id
`{orderId}:{legName}` — keyed on `orderId` alone, two of the three would
collide and vanish.

```python
await client.cancel_super_order_leg(order_id, "TARGET_LEG")
await client.modify_super_order_leg(order_id, "STOP_LOSS_LEG", **terms)
reports = await client.generate_super_order_reports()
```

> **Cancelling a target or stop leg on its own cannot be undone.** Dhan will
> not let the same leg be added again. The client logs a warning before it
> does it. Cancel `ENTRY_LEG` to cancel all three.

A super order takes `LIMIT` or `MARKET` only — no stop entry — and four of the
six product types. `CO` and `BO` are absent because a super order *is* the
bracket.

---

## Forever orders rest past the close

```python
await client.submit_forever_order(command, product_type="CNC")
await client.submit_forever_order(command, product_type="CNC", second_leg=other)  # OCO
await client.cancel_forever_order(order_id)
reports = await client.generate_forever_order_reports()
```

This is a separate call rather than a route for `GTC`, on purpose. Nautilus
has no Good-Till-Triggered concept, and quietly turning "rest at the exchange"
into "rest at the broker behind a trigger" would be a different order from the
one asked for. A caller who wants one asks for it.

**A forever order requires a trigger price** — that is what the *triggered* in
Good-Till-Triggered means. Only the delivery product types apply (`CNC`,
`MTF`): `INTRADAY` cannot outlive the day it is named after.

A cancel takes the whole order, not a leg.

> Dhan's forever-order page contradicts itself on the list path: the endpoint
> table says `GET /forever/orders`, the cURL sample says `GET /v2/forever/all`.
> Probed 2026-09-08 — the first works, the second answers **404**.

---

## Reports

All five generators are implemented:

```python
await client.generate_order_status_report(...)    # one order
await client.generate_order_status_reports(...)   # the order book
await client.generate_fill_reports(...)           # GET /v2/trades
await client.generate_position_status_reports(...)
await client.generate_mass_status(...)
```

**A failed read stays failed.** A report generator that cannot read returns
nothing rather than an empty list presented as "no orders" — an empty list is
a claim about the account, and a failed read is not evidence for it.

> `generate_fill_reports` and `generate_position_status_reports` **have never
> produced a report from real data.** No order this codebase placed has ever
> filled. Every fixture behind them is Dhan's published documentation.

---

## The order-update stream

`order_updates=True` subscribes to `wss://api-order-update.dhan.co`, so a fill
is reported when it happens rather than at the next reconciliation.

**It is off by default**, and honestly so: the socket refuses sandbox
credentials, so **not one real frame has ever been parsed** by this code.

When the stream drops, the client says so loudly and marks itself
disconnected:

```
the Dhan order-update stream is DOWN (...). Fills and status changes will NOT
arrive until it is back; they are still visible to reconciliation, which is
now the only thing that will see them.
```

A stream that dies quietly leaves an engine that looks healthy and learns
nothing. Check `client.order_updates_connected` if you depend on it.

The socket speaks a **different dialect from REST** — `TxnType` `B`/`S` rather
than `transactionType` `BUY`/`SELL`, `OrderType` `LMT` rather than `LIMIT`,
`Status` `"Pending"` *or* `"PENDING"`. It carries no trade id, and it carries
the whole account rather than one order. All of that is mapped, and all of it
is written up in [Dhan API notes](DHAN_API_NOTES.md).

With it off, fills arrive by polling `GET /v2/trades` at the next
reconciliation.

---

## Degrading on a whitelisting failure

Placing an order on Dhan requires a **whitelisted static IP**. When Dhan
refuses one with `Invalid IP`, the client sets `is_degraded_by_ip` and refuses
every subsequent order with a named reason, rather than sending each one to
fail the same way:

```
this client is degraded: Dhan refused an earlier order with 'Invalid IP', and
every order from this address fails the same way until a static IP is
whitelisted.
```

See [Troubleshooting](troubleshooting.md).
