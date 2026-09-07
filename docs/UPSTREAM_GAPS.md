# Upstream gaps

Core NautilusTrader behaviours this package works around. Each is a candidate
for a targeted upstream pull request. None justifies a fork — see the design
spec, section 1.

## 1. Portfolio margin is asked once, at open

Nautilus asks a position for its margin as it opens and does **not** re-ask
when another leg of the same structure closes.

A per-leg incremental charge — `M(book with P) − M(book without P)` — sums to
the correct portfolio figure while a structure is being built. But leg out of
a straddle (close the call, keep the put) and the survivor keeps the small
incremental charge it was given as an addition to a structure that no longer
exists, when a naked short should block roughly five times as much.

**Direction: unsafe.** The account would be under-margined.

### What ships instead

`IndianOptionMarginModel` is per-instrument, so a multi-leg short is
**over**-charged. Measured on NIFTY 24550 CE, 1 lot, 2026-08-04 expiry:

| | This model | Exchange (measured) |
| --- | --- | --- |
| Naked short, 1 lot | ₹171,845 | ~₹175,000 |
| Short straddle, 1 lot | ₹343,689 | ₹207,267 |

That is a **1.66× over-charge** on the straddle. Over-charging is safe for
sizing and wrong for research: it depresses return on capital and can veto
trades the exchange would have allowed.
`test_a_per_instrument_model_overcharges_a_multi_leg_short` pins the ratio so
it cannot change silently, and `test_there_is_no_bind_book` stops the
portfolio-aware path being added back without reading this file.

**Do not** ship the portfolio-aware path before the upstream fix. It trades a
safe over-charge for an unsafe under-charge, which is not an improvement.

**Upstream fix:** re-evaluate portfolio margin on position *change*, not only
on position *open*.

## 2. Settlement bypasses `get_commission`

STT on an ITM option exercised at expiry is levied on **intrinsic** value at a
rate far above the premium rate — on a small winner it can exceed the entire
profit. It is the charge naive backtests omit.

Nautilus settles an expiring contract through its own path rather than through
`FeeModel.get_commission`, so `IndianOptionFeeModel` cannot charge it.

**Direction: unsafe.** A run held to expiry is under-charged.

**What ships instead:** nothing automatic. `CostModel.exercise_cost` computes
the charge and is callable directly. A strategy that holds to expiry must
apply it itself, and this file is where it finds out.

**Upstream fix:** a settlement hook that lets a `FeeModel` price an exercise.

## 3. `Price(value, precision)` takes a C double

Not a defect — a signature. But passing a `Decimal` positionally still routes
the value through binary floating point, which is easy to do by accident and
silent when it happens. Every price in this package is built with
`Price.from_str`. `test_the_strike_is_exact_not_coerced_through_a_double`
pins it.
