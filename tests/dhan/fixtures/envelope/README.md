# The Dhan response-envelope corpus

Every file here is a real Dhan v2 response body. None was written by hand.

This corpus exists because the code it tests was once written from a shape
somebody believed in. `classify` parsed `{"status": "success", "data": {...}}`
on success and `{"status": ..., "remarks": {"error_code", "error_message"}}` on
failure — an envelope **no Dhan v2 endpoint returns**, in either direction.
The tests were green, because they asserted the same invented shape the code
implemented. A successful order therefore read as a rejection, and the one
exception whose job is to stop the session could not be raised at all.

So: fixtures are captured, never fabricated. A hand-written "valid" payload
encodes what we believe rather than what the venue sends, which is precisely
the bug fixtures exist to catch.

## Four tiers, kept in separate directories

The directory IS the provenance claim, so a reader sees it in a listing rather
than by opening a file. Each fixture also carries a `provenance` field.

### `captured/` — called from this repository, 2026-09-08

Live read-only `GET`s against the real account, credentials scrubbed
(`dhanClientId` → `SCRUBBED_CLIENT_ID`). The strongest evidence there is.
`rate_limited.json` was obtained by deliberately tripping the per-second Data
API limit with concurrent chart reads; charts are a read and place no order.

### `sandbox/` — real calls, simulated exchange

`https://sandbox.dhan.co`, which needs no IP whitelisting and so is the only
way to exercise the WRITE path from this machine. These are real responses to
real requests, including the first orders this codebase has ever placed.

**They are not production.** The sandbox diverges in measured ways — most
sharply, `GET /v2/holdings` answers `200 []` there and `500 DH-1111` on the
live account, so testing only here would have missed the production bug. The
divergences are listed in `docs/DHAN_API_NOTES.md`; a shape seen only in this
tier is strong evidence and not proof.

Three bugs in this adapter were found by this tier in under an hour, each of
which every unit test had agreed with: empty strings in the request body,
a `correlationId` limit of 25 rather than the documented 30, and a zero-date
sentinel that parses to a negative timestamp Nautilus accepts in silence.

### `recorded/` — real bodies, observed elsewhere

Write-path responses transcribed verbatim from a probe of the live account on
2026-09-08, recorded in a private repository that is not published here. Real,
but **this repository cannot re-observe them**: placing an order needs a
whitelisted static IP, and the machine holding those credentials has a dynamic
residential address. Each file says what it came from and why it cannot be
re-captured.

### `documented/` — Dhan's published specification

<https://dhanhq.co/docs/v2/orders/> documents every request and every response
on the order path, field by field, with the enum values for each. That is a
complete specification, and it is the source these fixtures are built from.

They are kept in their own directory because one fact about them is worth
knowing and cannot be recovered from the file otherwise: **no row of these
shapes has arrived here**. This account has never traded, and placing an
order needs a whitelisted static IP the machine holding these credentials does
not have. Each file says so in `not_observed_here`, and carries
`verified_against_live_account: false`.

That is a statement about *this account*, not about the source. The field
names are Dhan's own; the values are Dhan's own samples. Build against them.
When a real order finally exists, capture the response and move the file up a
tier — the difference will be in the values, not the shape.

This tier holds the **requests** too, not just the responses. Pinning the
payload this adapter builds against the payload Dhan documents is a test the
response fixtures cannot do, and it is the one that catches a missing required
field — which matters because Dhan validates in order (quantity, then the IP,
then the instrument), so an incomplete payload fails with "quantity is
required" and never reaches the check that would have told you the real
problem.

## What the corpus establishes

| Shape | Seen on | HTTP |
| --- | --- | ---: |
| bare object — the body IS the payload | `/v2/fundlimit`, `/v2/profile`, `/v2/charts/intraday`, `POST /v2/orders` (documented) | 200 |
| bare array | `/v2/orders`, `/v2/positions`, `/v2/trades`, `/v2/super/orders`, `/v2/orders/{unknown}` | 200 |
| `{errorType, errorCode, errorMessage}` | `/v2/holdings`, `/v2/charts/intraday`, `POST /v2/orders` | 400, 429, 500 |
| `[{message, status: "ERROR"}]` — an error INSIDE a bare array | `/v2/ip/getIP` | **200** |
| `{status: "success", data: ...}` | `/v2/optionchain/expirylist`, `/v2/marketfeed/ltp` | 200 |
| `{status: "failed", data: {code: message}}` | `/v2/optionchain/expirylist` | 400 |

### Two rows the source material did not have

The vendor notes this rewrite started from record four shapes and put
`{status: "success", data: {...}}` in a row of its own marked **NOTHING**.
That cell is true of every endpoint they probed — order, portfolio, funds,
profile — and false in general. `POST /v2/optionchain/expirylist` and
`POST /v2/marketfeed/ltp` both return exactly that envelope. **The envelope is
per endpoint FAMILY**, so a classifier has to read both, and one that unwraps
`data` unconditionally and one that never unwraps it are both wrong.

Its failure counterpart is worse, and is why the notes' proposed rule could
not be adopted as written. Those notes conclude: *"An error is a body carrying
`errorMessage`, or `status: "ERROR"`."* This body carries neither:

```json
{"status": "failed", "data": {"813": "Invalid SecurityId"}}
```

Under that rule it reads as a success, and the caller is handed
`{"813": "Invalid SecurityId"}` as its data. The notes' own warning —
*"the first rule written here after seeing three real bodies was still
wrong"* — turned out to apply to the rule the notes themselves propose. Both
rows were found by probing endpoints nobody had probed, which is the only
method that has ever worked on this API.

### Four consequences the corpus pins

1. **The container is not the discriminator.** A bare array is usually a
   success, but `/v2/ip/getIP` returns an error inside one at HTTP 200.
2. **Neither is the status code**, in either direction: an error at 200
   above, and parseable, actionable bodies at 400, 429 and 500.
3. **Neither is the presence of an error field**, per the `status: "failed"`
   row above.
4. **The error code is not diagnostic.** `DH-905` came back for `"Invalid
   IP"`, for `"quantity is required"` and for a 90-day span limit — it is the
   generic `Input_Exception`. Classify on the **message**.

What is left, after all four, is: an error is a body that *says* it failed —
by an error field, by a `status` that is not `success`, or by either of those
inside an array element — and the message is wherever that body puts it.

### The gap that used to be here — now closed

Every bare array in the corpus was once **empty**, because neither account had
traded, so `classify`'s rule for telling an error element from a data element
inside a list rested on documented rows alone.

`sandbox/order_book_row_real.json` closes it: an actual order-book row from an
actual placement, carrying `orderStatus` and no plain `status` key.

What is still unobserved anywhere is a **filled** order — so no real
`/v2/trades` row and no real position row exists, and the fill-report path is
still built on documentation. `docs/DHAN_API_NOTES.md` keeps that list.

## Adding to it

Capture where you can, cite where you cannot, and never compose. A `GET`
against the live account is free and read-only, so a shape a read can produce
belongs in `captured/`. A shape only a write produces belongs in `recorded/`
with a pointer to where it was observed, or in `documented/` with the doc URL
and the field list it came from. What is forbidden is the fourth thing: a body
somebody wrote because it looked right. `tests/dhan/test_errors.py` asserts
that every fixture declares a provenance matching its directory, so one cannot
enter quietly.
