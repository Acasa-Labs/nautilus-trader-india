"""SPAN + exposure margin for Indian derivatives, as a Nautilus MarginModel.

THE STRUCTURAL LIMITATION, STATED UP FRONT. Nautilus asks for margin ONE
INSTRUMENT AT A TIME and sums the answers. The exchange does not work that
way: SPAN is charged on a PORTFOLIO. A short straddle is roughly 1.2x a
naked short rather than 2x, and a hedged structure's SPAN collapses toward
its maximum loss regardless of leg notionals -- measured across 50-900 point
wings, an iron fly's SPAN runs 0.21% to 3.52% of notional, a factor of 16,
while tracking 92-102% of maximum loss throughout.

So this model reproduces exactly two cases:

  LONG    -- zero. A bought option is paid for in full and blocks nothing.
  SHORT   -- SPAN + exposure on the leg's notional.

and OVER-charges every multi-leg structure. Over-charging is the SAFE
direction for sizing and the WRONG direction for research: it depresses
return on capital and can veto trades the exchange would have allowed. The
deviation is reported here rather than discovered later.

WHY THE PORTFOLIO-AWARE PATH IS NOT SHIPPED. It is implementable -- charge
each leg what it ADDS to the book it joins, `M(book with P) - M(book
without P)`, which sums to the portfolio figure. But Nautilus asks once per
position, at open, and does NOT re-ask when a position closes. Leg out of a
straddle and the survivor keeps the small incremental charge it was given as
an addition to a structure that no longer exists. That is the UNSAFE
direction, and trading a safe over-charge for an unsafe under-charge is not
an improvement. See docs/UPSTREAM_GAPS.md.

WHY `calculate_margin_init` CHARGES THE SHORT RATE. It is not told the side,
so it cannot tell a long from a short. The short rate is the conservative
choice for the case that reaches it.

MARGIN IS ON THE UNDERLYING, so the model reads spot when it is bound.
Unbound, the STRIKE stands in -- exact at the money, drifting with
moneyness. A fallback, not the design: a deep OTM short would otherwise be
scored on a notional the exchange never used.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from pathlib import Path

import yaml
from nautilus_trader.accounting.margin_models import MarginModel
from nautilus_trader.model.currencies import INR
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.objects import Money

_RATES_PATH = Path(__file__).parent / "data" / "margin_rates.yaml"


@dataclass(frozen=True, slots=True)
class MarginRates:
    """Fitted fractions of short-leg notional, with their provenance.

    `error_pct` is the WORST relative error over the calibration grid, not
    the average, so nothing downstream can claim more precision than the fit
    has.
    """

    span_pct: Decimal
    exposure_pct: Decimal
    calibrated_on: str
    error_pct: Decimal
    observations: int

    @property
    def total_pct(self) -> Decimal:
        return self.span_pct + self.exposure_pct


@cache
def load_rates(path: Path = _RATES_PATH) -> MarginRates:
    raw = yaml.safe_load(path.read_text())
    naked = raw["naked_short"]
    return MarginRates(
        span_pct=Decimal(str(naked["span_pct"])),
        exposure_pct=Decimal(str(naked["exposure_pct"])),
        calibrated_on=str(raw["calibrated_on"]),
        error_pct=Decimal(str(raw["error_pct"])),
        observations=int(raw["observations"]),
    )


class IndianOptionMarginModel(MarginModel):
    """Notional-fraction SPAN + exposure, per position. See module docstring."""

    def __init__(self, rates: MarginRates | None = None) -> None:
        super().__init__()
        self.rates = rates or load_rates()
        self._cache = None
        self._index_id = None

    def bind_spot(self, cache, index_instrument_id) -> None:
        """Let the model read the underlying, which is what margin is on."""
        self._cache, self._index_id = cache, index_instrument_id

    # `bind_book` is deliberately absent. See the module docstring: the
    # portfolio-aware path trades a safe over-charge for an unsafe
    # under-charge until Nautilus re-evaluates margin on position change.
    # `test_there_is_no_bind_book` stops it being added back unread.

    def _spot(self, instrument) -> Decimal:
        if self._cache is not None and self._index_id is not None:
            for bar_type in self._cache.bar_types(instrument_id=self._index_id):
                bar = self._cache.bar(bar_type)
                if bar is not None:
                    return bar.close.as_decimal()
        return instrument.strike_price.as_decimal()

    def _short_margin(self, instrument, quantity) -> Money:
        units = int(quantity) * int(instrument.multiplier)
        return Money(self._spot(instrument) * units * self.rates.total_pct, INR)

    def calculate_margin_init(
        self, instrument, quantity, price, leverage: Decimal,
        use_quote_for_inverse: bool = False,
    ) -> Money:
        # No side is supplied here; see the module docstring.
        return self._short_margin(instrument, quantity)

    def calculate_margin_maint(
        self, instrument, side, quantity, price, leverage: Decimal,
        use_quote_for_inverse: bool = False,
    ) -> Money:
        if side == PositionSide.LONG:
            # The premium already left the account as cash. Charging margin
            # on top double-counts it.
            return Money(0, INR)
        return self._short_margin(instrument, quantity)
