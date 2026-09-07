"""The binary feed, built with struct.pack from the documented layouts.

Frames are built rather than captured, so these pin our reading of the
DOCUMENTED wire (https://dhanhq.co/docs/v2/live-market-feed/) rather than our
reading of the SDK. Two tests then pin us against the SDK itself: one that
the numbers agree, and one that the SDK drops packets -- which is the reason
this module exists.
"""

import struct

import pytest
from nautilus_trader.model.objects import Price

from nautilus_india.dhan.binary import (
    CODE_DEPTH20_ASK,
    CODE_DEPTH20_BID,
    CODE_DISCONNECT,
    CODE_FULL,
    CODE_OI,
    CODE_QUOTE,
    CODE_TICKER,
    Decoder,
    decode_depth20_frame,
    decode_frame,
    to_price,
)

FULL = struct.Struct("<BHBIfHIfIIIIIIffff100s")
QUOTE = struct.Struct("<BHBIfHIfIIIffff")
TICKER = struct.Struct("<BHBIfI")
OI = struct.Struct("<BHBII")
DISCONNECT = struct.Struct("<BHBIH")
DEPTH_ENTRY = struct.Struct("<IIHHff")


def full_packet(security_id: int = 49081, ltp: float = 368.15) -> bytes:
    depth = b"".join(
        DEPTH_ENTRY.pack(100 + i, 75 + i, 2, 1, 368.10 - i, 368.20 + i) for i in range(5)
    )
    return FULL.pack(
        CODE_FULL, 162, 2, security_id, ltp, 50, 1_757_155_200, 365.0,
        10_000, 2_500, 3_000, 1_250_000, 1_265_000, 1_210_000,
        360.0, 355.0, 372.0, 352.0, depth,
    )


def ticker_packet(security_id: int = 13, ltp: float = 23_900.0) -> bytes:
    return TICKER.pack(CODE_TICKER, 16, 0, security_id, ltp, 1_757_155_200)


def test_decodes_a_full_packet():
    (packet,) = decode_frame(full_packet())
    assert packet["code"] == CODE_FULL
    assert packet["security_id"] == 49081
    assert packet["ltp"] == pytest.approx(368.15, abs=0.005)
    assert packet["ltq"] == 50
    assert packet["oi"] == 1_250_000
    assert len(packet["depth"]) == 5


def test_every_packet_in_a_multi_packet_frame_is_returned():
    """THE reason this module exists. The SDK returns after the first."""
    frame = full_packet(49081) + full_packet(49082) + ticker_packet(13)
    packets = decode_frame(frame)
    assert [p["security_id"] for p in packets] == [49081, 49082, 13]


def test_the_decoder_counts_what_it_saw():
    """The counters are the measurement of what a naive reader is losing."""
    decoder = Decoder()
    decoder.frame(full_packet(1) + full_packet(2))
    decoder.frame(ticker_packet(3))
    assert decoder.frames == 2
    assert decoder.packets == 3
    assert decoder.multi_packet_frames == 1


def test_an_unknown_code_is_skipped_by_its_declared_length_and_counted():
    """A known code advances by its KNOWN size; only an unknown one is
    skipped by what the header claims. Guessing a stride would silently
    resynchronise onto garbage and record it as market data."""
    unknown = struct.pack("<BHBI", 99, 16, 2, 777) + b"\x00" * 8
    decoder = Decoder()
    packets = decoder.frame(unknown + ticker_packet(13))
    assert [p["security_id"] for p in packets] == [13]
    assert decoder.unknown_codes[99] == 1


def test_a_truncated_tail_stops_the_loop_rather_than_unpacking_garbage():
    decoder = Decoder()
    packets = decoder.frame(ticker_packet(13) + full_packet(1)[:20])
    assert [p["security_id"] for p in packets] == [13]
    assert decoder.truncated == 1


def test_a_disconnect_packet_carries_its_reason_code():
    (packet,) = decode_frame(DISCONNECT.pack(CODE_DISCONNECT, 13, 0, 0, 805))
    assert packet["disconnect_code"] == 805


def test_an_oi_packet_decodes():
    (packet,) = decode_frame(OI.pack(CODE_OI, 12, 2, 49081, 1_250_000))
    assert packet["oi"] == 1_250_000


def test_a_quote_packet_decodes():
    raw = QUOTE.pack(CODE_QUOTE, 50, 2, 49081, 368.15, 50, 1_757_155_200,
                     365.0, 10_000, 2_500, 3_000, 360.0, 355.0, 372.0, 352.0)
    (packet,) = decode_frame(raw)
    assert packet["code"] == CODE_QUOTE
    assert packet["volume"] == 10_000
    assert packet["day_high"] == pytest.approx(372.0, abs=0.005)


