# Roadmap

What is planned, in the order it is planned, and what "done" means for each.

There are no dates. This is alpha software written by one person; a date on
this page would be a guess dressed as a commitment. Milestones are ordered,
and each is independently useful — nothing in a later one is needed to prove
an earlier one.

**The ordering principle is that verification comes before surface.** Every
milestone below either turns a red row of the README's gap table green or
completes something already half-built. A second broker on top of an order
path that has never filled would double the unverified surface rather than
double the value, so Kite is late in this list on purpose.

---

## Where it stands today

| Component | State |
| --- | --- |
| `core` — symbology, instruments, lots, calendar, fees, margin | shipped, 80 tests |
| `dhan` — instrument master, binary decoder, feed protocol, tick parsers | shipped, 183 tests |
| `dhan` — the market data **client** | **not wired.** See 0.2 |
| `dhan` — execution | shipped, 216 tests. Exercised against the sandbox, never against a live account |
| `kite` — anything | not started |

---

## 0.2 — The market data client

The pieces of the live tape are built and tested in isolation. The client
that joins them is not: `DhanDataClient` defines `__init__` and nothing else,
so `_connect` and every `_subscribe_*` fall through to
`LiveMarketDataClient` and raise `NotImplementedError`. A node that registers
the data factory builds, and then fails the moment it starts.

What already exists and does not need rewriting:

| Piece | Where |
| --- | --- |
| Binary frame decoder, every packet in a multi-packet frame | `dhan/binary.py` |
| Subscription packets, chunking at Dhan's group size of 100, feed URL | `dhan/ws.py` |
| Ticker → `TradeTick`, full → `QuoteTick` | `dhan/data.py` |
| Instrument master, `security_id_for` / `instrument_id_for` | `dhan/providers.py` |

**Delivers:** `_connect` / `_disconnect`; `_subscribe_quote_ticks`,
`_subscribe_trade_ticks` and their unsubscribes; the reconnect and
resubscribe path; 20-level depth via `DEPTH_FEED_WSS`; historical bars
through `_request_bars`.

**Done when** a node streams live NSE quotes into a strategy — the exit
criterion the design spec set for this phase and that was never met — and
when a dropped socket says so loudly rather than leaving an engine that looks
healthy and learns nothing.

---

## 0.3 — Production verification of the order path

