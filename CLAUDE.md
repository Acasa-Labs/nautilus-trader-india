# nautilus-trader-india

NautilusTrader adapters for Zerodha Kite and Dhan. MIT. Not a fork.

**ALPHA.** This code is intended to place real orders with real money and has
never been run against a live account. Nothing here may be softened on the
grounds that it is "only alpha" — alpha is precisely when the guards below
matter, because there is no production history to catch what they miss.

## Rules

These are expensive to rediscover. Do not weaken one without a measurement
that says the reason it exists no longer holds.

### Live orders need two switches

Submission requires **both** `live_orders=True` on the config **and**
`NAUTILUS_INDIA_LIVE_ORDERS=1` in the environment. Two rather than one,
because a stray import cannot set an environment variable and a stray
environment variable cannot construct a client. Never collapse them.

### Decimal never float

`Price.from_str`, `Quantity.from_str`. Never `Price(float(...))` — and note
that `Price(value, precision)` takes a **C double**, so passing a `Decimal`
positionally still routes through binary floating point. `from_str` is the
only exact path.

Broker payloads arrive as strings or integers; they stay exact until they
become Nautilus objects. A strike is a `Decimal`, never a `float` —
`24550.05` does not survive a round trip through binary floating point.

### Never import a vendor SDK client at runtime

`kiteconnect` and `dhanhq` are dev dependencies for cross-checking payload
shapes. Three measured reasons, so nobody reverts this as arbitrary:

1. `dhanhq` 2.2.0's `marketfeed.process_data` unpacks `data[0:1]`,
   dispatches, and returns a single `process_*` call. There is no offset
   loop over the header's `message_length`, so **every packet after the
   first in a frame is silently discarded**.
2. `dhanhq` renders every price as `"{:.2f}".format(...)` — a string — and
   `LTT` as `datetime.utcfromtimestamp(e).strftime('%H:%M:%S')`, losing
   date, timezone and sub-second. Neither can timestamp a Nautilus tick.
3. `kiteconnect` 5.2.1's ticker is Twisted/autobahn. Nautilus is asyncio.
   Embedding it means a second event loop on another thread.

`tests/test_package.py` asserts `nautilus_india.core` imports none of them.

### An ambiguous order request emits nothing

Dhan answers **HTTP 200 for failures** — an unknown `securityId` returns 200
with empty arrays, byte-identical to a holiday. So the body is the evidence
and the status code is never the answer.

| Evidence | Event |
| --- | --- |
| Proof it was never sent | `OrderDenied` |
| Definitive venue rejection | `OrderRejected` |
| **May have reached the venue** (timeout, reset, unparseable body) | **none** — reconcile |

Never add an `OrderRejected` on a timeout. It reports a working order as
dead, and the position that follows is one nobody chose.

### Fixtures are captured, never fabricated

Every fixture in `tests/fixtures/` is a real venue response with credentials
scrubbed. A hand-written "valid" payload encodes what we believe rather than
what the venue sends, which is precisely the bug fixtures exist to catch.

For the same reason, a test that reconciles against an external calculator
must be filled in from that calculator. Populating it from our own output
asserts only that the model agrees with itself.

### Tests before implementation

Each phase lands as failing tests first. A phase is done when its tests
assert the behaviour in its "done when" column and pass — not when the code
exists.

### Tables raise rather than default

An uncovered date, an unknown underlying, a missing lot size: raise. Falling
back to another period's rates is a silent, uniform mispricing that no
downstream test catches.

### Quantity is in lots; the lot lives in `multiplier`

Nautilus prices a fill as `qty * multiplier * price`. Passing the lot size as
both squares the position — a NIFTY 24550 CE round trip read as ₹78,585
against a true ₹1,209, because 65 lots of a 65-multiplier contract is 4,225
units. `lot_size` is 1: one contract is one lot.

## Never commit

Credentials, tokens, account numbers, real order records, or anything copied
from a private repository.

## References

- Dhan API v2 — https://dhanhq.co/docs/v2/
- Kite Connect v3 — https://kite.trade/docs/connect/v3/
- NautilusTrader Python adapter base classes —
  `nautilus_trader/adapters/_template/`, `live/factories.py`, `live/node.py`
- Known core gaps — `docs/UPSTREAM_GAPS.md`