def test_the_20_depth_socket_uses_a_different_header():
    """12 bytes, message length FIRST, then code, segment, id, sequence --
    against the market feed's 8-byte header. Reusing one for the other
    resynchronises onto garbage."""
    entries = b"".join(struct.pack("<dII", 368.10 - i * 0.05, 100 + i, 2) for i in range(20))
    bid = struct.pack("<hBBiI", 332, CODE_DEPTH20_BID, 2, 49081, 7) + entries
    ask = struct.pack("<hBBiI", 332, CODE_DEPTH20_ASK, 2, 49081, 8) + entries
    packets = decode_depth20_frame(bid + ask)
    assert [p["side"] for p in packets] == ["bid", "ask"]
    assert len(packets[0]["levels"]) == 20


def test_the_depth_sequence_number_is_kept_not_discarded():
    """The docs label it "to be ignored". For a live reader it is noise; for
    anyone reconstructing a book it is the exchange's own ordering, and a gap
    in it distinguishes "the book did not change" from "we lost a packet" --
    which our own arrival counter cannot do."""
    entries = b"".join(struct.pack("<dII", 100.0, 1, 1) for _ in range(20))
    bid = struct.pack("<hBBiI", 332, CODE_DEPTH20_BID, 2, 49081, 4242) + entries
    (packet,) = decode_depth20_frame(bid)
    assert packet["msg_seq"] == 4242


def test_bid_and_ask_are_not_paired_here():
    """The SDK pairs them by holding one of each and emitting when the ids
    match, which discards a bid whenever two bids arrive in a row. Pairing is
    the reader's question anyway -- it has both arrival stamps."""
    entries = b"".join(struct.pack("<dII", 100.0, 1, 1) for _ in range(20))
    two_bids = b"".join(
        struct.pack("<hBBiI", 332, CODE_DEPTH20_BID, 2, 49081, n) + entries for n in (1, 2)
    )
    assert [p["msg_seq"] for p in decode_depth20_frame(two_bids)] == [1, 2]


# -- the float32 boundary ------------------------------------------------


def test_the_wire_is_float32_so_a_price_must_be_rounded_not_stringified():
    """Dhan puts prices on the wire as IEEE float32. 368.15 arrives as
    368.1499938964844. `Decimal(str(raw))` would carry that noise into the
    order book; rounding to the instrument's precision recovers the price the
    exchange meant."""
    raw = struct.unpack("<f", struct.pack("<f", 368.15))[0]
    assert raw != 368.15  # the wire already lost it
    assert to_price(raw, 2) == Price.from_str("368.15")


@pytest.mark.parametrize("value", ["368.15", "24550.05", "87.55", "19845.65", "0.05"])
def test_rounding_recovers_every_realistic_price(value):
    raw = struct.unpack("<f", struct.pack("<f", float(value)))[0]
    assert to_price(raw, 2) == Price.from_str(value)


def test_to_price_never_routes_through_price_of_a_double():
    """`Price(value, precision)` takes a C double. Going through it would
    reintroduce the float path the package forbids everywhere else."""
    import inspect

    from nautilus_india.dhan import binary

    assert "from_str" in inspect.getsource(binary.to_price)


# -- pinned against the SDK ----------------------------------------------


def test_agrees_with_the_sdk_on_a_single_packet_frame():
    """The SDK's LAYOUTS are correct; it is the rendering that loses
    information. Undo the rendering and the numbers must match."""
    from dhanhq.marketfeed import MarketFeed

    sdk = MarketFeed.__new__(MarketFeed)
    reference = sdk.process_data(full_packet())
    (ours,) = decode_frame(full_packet())

    assert float(reference["LTP"]) == pytest.approx(ours["ltp"], abs=0.005)
    assert reference["LTQ"] == ours["ltq"]
    assert reference["volume"] == ours["volume"]
    assert reference["OI"] == ours["oi"]
    assert float(reference["open"]) == pytest.approx(ours["day_open"], abs=0.005)


def test_the_sdk_drops_all_but_the_first_packet():
    """The defect, pinned. If a future SDK fixes this the test fails and we
    can reconsider owning the decoder -- which is the point of pinning it."""
    from dhanhq.marketfeed import MarketFeed

    sdk = MarketFeed.__new__(MarketFeed)
    frame = full_packet(49081) + full_packet(49082)
    reference = sdk.process_data(frame)
    assert isinstance(reference, dict)
    assert reference["security_id"] == 49081
    assert len(decode_frame(frame)) == 2
