# Configuration

Every field, every environment variable, and what each one changes.

---

## Environment variables

| Variable | Read by | Required | Notes |
| --- | --- | --- | --- |
| `DHAN_CLIENT_ID` | both clients, at connect | when the config leaves `client_id` as `None` | |
| `DHAN_ACCESS_TOKEN` | both clients, at connect | when the config leaves `access_token` as `None` | **Lives 24 hours from minting** |
| `NAUTILUS_INDIA_LIVE_ORDERS` | execution, per submission | to place any order | Must be exactly `1` |

Both credentials are resolved **as one set**, naming whichever is missing:

```
missing DHAN_ACCESS_TOKEN. Set them in the environment; Dhan's access token
rotates every 24 hours, so a static value in a checked-in file will be stale.
```

Copy [`.env.example`](../.env.example) to `.env`, which is git-ignored.

### The token expires 24 hours after it is minted

`exp` is exactly `iat + 86400` — measured 2026-09-04/05 and confirmed against
`GET /v2/profile`'s `tokenValidity`. The clock starts at minting rather than
at a fixed hour, so **a token minted during market hours dies during market
hours.**

```python
from nautilus_india.dhan.auth import from_env, token_expiry, token_is_live

creds = from_env()          # raises MissingCredentialsError, naming what is absent
token_expiry(creds.access_token)    # datetime, UTC
token_is_live(creds.access_token)   # False once under 30 minutes remain
```

`token_is_live` carries a 30-minute margin by default. Reporting a token live
up to its last second is how a feed drops thirty minutes after a healthy
looking start. Only the payload is read and **the signature is never
verified** — Dhan does that; this is a local check so a session can refuse to
start rather than fail at the first request.

`DhanCredentials.__repr__` redacts the token. It is meant to appear in no log,
at any level.

---

## `DhanDataClientConfig`

```python
from nautilus_india.dhan import DhanDataClientConfig
```

| Field | Default | Meaning |
| --- | --- | --- |
| `client_id` | `None` | Resolves from `DHAN_CLIENT_ID` at connect time |
| `access_token` | `None` | Resolves from `DHAN_ACCESS_TOKEN` at connect time |
| `base_url` | `https://api.dhan.co` | Point at `https://sandbox.dhan.co` to probe |
| `scrip_master_url` | Dhan's detailed CSV | The ~32 MB instrument master |

Plus everything on Nautilus's `LiveDataClientConfig`, of which
`instrument_provider` matters most:

```python
InstrumentProviderConfig(
    load_all=True,
    filters={"underlyings": {"NIFTY"}},   # or {"instrument_classes": {...}}
)
```

The master carries ~200,000 contracts. Filtering is the difference between a
two-second start and holding every Indian contract in the cache.

> **Leave `client_id` and `access_token` as `None`.** A Nautilus config is
> serialisable, and a serialised config with a token in it is a token in
> whatever wrote it out. Resolving at connect time also picks up a rotated
> token rather than one captured when the config was built.

---

## `DhanExecClientConfig`

Everything above, plus:

| Field | Default | Meaning |
| --- | --- | --- |
| `live_orders` | `False` | **Switch one of two.** Without it every order is denied before anything is sent |
| `product_type` | `INTRADAY` | `CNC`, `INTRADAY`, `MARGIN`, `MTF`, `CO`, `BO`. `CNC` and `MTF` are refused by the F&O segment; `INTRADAY` is the default because it is the only one whose margin `core.margin` models |
| `slice_over_freeze_limit` | `False` | Routes to `POST /v2/orders/slicing`. **Turns one order into several**, each with its own id and its own fills |
| `after_market_order` | `False` | Sends for release at `amo_time` rather than now |
| `amo_time` | `OPEN` | `PRE_OPEN`, `OPEN`, `OPEN_30`, `OPEN_60`. Applies only once `after_market_order` is true |
| `order_updates` | `False` | Subscribes to `wss://api-order-update.dhan.co`. **Never observed** — the socket refuses sandbox credentials |

Every default is the conservative one. **A config nobody edited reads, reports
and sends nothing.**

### The second switch

`live_orders=True` alone does nothing. `NAUTILUS_INDIA_LIVE_ORDERS=1` must
also be in the environment, compared **exactly** — `true`, `yes` and `0` are
all things somebody types meaning something, and a switch that accepts an
approximation of itself can be flipped by accident.

Ask before you submit, if you want to check without trying:

```python
from nautilus_india.dhan.config import submission_refusal
import os

reason = submission_refusal(config, os.environ)   # None means it may submit
```

It returns a *reason* rather than a boolean because the caller puts it in an
`OrderDenied` event, and "denied" with no reason is a support ticket.

---

## Pointing at the sandbox

`https://sandbox.dhan.co` needs **no IP whitelisting**, which makes it the only
way to exercise the write path from an ordinary machine:

```python
DhanExecClientConfig(base_url="https://sandbox.dhan.co", live_orders=True)
```

Its token is separate from the live one and much longer-lived: the one used
to build this package expires roughly 30 days out. Note that a sandbox token
carries no `iat` claim, so unlike the live token its exact lifetime is
inferred from `exp` rather than measured.

**It is not production, in measured ways.** `GET /v2/holdings` answers
`200 []` there and `500` on a live account. It has **no matching engine**, so
nothing placed there ever fills, whatever the price. A shape seen only in the
sandbox is strong evidence and not proof — the divergences are listed in
[Dhan API notes](DHAN_API_NOTES.md).

---

## Cost and margin models

Neither takes configuration in the usual sense; both take an optional override
of the table they read.

```python
from nautilus_india.core.fees import CostModel, IndianOptionFeeModel
from nautilus_india.core.margin import IndianOptionMarginModel, load_rates

CostModel()                                    # packaged rates.yaml
CostModel(rates_path=Path("my_rates.yaml"))    # your own, same schema

IndianOptionFeeModel()                         # wraps a CostModel
IndianOptionMarginModel()                      # packaged margin_rates.yaml
IndianOptionMarginModel(rates=load_rates(Path("my_margin.yaml")))
```

A margin model can be told where to read the underlying, which is what margin
is actually levied on:

```python
margin.bind_spot(cache, index_instrument_id)   # otherwise the strike is used
```

There is deliberately no `bind_book`. See
[Core without a broker](core-without-a-broker.md).
