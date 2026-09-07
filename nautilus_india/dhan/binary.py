"""Dhan's binary market feed, decoded here rather than by the SDK.

WHY NOT THE SDK. `MarketFeed.process_data` reads `data[0:1]`, dispatches on
it, and each `process_*` unpacks a fixed prefix and returns. There is no
offset loop, so every packet after the FIRST in a websocket frame is
discarded. `FullDepth.process_data` DOES chain through `remaining_data`, so
the omission is in `MarketFeed` alone -- and the response header carries
`message_length` for exactly this purpose.

The layouts are not the problem. Every one below was checked field by field
against https://dhanhq.co/docs/v2/live-market-feed/ and agrees with the SDK
to the byte. What the SDK loses is in the RENDERING:

  * `utc_time` is `datetime.utcfromtimestamp(e).strftime('%H:%M:%S')`, so LTT
    arrives with no date, no timezone and no sub-second part. Nothing can be
    ordered by that. The raw epoch integer is kept here instead.
  * every price is `"{:.2f}".format(...)`, a string.
  * `FullDepth.combine_and_format_depth` renders depth as DISPLAY STRINGS.

MESSAGE LENGTH IS NOT THE STRIDE. The docs describe bytes 2-3 as "Message
Length of the entire payload packet" without saying whether the 8-byte header
is inside that count. So a KNOWN code advances by its KNOWN size and any
disagreement with the header is counted rather than obeyed; only an UNKNOWN
code is skipped by what the header claims. Guessing a stride would silently
resynchronise onto garbage and record it as market data.

PRICES ARE IEEE float32 ON THE WIRE. See `to_price` -- this is the one place
in the package where a float is legitimate, because the wire is lossy before
we ever see it.
"""

from __future__ import annotations

import struct
from collections import Counter

from nautilus_trader.model.objects import Price

# Market-feed response header: code(1) + message_length(int16) +
# exchange_segment(1) + security_id(int32) = 8 bytes.
HEADER = struct.Struct("<BHBI")

CODE_TICKER = 2
CODE_DEPTH5 = 3
CODE_QUOTE = 4
CODE_OI = 5
CODE_PREV_CLOSE = 6
CODE_STATUS = 7
CODE_FULL = 8
CODE_DISCONNECT = 50

_TICKER = struct.Struct("<BHBIfI")
_PREV_CLOSE = struct.Struct("<BHBIfI")
_OI = struct.Struct("<BHBII")
_QUOTE = struct.Struct("<BHBIfHIfIIIffff")
_FULL = struct.Struct("<BHBIfHIfIIIIIIffff100s")
_DISCONNECT = struct.Struct("<BHBIH")
_DEPTH_ENTRY = struct.Struct("<IIHHff")

PACKET_SIZE: dict[int, int] = {
    CODE_TICKER: _TICKER.size,
    CODE_QUOTE: _QUOTE.size,
    CODE_OI: _OI.size,
    CODE_PREV_CLOSE: _PREV_CLOSE.size,
    CODE_FULL: _FULL.size,
    CODE_DISCONNECT: _DISCONNECT.size,
}

# The 20-level depth socket speaks a DIFFERENT header, verified against
# https://dhanhq.co/docs/v2/full-market-depth/: 12 bytes, message length
# FIRST, then code, segment, security id, and a uint32 the docs label
# "Message Sequence (to be ignored)".
#
# WE DO NOT IGNORE IT. For a live reader a sequence number is noise; for
# anyone reconstructing a book it is the exchange's own ordering, and a gap in
# it distinguishes "the book did not change" from "we lost a packet" -- a
# distinction an arrival counter cannot make, because it only counts what
# arrived.
DEPTH20_HEADER = struct.Struct("<hBBiI")
DEPTH20_ENTRY = struct.Struct("<dII")
DEPTH20_LEVELS = 20
DEPTH20_SIZE = DEPTH20_HEADER.size + DEPTH20_LEVELS * DEPTH20_ENTRY.size

# The docs contradict themselves here. The prose reads "the bid (sell) and ask
# (buy) data packets", while the table directly under it reads "41 for Bid
# Data (Buy)" and "51 for Ask Data (Sell)". The table agrees with the SDK and
# with the ordinary meaning of the words, so the prose is a typo.
CODE_DEPTH20_BID = 41
CODE_DEPTH20_ASK = 51


def to_price(raw: float, precision: int) -> Price:
    """A wire float32 as an exact `Price`.

    THE ONE PLACE A FLOAT IS ALLOWED, and only because the wire is already
    lossy: Dhan sends prices as IEEE float32, so 368.15 arrives as
    368.1499938964844 before this package ever sees it. `Decimal(str(raw))`
    would carry that noise into the book and into every comparison against a
    strike. Rounding to the instrument's own precision recovers the price the
    exchange meant, and `from_str` keeps it exact from there -- `Price`'s
    positional constructor takes a C double and would put it straight back.
    """
    return Price.from_str(f"{raw:.{precision}f}")


