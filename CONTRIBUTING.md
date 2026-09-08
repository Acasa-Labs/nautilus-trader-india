# Contributing

Contributions are welcome. This page covers setup, what a good change looks
like here, and the one kind of contribution this repository values above code.

**Read [`CLAUDE.md`](CLAUDE.md) first.** It holds the rules that must not be
weakened, each with the measurement that put it there. It is deliberately not
repeated here: two copies of a rule drift, and the copy that drifts is the one
nobody reviews. This page tells you how to work; that page tells you what is
non-negotiable.

---

## The most valuable contribution is a measurement

This package's hardest problem is not code. It is that **the largest surfaces
have never been observed.** No order placed by this codebase has ever filled.
The order-update socket has never delivered a frame. The charge model has
never been checked against a real contract note.

If you have a live Dhan account, you can settle questions nobody here can. In
rough order of value:

| Measurement | Closes |
| --- | --- |
| A real fill — `GET /v2/trades` with a row in it | The fill and position report path, currently built entirely on documentation |
| A real contract note against `core.fees` | `verified: false` on every charge rate |
| One frame from `wss://api-order-update.dhan.co` | An entire client that has never parsed real input |
| A `GET /v2/super/orders` with legs in it | Whether `totalQuatity` is really misspelled, or whether the docs are |
| A response shape not already in the corpus | The error taxonomy, which is built from bodies rather than status codes |

A measurement is worth more than an opinion about what the API does, and this
API has repeatedly rewarded probing over reading — `correlationId` caps at 25
characters and not the documented 30, and the documented request body is
refused outright. Both were found by calling the sandbox, and both had passing
unit tests agreeing with the wrong value.

### How to file one

Open a **Venue behaviour** issue. It asks for what makes a measurement usable:
the endpoint, the request, the response verbatim, the date, and which tier the
observation belongs to — live, sandbox, or documented. An undated observation
about an API that changes is a rumour.

### How to turn one into a fixture

Fixtures are captured, never fabricated, and **the directory is the provenance
claim**. Read [`tests/dhan/fixtures/envelope/README.md`](tests/dhan/fixtures/envelope/README.md)
before adding one — it explains the four tiers and why they are not equally
strong. In short:

- `captured/` — a real call from this repository against the live account.
- `sandbox/` — a real call against `sandbox.dhan.co`. Real, but not
  production: `GET /v2/holdings` answers `200 []` there and `500` live.
- `recorded/` — a real body observed elsewhere and transcribed verbatim, with
  a note on why it cannot be re-captured here.
- `documented/` — built from Dhan's published field-by-field specification.

Every fixture is a JSON record, not a bare body:

```json
{
  "endpoint": "GET /v2/fundlimit",
  "http_status": 200,
  "provenance": "captured-live",
  "captured_at": "2026-09-08T09:32:51+05:30",
  "source": "live read-only GET from nautilus-trader-india",
  "why_it_matters": "`availabelBalance` is misspelled in the API and in the docs.",
  "scrubbed": ["dhanClientId -> SCRUBBED_CLIENT_ID"],
  "body": { "...": "the response, verbatim" }
}
```

`tests/dhan/test_errors.py` asserts that every fixture declares a `provenance`
matching its directory, so a mislabelled one cannot enter quietly.

**Scrub before you commit.** Client ids, order ids, account numbers, holdings,
balances. `scrubbed` records what you replaced so a later reader knows the body
was edited and where. Never edit a value for any other reason — a fixture
adjusted to make a test pass is a fabricated fixture with extra steps.

---

## Setup

