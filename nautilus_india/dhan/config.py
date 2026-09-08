"""Configuration for the Dhan clients.

Credentials may be given explicitly or left None to resolve from the
environment at connect time. They are `str | None` rather than a credentials
object because a Nautilus config is serialisable, and a serialised config
with a token in it is a token in whatever wrote it out.
"""

from __future__ import annotations

from collections.abc import Mapping

from nautilus_trader.config import LiveDataClientConfig, LiveExecClientConfig

from nautilus_india.dhan.constants import BASE_URL, PRODUCT_INTRADAY, SCRIP_MASTER_URL


class DhanDataClientConfig(LiveDataClientConfig, frozen=True):
    client_id: str | None = None
    access_token: str | None = None
    base_url: str = BASE_URL
    scrip_master_url: str = SCRIP_MASTER_URL


# TWO SWITCHES, NEVER ONE. A stray import cannot set an environment variable
# and a stray environment variable cannot construct a client, so neither
# accident alone can put a real order on the exchange.
LIVE_ORDERS_ENV = "NAUTILUS_INDIA_LIVE_ORDERS"
LIVE_ORDERS_VALUE = "1"


class DhanExecClientConfig(LiveExecClientConfig, frozen=True):
    client_id: str | None = None
    access_token: str | None = None
    base_url: str = BASE_URL
    scrip_master_url: str = SCRIP_MASTER_URL
    # False by default and deliberately: a config nobody edited reads,
    # reports, and sends nothing.
    live_orders: bool = False
    # INTRADAY or MARGIN for F&O -- CNC and MTF are refused by the segment.
    product_type: str = PRODUCT_INTRADAY


def submission_refusal(
    config: DhanExecClientConfig, env: Mapping[str, str]
) -> str | None:
    """Why this client may not submit, or None if it may.

    Returns a REASON rather than a boolean because the caller puts it in an
    `OrderDenied` event, and "denied" with no reason is a support ticket.

    The environment value is compared exactly. `true`, `yes` and `0` are all
    things somebody types meaning something, and a switch that accepts an
    approximation of itself is a switch that can be flipped by accident.
    """
    missing = []
    if not config.live_orders:
        missing.append("`live_orders=True` on DhanExecClientConfig")
    if env.get(LIVE_ORDERS_ENV) != LIVE_ORDERS_VALUE:
        missing.append(f"{LIVE_ORDERS_ENV}={LIVE_ORDERS_VALUE} in the environment")
    if not missing:
        return None
    return (
        "live order submission is disabled: " + " and ".join(missing) + " is not "
        "set. Both are required and neither may be collapsed into the other -- a "
        "stray import cannot set an environment variable, and a stray environment "
        "variable cannot construct a client."
    )