class Decoder:
    """A stateful decoder that counts what it sees.

    The counters are not diagnostics for their own sake. `multi_packet_frames`
    measures what a naive reader is losing, and `length_mismatches` settles
    whether `message_length` includes the header.
    """

    def __init__(self) -> None:
        self.frames = 0
        self.packets = 0
        self.multi_packet_frames = 0
        self.length_mismatches = 0
        self.truncated = 0
        self.unknown_codes: Counter[int] = Counter()

    def frame(self, data: bytes) -> list[dict]:
        self.frames += 1
        packets: list[dict] = []
        offset, size = 0, len(data)

        while offset + HEADER.size <= size:
            code, message_length, segment, security_id = HEADER.unpack_from(data, offset)
            known = PACKET_SIZE.get(code)

            if known is None:
                self.unknown_codes[code] += 1
                if message_length <= HEADER.size or offset + message_length > size:
                    self.truncated += 1
                    break
                offset += message_length
                continue

            if message_length not in (known, known - HEADER.size):
                self.length_mismatches += 1
            if offset + known > size:
                self.truncated += 1
                break

            packets.append(_packet(code, data, offset, segment, security_id))
            offset += known

        self.packets += len(packets)
        if len(packets) > 1:
            self.multi_packet_frames += 1
        return packets


def decode_frame(data: bytes) -> list[dict]:
    """One frame, when nobody is counting."""
    return Decoder().frame(data)


def _packet(code: int, data: bytes, offset: int, segment: int, security_id: int) -> dict:
    out: dict = {"code": code, "segment": segment, "security_id": security_id}

    if code == CODE_TICKER:
        *_, ltp, ltt = _TICKER.unpack_from(data, offset)
        out["ltp"], out["ltt"] = ltp, ltt

    elif code == CODE_PREV_CLOSE:
        *_, prev_close, prev_oi = _PREV_CLOSE.unpack_from(data, offset)
        out["prev_close"], out["prev_oi"] = prev_close, prev_oi

    elif code == CODE_OI:
        *_, open_interest = _OI.unpack_from(data, offset)
        out["oi"] = open_interest

    elif code == CODE_QUOTE:
        (_, _, _, _, ltp, ltq, ltt, atp, volume, sell_qty, buy_qty,
         day_open, day_close, day_high, day_low) = _QUOTE.unpack_from(data, offset)
        out.update(ltp=ltp, ltq=ltq, ltt=ltt, atp=atp, volume=volume,
                   total_sell_qty=sell_qty, total_buy_qty=buy_qty,
                   day_open=day_open, day_close=day_close,
                   day_high=day_high, day_low=day_low)

    elif code == CODE_FULL:
        (_, _, _, _, ltp, ltq, ltt, atp, volume, sell_qty, buy_qty,
         open_interest, oi_high, oi_low, day_open, day_close, day_high,
         day_low, depth) = _FULL.unpack_from(data, offset)
        out.update(ltp=ltp, ltq=ltq, ltt=ltt, atp=atp, volume=volume,
                   total_sell_qty=sell_qty, total_buy_qty=buy_qty,
                   oi=open_interest, oi_day_high=oi_high, oi_day_low=oi_low,
                   day_open=day_open, day_close=day_close,
                   day_high=day_high, day_low=day_low,
                   depth=_depth5(depth))

    elif code == CODE_DISCONNECT:
        *_, reason = _DISCONNECT.unpack_from(data, offset)
        out["disconnect_code"] = reason

    return out


def _depth5(blob: bytes) -> list[dict]:
    return [
        {"bid_qty": bid_qty, "ask_qty": ask_qty, "bid_orders": bid_orders,
         "ask_orders": ask_orders, "bid_price": bid_price, "ask_price": ask_price}
        for bid_qty, ask_qty, bid_orders, ask_orders, bid_price, ask_price
        in _DEPTH_ENTRY.iter_unpack(blob)
    ]


def decode_depth20_frame(data: bytes) -> list[dict]:
    """The 20-level depth socket. Bid and ask arrive as SEPARATE packets.

    They are NOT paired here. The SDK pairs them by holding one of each and
    emitting when the ids match, which discards a bid whenever two bids arrive
    in a row -- and pairing is a question for the reader anyway, who has both
    sides' arrival stamps and can decide what "the book at time t" means. This
    returns what arrived.
    """
    packets: list[dict] = []
    offset, size = 0, len(data)

    while offset + DEPTH20_HEADER.size <= size:
        message_length, code, segment, security_id, msg_seq = DEPTH20_HEADER.unpack_from(
            data, offset
        )
        if code not in (CODE_DEPTH20_BID, CODE_DEPTH20_ASK):
            if message_length <= DEPTH20_HEADER.size or offset + message_length > size:
                break
            offset += message_length
            continue
        if offset + DEPTH20_SIZE > size:
            break
        body = data[offset + DEPTH20_HEADER.size : offset + DEPTH20_SIZE]
        packets.append({
            "code": code,
            "segment": segment,
            "security_id": security_id,
            "msg_seq": msg_seq,
            "side": "bid" if code == CODE_DEPTH20_BID else "ask",
            "levels": [
                {"price": price, "quantity": quantity, "orders": orders}
                for price, quantity, orders in DEPTH20_ENTRY.iter_unpack(body)
            ],
        })
        offset += DEPTH20_SIZE

    return packets
