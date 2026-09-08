# Core without a broker

`nautilus_india.core` turns Indian contracts into Nautilus instruments and
prices their transaction costs. **It talks to no network and knows no broker
exists** — enforced by `tests/test_package.py` against `sys.modules`, not by
convention.

So it is useful on its own, with no account, no credentials and no
connectivity: for backtests, for research, or for pricing what a trade would
have cost. It is also the most finished part of this package.

Every table here reports **how much it trusts itself**, and every lookup
**raises rather than defaulting**. An uncovered date or an unknown underlying
is an error, because falling back to another period's rates is a silent,
uniform mispricing that no downstream assertion catches.

---

## Symbology

A `ContractKey` is what a contract *is*, independent of who is quoting it.

```python
from datetime import date
from decimal import Decimal

from nautilus_india.core.enums import Exchange, InstrumentClass
from nautilus_india.core.symbology import (
    ContractKey, to_symbol, parse_symbol, to_instrument_id,
)

key = ContractKey("NIFTY", InstrumentClass.OPTION, date(2026, 8, 4), Decimal("24550"), "CE")

to_symbol(key)                    # 'NIFTY260804002455000CE'
to_instrument_id(key, Exchange.NSE)  # InstrumentId('NIFTY260804002455000CE.NSE')
parse_symbol(to_symbol(key)) == key  # True
```

The strike is a `Decimal` and is encoded in **paise**, which is why
`002455000` is 24550.00 rather than a rounded float. `24550.05` does not
survive a round trip through binary floating point, and a strike that shifts
by 5 paise names a different contract.

`parse_symbol` is the exact inverse, round-tripped by test rather than by eye.
A symbol is only read as an option if the *whole* shape matches, not just the
`CE`/`PE` suffix — an equity called `NICE` is not an option on `NI`.

---

## Instruments

Four constructors, all returning stock Nautilus types:

```python
from nautilus_india.core.instruments import (
    option_contract, futures_contract, equity, index_instrument,
)
from nautilus_india.core.enums import Exchange, InstrumentClass
from nautilus_india.core.symbology import ContractKey
from datetime import date
from decimal import Decimal

option_contract(
    ContractKey("NIFTY", InstrumentClass.OPTION, date(2026, 8, 4), Decimal("24550"), "CE"),
    Exchange.NSE,
)                                                    # OptionContract, multiplier 65

futures_contract(
    ContractKey("NIFTY", InstrumentClass.FUTURE, date(2026, 8, 28)),
    Exchange.NSE,
)                                                    # NIFTY260828FUT.NSE

equity("RELIANCE", Exchange.NSE)                     # RELIANCE.NSE
index_instrument("NIFTY", Exchange.NSE)              # NIFTY.NSE
```

`index_instrument` is the index itself: a reference for signals, not a
tradeable contract.

### The lot is in `multiplier`, and `lot_size` is 1

This is the single most expensive thing to get wrong in this package.

Nautilus prices a fill as `qty × multiplier × price`. One NIFTY contract is
one lot, so `lot_size` is `1` and the 65 lives in `multiplier`. Passing the
lot size as the quantity *as well* squares the position — a NIFTY 24550 CE
round trip reads as ₹78,585 against a true ₹1,209, because 65 lots of a
65-multiplier contract is 4,225 units.

---

## Lot sizes, with provenance

```python
from datetime import date
from nautilus_india.core.lots import lot_size, is_verified_lot_size, lot_size_source

lot_size("NIFTY", date(2026, 8, 4))              # 65
is_verified_lot_size("NIFTY", date(2026, 8, 4))  # False
lot_size_source("NIFTY", date(2026, 8, 4))
# 'regime 2025-12-24..2027-12-31, collapsed from observed expiries'
```

`is_verified_lot_size` is `True` only when that exact expiry was observed in
NSE's bhavcopy archive. `False` means the value was collapsed from a regime of
observed expiries around it — most likely right, and not the same as measured.
A number with no provenance is a guess, so every one carries its own.

An unknown underlying raises `UnknownLotSizeError`, naming where to harvest
the real value, rather than returning a plausible default.

---

## The market calendar

```python
from datetime import date, datetime
from zoneinfo import ZoneInfo
from nautilus_india.core.calendar import session_bounds, is_open
from nautilus_india.core.enums import Exchange

session_bounds(Exchange.NSE, date(2026, 8, 4))
# (2026-08-04 09:15 IST, 2026-08-04 15:30 IST)

IST = ZoneInfo("Asia/Kolkata")
is_open(Exchange.NSE, datetime(2026, 8, 4, 10, 0, tzinfo=IST))   # True
is_open(Exchange.NSE, datetime(2026, 8, 4, 18, 0, tzinfo=IST))   # False
```

