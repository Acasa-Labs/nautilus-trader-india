from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from nautilus_india.core.calendar import IST, NaiveDatetimeError, is_open, session_bounds
from nautilus_india.core.enums import Exchange


def test_nse_trades_0915_to_1530_ist():
    start, end = session_bounds(Exchange.NSE, date(2026, 9, 7))
    assert start == datetime(2026, 9, 7, 9, 15, tzinfo=IST)
    assert end == datetime(2026, 9, 7, 15, 30, tzinfo=IST)


def test_mcx_trades_into_the_evening():
    """MCX is not NSE's hours. Reusing 15:30 for MCX would silently declare
    the commodity market shut for its busiest six hours."""
    start, end = session_bounds(Exchange.MCX, date(2026, 9, 7))
    assert start.time() == time(9, 0)
    assert end.time() == time(23, 30)


def test_a_weekday_inside_the_session_is_open():
    monday_noon = datetime(2026, 9, 7, 12, 0, tzinfo=IST)
    assert is_open(Exchange.NSE, monday_noon) is True


def test_a_weekend_is_closed():
    saturday_noon = datetime(2026, 9, 5, 12, 0, tzinfo=IST)
    assert is_open(Exchange.NSE, saturday_noon) is False


def test_the_boundary_is_inclusive_at_the_open_and_at_the_close():
    assert is_open(Exchange.NSE, datetime(2026, 9, 7, 9, 15, tzinfo=IST)) is True
    assert is_open(Exchange.NSE, datetime(2026, 9, 7, 15, 30, tzinfo=IST)) is True
    assert is_open(Exchange.NSE, datetime(2026, 9, 7, 9, 14, 59, tzinfo=IST)) is False
    assert is_open(Exchange.NSE, datetime(2026, 9, 7, 15, 30, 1, tzinfo=IST)) is False


def test_a_non_ist_datetime_is_converted_not_misread():
    """09:00 UTC is 14:30 IST -- inside the session. Reading the clock face
    instead of the instant would call it closed."""
    utc_morning = datetime(2026, 9, 7, 9, 0, tzinfo=ZoneInfo("UTC"))
    assert is_open(Exchange.NSE, utc_morning) is True


def test_a_naive_datetime_is_refused():
    """A naive datetime is read as IST wherever it came from, and a session
    boundary is exactly where that silently shifts by hours."""
    with pytest.raises(NaiveDatetimeError):
        is_open(Exchange.NSE, datetime(2026, 9, 7, 12, 0))  # noqa: DTZ001
