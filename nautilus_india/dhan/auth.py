"""Dhan credentials and the 24-hour access token.

THE TOKEN IS NOT LONG-LIVED. `exp` is exactly `iat + 86400` -- measured
2026-09-04/05 and confirmed against `GET /v2/profile`'s `tokenValidity`. The
expiry is 24 hours after MINTING rather than at a fixed hour, so a token
minted during market hours dies during market hours. `token_is_live` carries
a margin for that reason: reporting a token live up to its last second is how
a feed drops thirty minutes after a healthy-looking start.

ONLY THE PAYLOAD IS READ, and its signature is never verified. This is not
authentication -- Dhan verifies the token. It is a local check so a session
can refuse to start rather than fail at the first request.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

CLIENT_ID_ENV = "DHAN_CLIENT_ID"
ACCESS_TOKEN_ENV = "DHAN_ACCESS_TOKEN"

DEFAULT_MARGIN = timedelta(minutes=30)


class MissingCredentialsError(RuntimeError):
    """A required environment variable is absent."""


class MalformedTokenError(ValueError):
    """The access token is not a readable JWT."""


@dataclass(frozen=True, slots=True)
class DhanCredentials:
    client_id: str
    access_token: str

    def __repr__(self) -> str:
        # The token is deliberately absent -- never in a log, at any level.
        return f"DhanCredentials(client_id={self.client_id!r}, access_token=<redacted>)"


def from_env(env: Mapping[str, str] | None = None) -> DhanCredentials:
    """Resolve both credentials as one set, naming whichever is missing."""
    source = os.environ if env is None else env
    missing = [k for k in (CLIENT_ID_ENV, ACCESS_TOKEN_ENV) if not source.get(k)]
    if missing:
        raise MissingCredentialsError(
            f"missing {' and '.join(missing)}. Set them in the environment; "
            "Dhan's access token rotates every 24 hours, so a static value in "
            "a checked-in file will be stale."
        )
    return DhanCredentials(source[CLIENT_ID_ENV], source[ACCESS_TOKEN_ENV])


def _claims(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise MalformedTokenError(
            f"access token is not a JWT: expected 3 dot-separated segments, got {len(parts)}"
        )
    payload = parts[1]
    # base64url in a JWT is unpadded. Without this, decoding fails on two
    # payload lengths out of three, which reads as an intermittent bug.
    payload += "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise MalformedTokenError(f"access token payload is not readable JSON: {exc}") from exc
    if not isinstance(claims, dict):
        raise MalformedTokenError("access token payload is not a JSON object")
    return claims


def token_expiry(token: str) -> datetime:
    """When this token dies, in UTC."""
    exp = _claims(token).get("exp")
    if not isinstance(exp, int | float):
        raise MalformedTokenError("access token carries no numeric `exp` claim")
    return datetime.fromtimestamp(exp, tz=UTC)


def token_is_live(
    token: str,
    now: datetime | None = None,
    margin: timedelta = DEFAULT_MARGIN,
) -> bool:
    """True while the token has more than `margin` left. See the docstring."""
    moment = now or datetime.now(UTC)
    return token_expiry(token) - margin > moment