A naive datetime raises `NaiveDatetimeError` rather than being assumed to be
IST, UTC or local. Passing `now=None` uses the current time.

---

## What a trade costs

```python
from datetime import date
from decimal import Decimal
from nautilus_trader.model.enums import OrderSide
from nautilus_india.core.fees import CostModel

costs = CostModel()
b = costs.leg_cost(date(2026, 8, 4), OrderSide.SELL, Decimal("100.00"), units=65)

b.stt         # 9.750000     sell side only
b.brokerage   # 20.0         flat, per executed order
b.exchange    # 2.309450000
b.sebi        # 0.00650000
b.stamp       # 0            buy side only
b.gst         # 4.01570100000
b.total       # 36.08165100000
```

`units`, not lots — 65 units is one NIFTY lot. GST is a tax on services, so it
touches brokerage and exchange charges and never STT or the SEBI turnover fee.
That was measured, not assumed.

### How much the rates trust themselves

```python
costs.rates_are_verified(date(2026, 8, 4))   # False
costs.reconciled_against(date(2026, 8, 4))
# 'https://dhan.co/calculators/brokerage-calculator/ on 2026-09-04,
#  NIFTY 08 SEP 18900 CE (lot 65). Seven cases from 1 to 100 lots and
#  buy/sell premiums 50-1000 reproduce the calculator total exactly.'
```

The flag flips to `True` only when a period has been reconciled against a real
**contract note**. Reproducing a broker's calculator is strong evidence about
the calculator. Rates before 2024-10-01 are estimated, and `rates.yaml` says
so per row.

A date no period covers raises `UnknownCostRatesError`.

### Charging fills automatically

```python
from nautilus_india.core.fees import IndianOptionFeeModel

fee_model = IndianOptionFeeModel()   # pass to a Nautilus engine or venue
```

> **It does not charge exercise STT.** Nautilus settles an expiring contract
> through its own path rather than through `FeeModel.get_commission`, so this
> model cannot see it. STT on an ITM option exercised at expiry is levied on
> **intrinsic** value at a rate far above the premium rate — on a small winner
> it can exceed the entire profit, and it is the charge naive backtests omit.
>
> Compute it yourself with `costs.exercise_cost(on, intrinsic, units)`, which
> is deliberately callable rather than approximated inside the fee model. See
> [Upstream gaps](UPSTREAM_GAPS.md).

---

## Margin

```python
from decimal import Decimal
from nautilus_trader.model.enums import PositionSide
from nautilus_india.core.margin import IndianOptionMarginModel, load_rates

margin = IndianOptionMarginModel()

rates = load_rates()
rates.span_pct       # 0.087657
rates.exposure_pct   # 0.020032
rates.error_pct      # 0.0406  <- the WORST relative error of the fit, not the average
rates.observations   # 9
rates.calibrated_on  # '2026-09-04'

margin.calculate_margin_maint(
    instrument, PositionSide.SHORT, instrument.make_qty(1), instrument.make_price(100), Decimal(1),
)   # 171844.72 INR   (measured exchange figure: ~175,000)

margin.calculate_margin_maint(
    instrument, PositionSide.LONG, instrument.make_qty(1), instrument.make_price(100), Decimal(1),
)   # 0.00 INR   the premium already left the account as cash
```

`error_pct` is the *worst* relative error of the fit rather than the average,
because an average hides the case that breaks you.

By default the model uses the strike as the spot. `bind_spot(cache,
index_instrument_id)` lets it read the underlying's last bar instead, which is
what margin is actually levied on.

> **It is per-instrument, so a multi-leg short is over-charged ~1.66×** — a
> short straddle costs ₹343,689 here against a measured exchange figure of
> ₹207,267. Over-charging is safe for sizing and wrong for research: it
> depresses return on capital and can veto trades the exchange would have
> allowed.
>
> There is deliberately **no** portfolio-aware path. Nautilus asks a position
> for margin once as it opens and does not re-ask when another leg closes, so
> the portfolio-aware version would trade a safe over-charge for an unsafe
> under-charge. `test_there_is_no_bind_book` stops it being added back by
> someone who has not read [Upstream gaps](UPSTREAM_GAPS.md).

---

## The two gaps, in one place

Both err in the unsafe direction if ignored, and both are core NautilusTrader
behaviours rather than defects here:

| Gap | Direction | Read |
| --- | --- | --- |
| Exercise STT is not charged at settlement | **Under**-costs a position held to expiry | [Upstream gaps §2](UPSTREAM_GAPS.md) |
| Margin is per-instrument, not per-portfolio | **Over**-charges a multi-leg short by ~1.66× | [Upstream gaps §1](UPSTREAM_GAPS.md) |