Python 3.12–3.14. With [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/Acasa-Labs/nautilus-trader-india
cd nautilus-trader-india
uv sync --extra dev
uv run pytest
uv run ruff check .
```

Or with pip:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .
```

546 tests, and they run in a couple of seconds. **No test opens a socket** —
not one, deliberately — so the suite passes with no credentials, no network
and no Dhan account. That is also its limitation: see
[the testing section](#testing) below.

`kiteconnect` and `dhanhq` are dev dependencies for cross-checking payload
shapes while writing parsers. They are never imported at runtime, and
`tests/test_package.py` asserts it against `sys.modules`.

---

## Testing

**Tests come first.** A change lands as failing tests, then the code that
makes them pass. A phase is done when its tests assert the behaviour and pass
— not when the code exists.

Test names here are sentences that state a claim:

```
test_a_per_instrument_model_overcharges_a_multi_leg_short
test_the_strike_is_exact_not_coerced_through_a_double
test_a_full_packet_with_an_empty_book_yields_no_quote
```

A name that says what is true is a name that fails informatively. Prefer that
to `test_margin_2`.

**What the suite cannot tell you.** No socket is ever opened, so the live path
has unit tests and no integration coverage. If your change touches transport,
say in the pull request what you ran it against and what you saw. "The tests
pass" is true of code that has never spoken to Dhan.

---

## Rules that will fail review

All of these are in [`CLAUDE.md`](CLAUDE.md) with the measurement behind each.
Named here so a first-time contributor meets them before writing rather than
after:

1. **Live orders need two switches.** `live_orders=True` **and**
   `NAUTILUS_INDIA_LIVE_ORDERS=1`. Never collapse them into one.
2. **Decimal, never float.** `Price.from_str` and `Quantity.from_str`. Note
   that `Price(value, precision)` takes a C double, so passing a `Decimal`
   positionally still routes through binary floating point.
3. **Never import a vendor SDK client at runtime.** Three measured reasons.
4. **An ambiguous order request emits nothing.** A submission that times out,
   is reset, or returns an unreadable body may be working at the exchange, so
   the adapter says nothing and leaves it to reconciliation. Never add an
   `OrderRejected` on a timeout — it reports a live order as dead, and the
   position that follows is one nobody chose.
5. **Tables raise rather than default.** An uncovered date or unknown
   underlying raises. Falling back to another period's rates is a silent,
   uniform mispricing no downstream test catches.
6. **Quantity is in lots; the lot lives in `multiplier`.**
7. **Never copy NautilusTrader source into this repository.** It is LGPL-3.0;
   this package is MIT and links against it. Linking is fine. Vendoring its
   `_template` files is not, and would poison the licence.

If you believe one of these is wrong, the way to change it is a measurement
showing the reason it exists no longer holds — not an argument that it is
inconvenient.

---

## Pull requests

**One idea per pull request.** Reviewing "and also" is how a real problem
hides behind four cosmetic ones.

**Say what you verified and what you did not.** This repository states its own
uncertainty in its README, its rate tables and its fixture directories; a pull
request is held to the same standard. "I could not test this against a live
account" is a welcome sentence. Silence in its place is not.

**Commit subjects state what was learned, not what was typed.** The existing
log:

```
dhan: the sandbox found three bugs the whole test suite agreed with
dhan: build the whole documented order surface, not a subset of it
dhan: submit an order, and say nothing when we do not know
dhan: mark classify broken -- it parses an envelope nothing returns
```

Lowercase scope prefix (`dhan:`, `core:`, none for repo-wide), then a claim.
`fix bug` and `update orders.py` say nothing a diff does not.

**CI must be green.** `ruff check .` and `pytest` on Python 3.12 and 3.13,
plus a packaging job that builds the wheel and asserts the YAML data tables
are inside it — a unit test cannot catch a packaging defect, because the
source tree has the data files whether or not the wheel does.

Note that `pyproject.toml` classifies 3.14 as supported and CI does not test
it. Treat that as a known gap rather than a promise.

---

## Never commit

Credentials, access tokens, client ids, account numbers, real order records,
holdings, balances, or anything copied out of a private repository.

`.env` is git-ignored and `.env.example` carries names only. If you commit a
secret by accident, **rotate it before you rewrite history** — a Dhan access
token lives 24 hours from minting, which is long enough to matter. See
[`SECURITY.md`](SECURITY.md).

---

## Working with coding agents

`CLAUDE.md` is read by contributors and by coding agents alike, which is why
it carries rules rather than description. If an agent wrote part of your
change, that is fine and needs no disclaimer — but you are the author, and
"the model generated it" is not an answer to a review comment. Two things
agents get wrong here in particular, both of which the rules above exist to
catch: they collapse the two switches into one because it looks redundant, and
they write a plausible fixture instead of capturing a real one.

---

## Where to start

[`ROADMAP.md`](ROADMAP.md) lists what is planned and what "done" means for
each milestone. Everything on it is open. Milestone 0.2 — wiring the market
data client, whose decoder, protocol and parsers are already built and tested
— is the most self-contained piece of code on the list.
