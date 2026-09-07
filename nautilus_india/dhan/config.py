"""Configuration for the Dhan clients.

Credentials may be given explicitly or left None to resolve from the
environment at connect time. They are `str | None` rather than a credentials
object because a Nautilus config is serialisable, and a serialised config
with a token in it is a token in whatever wrote it out.
"""

from __future__ import annotations

from nautilus_trader.config import LiveDataClientConfig

from nautilus_india.dhan.constants import BASE_URL, SCRIP_MASTER_URL


class DhanDataClientConfig(LiveDataClientConfig, frozen=True):
    client_id: str | None = None
    access_token: str | None = None
    base_url: str = BASE_URL
    scrip_master_url: str = SCRIP_MASTER_URL
