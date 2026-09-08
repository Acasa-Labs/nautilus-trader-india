# Dhan API — measured behaviour

Where this file disagrees with <https://dhanhq.co/docs/v2/>, the measurement
won. Every claim below names how it was established and when.

Three sources, in descending order of authority:

| Source | What it proves |
| --- | --- |
| **live** — a call to `api.dhan.co` on the real account | what production does |
| **sandbox** — a call to `sandbox.dhan.co` | what the sandbox does, which is *usually* what production does — see [The sandbox is not production](#the-sandbox-is-not-production) |
| **documented** — the published spec | what Dhan intends, which the first two have contradicted |

The bodies behind these claims are in `tests/dhan/fixtures/envelope/`, one
directory per source, and the test suite asserts every one declares which.

---

## The order path

### `""` is refused where the docs send it

**Sandbox, 2026-09-08.** Dhan's documented request body for `POST /v2/orders`
sends an empty string for each field that does not apply:

```json
{"...": "...", "disclosedQuantity": "", "price": "", "triggerPrice": "",
 "amoTime": "", "boProfitValue": "", "boStopLossValue": ""}
```

Sent exactly as documented, the order is **refused**:

```
400 {"errorType":"Input_Exception","errorCode":"DH-905",
     "errorMessage":"Missing required fields, bad values for parameters etc."}
```

The identical payload with those keys **omitted** is accepted. Both were sent
in the same minute and the empty strings are the only difference. The error
message names no field, so nothing about the response points at the cause.

**Rule: omit a field that does not apply. Never send it empty.**
`orders.place_payload` does this, and this repository shipped the documented
form first — every order it built would have been refused.

### `correlationId` caps at 25 characters, not 30

**Sandbox, 2026-09-08, binary-searched.** 25 characters is accepted, 26 is
refused, with the same uninformative DH-905. The docs say 30.

This is not a cosmetic five. A **default Nautilus `ClientOrderId`** is 27
characters (`O-19700101-000000-001-000-1`), so it does not fit, and the
format's fixed parts alone exceed the limit — no configuration produces a
shorter one. A UUID one is 36 and is further out still.

The charset is wider than the length: alphanumerics, spaces, hyphens and
underscores were all accepted. The docs' own note, `[^a-zA-Z0-9 _-]`, has a
leading caret that negates the class, so it cannot be read literally.

**Consequence for this adapter:** `orders.correlation_id` sends an id that
fits as itself and hashes anything longer to 18 hex characters
(`blake2s`, 9 bytes). Deterministic, so no map has to be stored and a restart
between a submission and its answer does not lose the thread —
`GET /v2/orders/external/{id}` still finds the order.
`orders.client_order_id_for` maps back by recomputation.

### A `200` with an `orderId` does not mean the order is working

**Sandbox, 2026-09-08.** A placement answers `{"orderId": "...",
"orderStatus": "TRANSIT"}` — not the `PENDING` the docs sample shows. TRANSIT
means on its way to the exchange. Every order placed during this session was
subsequently **`REJECTED`** by the exchange, with the reason arriving only in
the order book:

```json
"omsErrorDescription": "RMS:<order-id>:Order Price needs to be Circuit Limits of 2915.70 to 3563.50"
```

So the acknowledgement is Dhan accepting the *request*, not the exchange
accepting the *order*. Anything treating a 200 as "working at the exchange"
is one status poll behind the truth.

### Omitting `correlationId` does not mean there is none

**Sandbox, 2026-09-08.** Dhan generates one: `<client-id>-1788847935148`. It is
24 characters of entirely legal charset, so it is indistinguishable by shape
from an id we could have sent. `client_order_id_for` therefore matches only
against ids this session knows about, and answers `None` otherwise — guessing
would attach a stranger's fill to our position.

### `omsErrorDescription` is not always an error

**Sandbox, 2026-09-08.** On a healthy resting order the field reads
`"CONFIRMED"`. Reported as a Nautilus `cancel_reason` — which is what this
adapter did — a working order says it was cancelled because it was confirmed.
The field is only meaningful once the order is `REJECTED`, `CANCELLED` or
`EXPIRED`.

### `TRANSIT` is a state with its own rules

**Sandbox, 2026-09-08.** Between placement and `PENDING` an order sits in
`TRANSIT`, and it is not simply "nearly accepted":

- **A cancel is refused** — `400 DH-906 "Order is in Transit state"`.
- **A modify is accepted** — `200`, same as any other. The asymmetry is not
  documented.
- **`GET /v2/orders/external/{correlationId}` cannot find it** — see below.

### `DH-906` is not diagnostic either

Like `DH-905`. Observed for three unrelated things:

| Message | Means |
| --- | --- |
| `Invalid Token` | the session is dead |
| `Incorrect request for order and cannot be processed` | no such order |
| `Order is in Transit state` | too early to cancel |

The first and second need opposite handling, so this adapter matches on the
**message** when it has to tell them apart — reading the code alone would let
an expired token be reported as an empty order book.

### `GET /v2/orders/external/{id}` answers "not found" differently

**Sandbox, 2026-09-08.** For an id it has no order for — including one placed
seconds earlier and still in `TRANSIT` — it answers
`404 DH-906 "Incorrect request for order and cannot be processed"`.

`GET /v2/orders/{id}` answers the same question with `200 []`. So the two
lookups phrase "no such order" incompatibly, and a reconciliation asking by
client order id would raise where asking by venue id returns nothing. This
adapter maps both to `None`.

### `0001-01-01` is a sentinel, not a date

**Sandbox, 2026-09-08.** An order that never reached the exchange comes back
with `exchangeTime: "0001-01-01 00:00:00"`, `drvExpiryDate: "0001-01-01"`,
and `drvOptionType: "NA"` / `legName: "NA"` where the docs show `null`.

Parsed literally the first is **-62135618008000000000 nanoseconds**, and
Nautilus **accepts a negative timestamp without complaint** — so a report
carries a date in year 1 and nothing anywhere notices. `orders.ist_to_ns`
treats the sentinel as unset.

---

## The response envelope

There is no single envelope. **Live, 2026-09-08.**

| Shape | Seen on | HTTP |
| --- | --- | ---: |
| bare object | `/v2/fundlimit`, `/v2/profile`, `/v2/charts/*`, `POST /v2/orders` | 200 |
| bare array | `/v2/orders`, `/v2/positions`, `/v2/trades`, `/v2/super/orders`, `/v2/forever/orders` | 200 |
| `{status: "success", data: ...}` | `/v2/optionchain/*`, `/v2/marketfeed/*` | 200 |
| `{errorType, errorCode, errorMessage}` | `/v2/orders`, `/v2/charts/*`, `/v2/holdings` | 400, 429, 500 |
| `{status: "failed", data: {code: message}}` | `/v2/optionchain/*` | 400 |
| `[{message, status: "ERROR"}]` | `/v2/ip/getIP` | **200** |
| `{timestamp, status: 404, error, path}` | any unknown path (the gateway, not Dhan) | 404 |

Four things that look like discriminators and are not:

1. **The container.** A bare array is usually success — but `/v2/ip/getIP`
   returns an error inside one.
2. **The status code**, in either direction: an error at 200 above, readable
   bodies at 400, 429 and 500.
3. **The presence of an error field.** `{"status":"failed","data":{"813":
   "Invalid SecurityId"}}` carries none.
4. **The error code.** See below.

What is left: an error is a body that *says* it failed — by an error field, by
a `status` that is not `success`, or by either inside an array element — and
the message is wherever that body puts it. `errors.classify` implements
exactly that.

### `DH-905` is not diagnostic

Returned for at least four unrelated causes:

| Message | Endpoint | Source |
| --- | --- | --- |
| `Data for Intraday Charts can be fetched for 90 days at a time` | `/v2/charts/intraday` | live |
| `Missing required fields, bad values for parameters etc.` | `POST /v2/orders` | sandbox |
| `Invalid IP` | `POST /v2/orders` | prior probe |
| `quantity is required` | `POST /v2/orders` | prior probe |

`DH-905` is the generic `Input_Exception`. **Classify on the message.** The
first and second of those need opposite handling — one is a caller bug, one
stops the session — and the code cannot tell them apart.

Also observed: `DH-904` = `Rate_Limit`, `DH-906` = invalid token,
`DH-1111` = `HOLDING_ERROR`.

### Empty is a legitimate answer, and looks like an error

- An unknown `securityId` on `/v2/charts/intraday` returns **200 with every
  array empty**, byte-identical to a market holiday.
- `POST /v2/marketfeed/ltp` for an unknown id returns `{"data":{"IDX_I":{}},
  "status":"success"}`.
- `GET /v2/orders/{unknown-id}` returns **200 `[]`**, not 404 — so "no such
  order" and "nothing to report" are the same answer.

The last one matters most: after a submission that times out, the status query
is the only way to learn whether the order reached the exchange, and an empty
answer cannot settle it. This is why an ambiguous write emits **no event** and
goes to reconciliation.

### `GET /v2/holdings` returns 500 for an empty portfolio

**Live, 2026-09-08.** `{"errorType":"HOLDING_ERROR","errorCode":"DH-1111",
"errorMessage":"No holdings available"}`. Retry-on-5xx will retry this
forever. Note the sandbox does **not** reproduce it — see below.

### The 429 carries no `Retry-After`

**Live, 2026-09-08**, tripped with eight concurrent chart reads.

```json
{"errorType":"Rate_Limit","errorCode":"DH-904",
 "errorMessage":"Too many requests on server from single user breaching rate limits. Try throttling API calls."}
```

---

## Field names that are misspelled or lie

| Field | Where | Status |
| --- | --- | --- |
| `availabelBalance` | `GET /v2/fundlimit` | **Real.** Live-captured. Reading the correct spelling returns `None`, which renders as a broke account. |
| `totalQuatity` | `GET /v2/super/orders` → `legDetails[]` | **Unverified.** Docs only. Neither account has placed a super order, and `dhanhq` never names the field. Given `availabelBalance`, neither spelling can be assumed — `super_orders.LEG_QUANTITY_KEYS` reads both plus `remainingQuantity`. |
| `orderType` | `GET /v2/forever/orders` | **Lies.** Carries `SINGLE`/`OCO` — the order *flag* — not the `LIMIT`/`MARKET` it was sent as. |
| `legName` | forever vs super orders | **Means different things.** On a super order: `ENTRY_LEG`/`TARGET_LEG`/`STOP_LOSS_LEG`. On a forever order: `TARGET_LEG` is a SINGLE and an OCO's first leg, `STOP_LOSS_LEG` its second, and there is no `ENTRY_LEG`. |

## Paths the docs get wrong

- **`GET /v2/forever/all` does not exist** — 404. The forever-order page's
  endpoint table says `/forever/orders`, its cURL sample says
  `/v2/forever/all`. The table is right, and `dhanhq`'s `get_forever()`
  agrees. **Live, 2026-09-08.**
- `GET /v2/orders/{order-id}` is documented as returning an object; against an
  unknown id it returns an array. Both are read.

---

## The sandbox is not production

`https://sandbox.dhan.co/v2` — same paths, different host, its own token and
client id, a simulated exchange, and no IP whitelisting. It is the only way to
exercise the write path from a dynamic residential address, and it found three
real bugs in this adapter in under an hour.

**But it diverges from production, so a shape seen only there is not proof.**
Measured differences, 2026-09-08:

| | Live | Sandbox |
| --- | --- | --- |
| `GET /v2/holdings`, no holdings | **500** `DH-1111` | **200 `[]`** |
| `GET /v2/super/orders` | 200 `[]` | **404 — not implemented** |
| `/v2/profile` `activeSegment` | `"E, D, C, M, "` | `"Equity, Derivative, Currency, Commodity"` |
| `/v2/profile` fields | includes `mtf`, `dataValidity` | absent |
| `availabelBalance` | the real balance | `1000000.0` |
| `POST /v2/marketfeed/ltp` | 200 with quotes | **404 — data APIs absent** |
| order matching | a real exchange | **none — orders rest at `PENDING` and never fill** |

The `/v2/holdings` row is the cautionary one: **testing only against the
sandbox would not have found production's 500.** Fixtures captured from the
sandbox live in their own directory for this reason.

**There is no matching engine.** An order rests at `PENDING` and is never
filled, whatever its price — a limit at the upper circuit band sat unfilled
indefinitely. So the sandbox cannot exercise the fill path at all: no
`FillReport` and no `PositionStatusReport` has ever been produced from real
data, and `trade_book_row` and `position_row` remain documentation.

Sandbox tokens last 30 days. Keep them in `.env`, which is gitignored.

---

## Still unverified

Nothing below has been observed anywhere, and each is a place a future
measurement should be pinned:

- Any **super order** response — the sandbox 404s the endpoint and the live
  account has never placed one. Every super-order fixture is documentation.
- Any **forever order** response body beyond the empty list.
- A **filled** order, and therefore any real `GET /v2/trades` row, any
  position row, and the whole fill-report path. The sandbox cannot supply
  this — it has no matching engine.
- The **order-update WebSocket** (`wss://api-order-update.dhan.co`).
- Whether `POST /v2/orders/slicing` splits as documented.
