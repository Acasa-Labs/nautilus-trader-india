import re
from datetime import date

import pytest

from nautilus_india.core.lots import (
    UnknownLotSizeError,
    is_verified_lot_size,
    lot_size,
    lot_size_source,
)


def test_lot_size_is_keyed_by_expiry_not_by_trade_date():
    """Two contracts alive on the same day can carry different lots.

    NSE revises lot size per series, and a long-dated contract keeps the lot
    it was listed under. The archive shows 2026-06-25 at 25 while 2026-06-30
    is at 65. A trade-date lookup cannot express that and is wrong for every
    weekly straddling a revision.
    """
    assert lot_size("NIFTY", date(2026, 6, 25)) == 25
    assert lot_size("NIFTY", date(2026, 6, 30)) == 65


def test_an_observed_expiry_is_verified_and_says_where_it_came_from():
    assert is_verified_lot_size("NIFTY", date(2026, 6, 30)) is True
    assert "bhavcopy" in lot_size_source("NIFTY", date(2026, 6, 30))


def test_an_unknown_underlying_raises_rather_than_defaulting():
    """A wrong lot size scales every P&L uniformly, so nothing downstream
    catches it. Raising is the only safe answer."""
    with pytest.raises(UnknownLotSizeError, match="NOTATHING"):
        lot_size("NOTATHING", date(2026, 6, 30))


def test_an_uncovered_expiry_raises_and_says_how_to_fix_itself():
    with pytest.raises(UnknownLotSizeError, match=re.escape("lot_sizes.yaml")):
        lot_size("NIFTY", date(1990, 1, 1))


def test_a_regime_fallback_is_used_but_is_not_verified():
    """A regime hit still returns a lot -- it just admits to being one.

    The flag exists so a result that leaned on an interpolated lot can say
    so. Refusing the fallback entirely would make most of the archive
    unusable; hiding it would make every P&L look equally trustworthy.
    """
    from nautilus_india.core.lots import _lookup

    lot, verified, source = _lookup("NIFTY", date(2027, 9, 30))
    assert lot > 0
    assert verified is False
    assert "regime" in source


def test_an_observed_lot_overrides_the_regime_it_falls_inside():
    """The whole reason the exact map is consulted first.

    2027-06-24 was observed at 25, inside a regime that says 65 -- a
    long-dated series keeping the lot it was listed under. A range table
    alone would mis-scale it by 2.6x, and no downstream assertion would
    notice, because a wrong lot scales every P&L uniformly.
    """
    assert lot_size("NIFTY", date(2027, 6, 24)) == 25
    assert is_verified_lot_size("NIFTY", date(2027, 6, 24)) is True

    from nautilus_india.core.lots import _lookup

    _, regime_verified, regime_source = _lookup("NIFTY", date(2027, 9, 30))
    assert regime_verified is False
    assert "2025-12-24..2027-12-31" in regime_source
