# Documentation

`nautilus-trader-india` supplies Indian instruments, transaction costs and
broker connectivity to NautilusTrader. Start with whichever describes you.

| I want to… | Read |
| --- | --- |
| Install it and see it work | [Getting started](getting-started.md) |
| Price Indian contracts without a broker account | [Core without a broker](core-without-a-broker.md) |
| Stream live market data | [Market data](market-data.md) |
| Place orders | [Execution](execution.md) |
| Look up a config field or an environment variable | [Configuration](configuration.md) |
| Work out why something failed | [Troubleshooting](troubleshooting.md) |

## Reference

These two are the reasoning behind the code, and are worth reading before
trusting it with money:

- **[Dhan API — measured behaviour](DHAN_API_NOTES.md)** — everything this
  package knows about Dhan's API that its documentation does not say, or says
  wrongly, each with how and when it was measured. Three of these break an
  order silently.
- **[Upstream gaps](UPSTREAM_GAPS.md)** — NautilusTrader core behaviours this
  package works around, with the direction and measured size of each error.
  Read it before holding a position to expiry or legging out of a multi-leg
  short.

Also in the repository root:

- [`README.md`](../README.md) — what this is, and the gap table listing what
  is known to be unverified.
- [`ROADMAP.md`](../ROADMAP.md) — what is planned and what "done" means.
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — how to work on it.
- [`CLAUDE.md`](../CLAUDE.md) — the rules that must not be weakened, and the
  measurement behind each. Read by contributors and coding agents alike.
- [`SECURITY.md`](../SECURITY.md) — reporting, and how to handle a token that
  lives 24 hours.

## What is honestly ready

Documentation that overstates readiness is how someone loses money, so:

| | State |
| --- | --- |
| **`core`** — symbology, instruments, lots, calendar, fees, margin | **Usable now.** No network, no broker, no account. Two known cost gaps, both named in [Upstream gaps](UPSTREAM_GAPS.md) |
| **Dhan instrument master** | **Usable now.** Downloads and parses ~200,000 contracts |
| **Dhan market data** | **Not wired.** The decoder, feed protocol and tick parsers are built and tested; the client that joins them is not. See [Market data](market-data.md) |
| **Dhan execution** | **Complete, and unproven against production.** Every endpoint is implemented and exercised against the sandbox. No order has ever filled. See [Execution](execution.md) |
| **Kite** | Not started |
