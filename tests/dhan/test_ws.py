"""Dhan's subscription protocol is BINARY, not JSON."""

import struct

import pytest

from nautilus_india.dhan.constants import REQUEST_FULL, SEGMENT_NSE_FNO, SUBSCRIBE_GROUP_SIZE
from nautilus_india.dhan.ws import chunked, feed_url, redacted_feed_url, subscription_packet


def test_the_packet_is_binary_and_carries_an_83_byte_header():
    """header = code(1) + length(2) + client_id(30) + auth(50) = 83."""
    packet = subscription_packet("CLIENT1", [(SEGMENT_NSE_FNO, "49081")], REQUEST_FULL)
    code, length, client_id, _auth = struct.unpack("<bH30s50s", packet[:83])
    assert code == REQUEST_FULL
    assert client_id.rstrip(b"\x00").decode() == "CLIENT1"
    assert length == 83 + 4 + 1 * 21


def test_the_instrument_count_precedes_the_instruments():
    packet = subscription_packet(
        "C1", [(SEGMENT_NSE_FNO, "49081"), (SEGMENT_NSE_FNO, "49082")], REQUEST_FULL
    )
    (count,) = struct.unpack_from("<I", packet, 83)
    assert count == 2


def test_each_instrument_is_a_segment_byte_and_a_20_byte_id():
    packet = subscription_packet("C1", [(SEGMENT_NSE_FNO, "49081")], REQUEST_FULL)
    segment, security_id = struct.unpack_from("<B20s", packet, 87)
    assert segment == SEGMENT_NSE_FNO
    assert security_id.rstrip(b"\x00").decode() == "49081"


def test_the_packet_is_padded_to_a_hundred_slots():
    """Dhan's own client pads regardless of how many are sent, so the server
    expects the fixed size. A short packet is silently ignored, which reads
    as 'subscribed but no data'."""
    packet = subscription_packet("C1", [(SEGMENT_NSE_FNO, "49081")], REQUEST_FULL)
    assert len(packet) == 83 + 4 + SUBSCRIBE_GROUP_SIZE * 21


def test_more_than_a_hundred_instruments_is_refused_rather_than_truncated():
    """Truncating would subscribe to a subset and report success -- the
    caller would see a feed that is quietly missing instruments."""
    too_many = [(SEGMENT_NSE_FNO, str(i)) for i in range(SUBSCRIBE_GROUP_SIZE + 1)]
    with pytest.raises(ValueError, match="100"):
        subscription_packet("C1", too_many, REQUEST_FULL)


def test_chunked_splits_a_large_subscription_into_legal_packets():
    instruments = [(SEGMENT_NSE_FNO, str(i)) for i in range(250)]
    chunks = list(chunked(instruments))
    assert [len(c) for c in chunks] == [100, 100, 50]
    assert sum(len(c) for c in chunks) == 250


def test_every_chunk_is_a_legal_packet():
    """The split is only useful if each piece is actually acceptable."""
    instruments = [(SEGMENT_NSE_FNO, str(i)) for i in range(250)]
    for chunk in chunked(instruments):
        packet = subscription_packet("C1", chunk, REQUEST_FULL)
        assert len(packet) == 83 + 4 + SUBSCRIBE_GROUP_SIZE * 21


def test_a_security_id_too_long_for_the_field_is_refused():
    """A 20-byte field silently truncates a longer id, and a truncated id is
    a different contract -- which Dhan answers 200 for."""
    with pytest.raises(ValueError, match="20"):
        subscription_packet("C1", [(SEGMENT_NSE_FNO, "x" * 21)], REQUEST_FULL)


def test_an_empty_subscription_is_still_a_legal_packet():
    """Nothing to subscribe to is not an error; it is a no-op the caller may
    reasonably produce from an empty filter."""
    packet = subscription_packet("C1", [], REQUEST_FULL)
    (count,) = struct.unpack_from("<I", packet, 83)
    assert count == 0
    assert len(packet) == 83 + 4 + SUBSCRIBE_GROUP_SIZE * 21


# A realistic JWT-shaped token. Deliberately not something like "tok", which
# is a substring of "token=" and would make the redaction assertion below
# impossible to fail -- and therefore worthless.
SECRET = "eyJhbGciOiJIUzI1NiJ9.SUPERSECRETPAYLOAD.signature"


def test_the_feed_url_carries_the_token_and_client_in_the_query():
    url = feed_url(SECRET, "C1")
    assert url.startswith("wss://")
    assert "version=2" in url
    assert f"token={SECRET}" in url
    assert "clientId=C1" in url


def test_the_feed_url_is_never_logged_whole():
    """The token is in the query string, so the URL is itself a credential."""
    redacted = redacted_feed_url(SECRET, "C1")
    assert SECRET not in redacted
    assert "SUPERSECRETPAYLOAD" not in redacted
    assert "C1" in redacted
    assert redacted.startswith("wss://")
    assert "<redacted>" in redacted