The order path is complete and has been driven end to end against Dhan's
[sandbox](https://sandbox.dhan.co/v2/). It has never run against a live
account, because placing an order needs a whitelisted static IP the machine
this was written on does not have. The sandbox is not a faithful mirror —
`GET /v2/holdings` answers `200 []` there and `500` in production — so it
raises confidence without settling it.

**Delivers:**

- A whitelisted static IP, and a first live place / modify / cancel.
- **A real fill.** The sandbox has no matching engine: orders rest at
  `PENDING` for ever, whatever the price. So `generate_fill_reports` and
  `generate_position_status_reports` have never produced a report from real
  data, and every fixture behind them is Dhan's documentation. This is the
  largest untested surface in the package.
- The order-update socket accepting live credentials, so at least one real
  frame is parsed. It refuses sandbox credentials today, which is why
  `order_updates` is off by default.
- **A real contract note**, reconciled against `core.fees`.

**Done when** the gap table in `README.md` has no row reading "never run" or
"never filled"; the fill and position fixtures move from
`tests/dhan/fixtures/envelope/documented/` to `.../live/`; and the charge
rates in `rates.yaml` carry `verified: true` — or carry a written account of
where the model and the note disagree, which is a result too.

---

## 0.4 — Cost and margin correctness

Both items are core NautilusTrader behaviours rather than adapter defects,
and both are written up with their direction of error and their measured size
in [`docs/UPSTREAM_GAPS.md`](docs/UPSTREAM_GAPS.md).

**Exercise STT at settlement.** Nautilus settles an expiring contract through
its own path rather than through `FeeModel.get_commission`, so
`IndianOptionFeeModel` cannot charge it. A position held to expiry is
under-costed, and on a small ITM winner the real charge can exceed the entire
profit. `CostModel.exercise_cost` computes it and must be called by hand
today. The fix is a settlement hook upstream.

**Portfolio margin.** Nautilus asks a position for margin once as it opens
and does not re-ask when another leg of the same structure closes. What ships
is per-instrument and therefore over-charges a short straddle by 1.66×
(₹343,689 against a measured ₹207,267). Over-charging is safe for sizing and
wrong for research. **The portfolio-aware path stays out until the upstream
fix lands** — it trades a safe over-charge for an unsafe under-charge, which
is not an improvement.

**Historical rate dating.** Rates before 2024-10-01 are estimated rather than
measured, and `rates.yaml` says so per row. A backtest over that period may
mis-charge STT.

**Done when** both upstream items are merged or have a linked open pull
request, and no row in `rates.yaml` before 2024-10-01 is still an estimate.

---

## 0.5 — Completing the Dhan surface

**NSE commodity options.** Dhan publishes no market-feed segment code for the
NSE commodity segment, so its 23,870 `OPTFUT` contracts are listed but not
subscribable. Dhan's own SDK cannot address them either — it enumerates
segments 0–5, 7 and 8, leaving 6 unassigned. This is blocked on Dhan, and
tracked rather than guessed: a wrong segment names a different instrument,
and Dhan answers HTTP 200 with empty data for one that does not exist, so a
guess would look like a quiet market rather than an error.

**`GTC`.** Sent as `DAY` today, because `/v2/orders` rests nothing overnight.
Dhan's equivalent is a forever order, which this package implements as an
explicit `submit_forever_order` call rather than as a route for `GTC` —
quietly turning "rest at the exchange" into "rest at the broker behind a
trigger" would be a different order from the one asked for. Whether that
should become an opt-in config flag is an open question, not a decided one.

**`_batch_cancel_orders`**, and the option chain endpoint.

**Done when** every instrument the provider lists is subscribable, or the
reason it is not is named with the date it was last checked.

---

## 0.6 — The Kite adapter

Phases 6 and 7 of the design: Kite protocol (`http`, `ws`, `auth`,
`parsing`), then `providers`, `data`, `execution`, `factories`. Dhan went
first because its measured knowledge was the deepest; Kite follows the shape
Dhan proves.

Two constraints are known in advance and will not be papered over:

- **Kite historical data is a paid add-on.** Without it the endpoint returns
  403. The adapter will degrade honestly and name the add-on rather than
  falling back to another source and pretending.
- **Kite's access token needs a daily interactive browser login.** No
  headless path exists that respects Zerodha's terms. Dhan's 24-hour JWT can
  be renewed unattended; Kite's cannot.

**Done when** a strategy written against `NIFTY260804002455000CE.NSE` runs
unchanged under `ClientId("KITE")`. That is the whole point of putting the
venue on the exchange rather than the broker, and it is not proven until a
second broker exists.

---

## 1.0 — A stable API

Until then, **the API will change without deprecation. Pin an exact version.**

**Done when** every remaining row in the README's gap table is bounded and
measured rather than unknown, and a breaking change requires a deprecation
cycle.

---

## Not planned

Naming these saves everyone an issue.

| | Why not |
| --- | --- |
| A fork of NautilusTrader | A fork must stay LGPL-3.0 and cannot be relicensed, means a perpetual rebase, and requires replacing the engine a user already runs. No Indian-market concept requires editing core — the two genuine gaps are worth a targeted upstream pull request, not a permanent fork |
| Transport built on `kiteconnect` or `dhanhq` | `dhanhq` 2.2.0 silently discards every packet after the first in a frame, and renders prices as strings and timestamps without a date; `kiteconnect` 5.2.1's ticker is Twisted, which means a second event loop on another thread. The three reasons are measured and recorded in `CLAUDE.md` |
| An in-tree Rust adapter | In-tree adapters are Rust crates projected through PyO3. This is an out-of-tree Python extension package, which is a different and still-supported hook |
| Brokers beyond Dhan and Kite | Not a policy — a capacity statement. A third broker before the first two are verified would be surface without evidence |
| Strategy, research or backtest tooling | NautilusTrader's job. This package supplies Indian instruments, costs and connectivity to it |

---

## Contributing to any of this

Every milestone above is open. The highest-value contribution to 0.3 in
particular is not code: it is **a measurement from a live account** — a real
fill, a real contract note, a real order-update frame — captured, scrubbed
and filed with its provenance. See [`CONTRIBUTING.md`](CONTRIBUTING.md).
