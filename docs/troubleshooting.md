# Troubleshooting

Symptom first. Where the reasoning lives elsewhere, this page links rather
than restates it — [Dhan API notes](DHAN_API_NOTES.md) is the file with the
measurements and the dates.

---

## Install and import

**`NotImplementedError: implement the '_connect' coroutine`**
The market data client is not wired. This is expected, not a broken install —
`DhanDataClient` has no `_connect` and no subscription methods. See
[Market data](market-data.md) and [milestone 0.2](../ROADMAP.md).

**`UnknownLotSizeError` / `UnknownCostRatesError` on an installed copy, when
the repo worked**
The YAML data tables did not make it into the wheel. CI has a job that builds
the wheel and asserts all three are inside it, precisely because a unit test
cannot catch this: the source tree has the files whether or not the wheel
does. Reinstall; if it persists, open a bug with your wheel filename.

**`ModuleNotFoundError: kiteconnect` / `dhanhq`**
Those are dev dependencies for cross-checking payload shapes and are never
imported at runtime. If something in `nautilus_india` is importing one, that
is a bug — `tests/test_package.py` asserts it does not happen.

---

## Credentials

**`MissingCredentialsError`**
It names which of `DHAN_CLIENT_ID` / `DHAN_ACCESS_TOKEN` is absent. Both are
resolved as one set at connect time.

**Everything worked yesterday and now nothing authenticates**
The access token expired. It lives **24 hours from minting**, not from a fixed
hour, so a token minted during market hours dies during market hours. Check
before blaming the network:

```python
from nautilus_india.dhan.auth import token_expiry, token_is_live
token_expiry(token)     # UTC datetime
token_is_live(token)    # False once under 30 minutes remain
```

**`MalformedTokenError`**
The token is not a readable JWT — usually a truncated copy-paste, or a shell
that ate part of it. Only the payload is read; the signature is never
verified here.

**`Invalid Token` from Dhan**
The session is dead at Dhan's end, whatever the local expiry says. Mint a new
one.

---

## Orders

**Every order comes back `OrderDenied` and nothing reaches Dhan**
One of the two switches is missing, and the denial reason names both. You need
`live_orders=True` on the config **and** `NAUTILUS_INDIA_LIVE_ORDERS=1` in the
environment, the second compared exactly — `true`, `yes` and `0` do nothing.

This is the gate working. Run `examples/dhan_execution.py` without `--live` to
watch it deny on purpose.

**`Invalid IP`, then every later order refused locally**
Placing an order on Dhan requires a **whitelisted static IP**. After one such
refusal the client sets `is_degraded_by_ip` and stops sending, rather than
firing each order at the same wall:

```
this client is degraded: Dhan refused an earlier order with 'Invalid IP' ...
```

