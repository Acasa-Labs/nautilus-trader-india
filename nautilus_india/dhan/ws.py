"""Dhan's subscription protocol, which is BINARY rather than JSON.

THE PACKET IS PADDED TO A HUNDRED SLOTS regardless of how many instruments
are sent. Dhan's own client does this, so the server expects the fixed size;
a short packet is ignored silently, which reads as "subscribed, but no data
ever arrives" -- the least diagnosable failure a feed has.

REFUSING IS BETTER THAN TRUNCATING, twice over. More than a hundred
instruments raises rather than silently subscribing to the first hundred: a
truncated subscription reports success while quietly missing instruments. A
security id longer than its 20-byte field raises for a sharper reason -- a
truncated id is a DIFFERENT contract, and Dhan answers 200 for an unknown
one, so the mistake would look like a quiet market rather than an error.

THE FEED URL IS A CREDENTIAL. The v2 handshake carries the access token in
the query string, so the URL must never be logged whole. `redacted_feed_url`
is what may be.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator, Sequence

from nautilus_india.dhan.constants import MARKET_FEED_WSS, SUBSCRIBE_GROUP_SIZE

# code(1) + message_length(2) + client_id(30) + auth(50) = 83 bytes.
_HEADER = struct.Struct("<bH30s50s")
_COUNT = struct.Struct("<I")
# segment(1) + security_id(20) = 21 bytes per instrument slot.
_SLOT = struct.Struct("<B20s")

_ID_FIELD_BYTES = 20
_AUTH_PADDING = b"\0" * 50


def subscription_packet(
    client_id: str,
    instruments: Sequence[tuple[int, str]],
    request_code: int,
) -> bytes:
    """One subscription packet. See the module docstring on padding."""
    if len(instruments) > SUBSCRIBE_GROUP_SIZE:
        raise ValueError(
            f"{len(instruments)} instruments exceeds Dhan's {SUBSCRIBE_GROUP_SIZE} "
            "per packet. Use `chunked` -- truncating here would subscribe to a "
            "subset and report success."
        )

    body = _COUNT.pack(len(instruments))
    for segment, security_id in instruments:
        encoded = security_id.encode()
        if len(encoded) > _ID_FIELD_BYTES:
            raise ValueError(
                f"security id {security_id!r} does not fit {_ID_FIELD_BYTES} bytes. "
                "Refusing to truncate: a truncated id names a different contract, "
                "and Dhan answers 200 for an unknown one."
            )
        body += _SLOT.pack(segment, encoded)

    # Pad to the full hundred. The server expects the fixed size.
    body += _SLOT.pack(0, b"") * (SUBSCRIBE_GROUP_SIZE - len(instruments))

    message_length = _HEADER.size + _COUNT.size + len(instruments) * _SLOT.size
    header = _HEADER.pack(request_code, message_length, client_id.encode(), _AUTH_PADDING)
    return header + body


def chunked(
    instruments: Sequence[tuple[int, str]],
    size: int = SUBSCRIBE_GROUP_SIZE,
) -> Iterator[list[tuple[int, str]]]:
    """Split a subscription into packets Dhan will accept."""
    for start in range(0, len(instruments), size):
        yield list(instruments[start : start + size])


def feed_url(token: str, client_id: str) -> str:
    """The v2 market-feed URL. Carries the token, so treat it as a secret."""
    return f"{MARKET_FEED_WSS}?version=2&token={token}&clientId={client_id}&authType=2"


def redacted_feed_url(token: str, client_id: str) -> str:
    """The same URL with the credential removed. This is the loggable one.

    Takes the token it will not print, so it is a drop-in for `feed_url`
    at a call site that is being made safe -- swapping the name is the
    whole change, with no argument list to get wrong.
    """
    _ = token  # deliberately unused; see the docstring
    return f"{MARKET_FEED_WSS}?version=2&token=<redacted>&clientId={client_id}&authType=2"
