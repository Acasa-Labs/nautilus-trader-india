"""Session bounds per exchange, in IST.

HOLIDAYS ARE NOT MODELLED, and the limitation is stated rather than hidden.
There is no verifiable trading-holiday source vendored here, so on a weekday
holiday `is_open` reports open, the feed delivers nothing, and a REST poll
returns the previous session's prints. That is a few wasted polls a year and
no wrong number: the prices are real, they are simply stale. A holiday table
would be better and needs a source we can cite, not one we type in.

MCX IS NOT NSE. Reusing 15:30 for commodities would declare the market shut
for its busiest six hours. Each exchange carries its own bounds.

NAIVE DATETIMES ARE REFUSED. A naive datetime is read as IST wherever it came
from, and a session boundary is exactly where that silently shifts by hours.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from nautilus_india.core.enums import Exchange

IST = ZoneInfo("Asia/Kolkata")

# Saturday and Sunday.
_WEEKEND = (5, 6)

# Source: NSE and BSE equity/derivatives normal market 09:15-15:30 IST; MCX
# non-agri evening session to 23:30 IST (23:55 during US daylight time,
# which is deliberately NOT modelled -- see the module docstring on why an
# uncited boundary is worse than a stated approximation).
_SESSIONS: dict[Exchange, tuple[time, time]] = {
    Exchange.NSE: (time(9, 15), time(15, 30)),
    Exchange.BSE: (time(9, 15), time(15, 30)),
    Exchange.MCX: (time(9, 0), time(23, 30)),
}


class NaiveDatetimeError(ValueError):
    """A datetime arrived without a timezone."""


def session_bounds(exchange: Exchange, day: date) -> tuple[datetime, datetime]:
    """The IST open and close instants for `exchange` on `day`."""
    start, end = _SESSIONS[exchange]
    return (
        datetime.combine(day, start, tzinfo=IST),
        datetime.combine(day, end, tzinfo=IST),
    )


def _ist(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(IST)
    if now.tzinfo is None:
        raise NaiveDatetimeError(
            "this needs a timezone-aware datetime: a naive one is read as "
            "IST wherever it came from, and a session boundary is exactly "
            "where that silently shifts by hours."
        )
    return now.astimezone(IST)


def is_open(exchange: Exchange, now: datetime | None = None) -> bool:
    """True while `exchange` is trading. See the docstring on holidays."""
    moment = _ist(now)
    if moment.weekday() in _WEEKEND:
        return False
    start, end = session_bounds(exchange, moment.date())
    return start <= moment <= end