Whitelist a static IP in Dhan's dashboard. To exercise the write path without
one, point `base_url` at `https://sandbox.dhan.co` — it needs no whitelisting,
and it is [not production](configuration.md#pointing-at-the-sandbox).

**An order vanished: no accepted, no rejected, no event at all**
Working as designed. A submission that timed out, was reset, or returned an
unreadable body **may be working at the exchange**, so the client emits
nothing and logs `NO EVENT EMITTED; it may be working`. Call
`generate_order_status_reports` to find out what actually happened. It will
never emit `OrderRejected` on a timeout — that reports a live order as dead.

**`DH-905` or `DH-906`, and the message does not match the problem**
Neither code is diagnostic. `DH-905` is the generic input exception and has
come back for `Invalid IP`, for `quantity is required`, and for a 90-day span
limit. **Classify on the message, not the code.** `DH-906`'s messages map:

| Message | Means |
| --- | --- |
| `Invalid Token` | the session is dead |
| `Incorrect request for order and cannot be processed` | no such order |
| `Order is in Transit state` | too early to cancel |

**`quantity is required` when quantity is obviously set**
Dhan validates in order — quantity, then the IP, then the instrument — so an
incomplete payload fails on the first check and never reaches the one that
would have told you the real problem. Check the whole payload, not the field
named.

**An order id in Dhan's order book that is an unreadable hash**
Expected. `correlationId` accepts 25 characters — measured; the docs say 30 —
and a default Nautilus `ClientOrderId` is 27, so it is sent as an 18-character
`blake2s` digest. Pass a short `client_order_id` of your own to keep it
readable.

**A `MARKET` order filled at a price nobody chose**
Dhan converts an API `MARKET` order into a limit order with market-protection
pricing. Send a `LIMIT` order to name your own price. The adapter logs a
warning when it forwards a market order.

**A `GTC` order died at the close**
It was sent as `DAY`; `/v2/orders` accepts `DAY` and `IOC` only. Use
`submit_forever_order` to rest past the close.

**Cancelling one leg of a bracket cannot be undone**
Dhan will not let the same leg be added again. Cancel `ENTRY_LEG` to cancel
all three. The client warns before doing it.

**Fills are late**
They arrive at reconciliation, by polling `GET /v2/trades`. Set
`order_updates=True` for the socket — but note it has **never delivered a real
frame** to this code, and it is off by default for that reason.

**`client.order_updates_connected` is `False`**
The stream dropped and said so loudly. Fills and status changes are still
visible to reconciliation, which is now the only thing that will see them.

**Commission on every fill is zero**
Dhan does not send one. Use [`CostModel`](core-without-a-broker.md) to price
the fill. Putting our own estimate into a broker record would launder it as
the venue's.

---

## Data that looks wrong

**An empty response that should have data**
Dhan answers **HTTP 200 with empty arrays** for an unknown `securityId` —
byte-identical to a holiday. The status code is never the answer. Check the
security id resolved to what you expected via `provider.security_id_for`.

**A date of `0001-01-01`**
A sentinel, not a date. It parses to a negative timestamp that Nautilus
accepts in silence, which is why it is handled explicitly.

**`GET /v2/holdings` returns 500**
On a live account that is what an **empty portfolio** looks like. The sandbox
answers `200 []` for the same state — one of the measured ways the two differ.

**An NSE commodity option is in the instrument master but will not subscribe**
Dhan publishes no market-feed segment code for that segment, so its 23,870
`OPTFUT` contracts are listed and unsubscribable. Dhan's own SDK cannot
address them either. Guessing a segment would name a *different* instrument
and return 200 with empty data, which would look like a quiet market rather
than an error.

**A 429 with no `Retry-After`**
Dhan sends none. Back off on your own schedule.

**A field spelled wrong**
Some of them really are. `availabelBalance` on `GET /v2/fundlimit` is Dhan's
actual spelling — reading the correct one returns `None`, which renders as a
broke account. `orderType` on a forever order carries `SINGLE`/`OCO` rather
than the type it was sent as. The full list is in
[Dhan API notes](DHAN_API_NOTES.md).

---

## Costs and margin

**A backtest held to expiry looks too profitable**
Exercise STT is not charged. Nautilus settles through its own path rather than
`FeeModel.get_commission`, so the fee model cannot see it. On a small ITM
winner the real charge can exceed the entire profit. Call
`CostModel.exercise_cost` yourself. See [Upstream gaps](UPSTREAM_GAPS.md).

**A multi-leg short blocks far more capital than the exchange asks**
Margin is per-instrument, so a short straddle is over-charged ~1.66×. Safe for
sizing, wrong for research. There is deliberately no portfolio-aware path
until Nautilus re-evaluates margin on position *change* — it would trade a
safe over-charge for an unsafe under-charge.

**Charges do not match my contract note**
Quite possibly. The rates carry `verified: false`: they reproduce Dhan's
*calculator* to the paisa over seven cases, and no real contract note has ever
been checked. **If you have one, that is the single most valuable thing you
can contribute** — see [CONTRIBUTING.md](../CONTRIBUTING.md).

**A position is priced 65× too large**
The lot was passed as the quantity as well as living in `multiplier`. Quantity
is in **lots**; one contract is one lot and `lot_size` is `1`.

**A cost or lot lookup raised instead of returning a number**
By design. Tables raise rather than defaulting, because falling back to
another period's rates is a silent, uniform mispricing that no downstream
assertion catches. Add the period with its source.

---

## Still stuck

- [The README's gap table](../README.md) lists what is known to be wrong or
  unverified, in the direction that costs money.
- [ROADMAP.md](../ROADMAP.md) lists what is not built yet.
- If Dhan does something not written down here, file a **Venue behaviour**
  issue with the endpoint, the response verbatim, and the date. That is the
  most useful issue this repository receives.
