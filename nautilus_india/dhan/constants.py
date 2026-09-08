"""Dhan's own vocabulary and endpoints, spelled once.

Sources: https://dhanhq.co/docs/v2/ for the REST paths and the market feed,
https://dhanhq.co/docs/v2/full-market-depth/ for the 20-level socket, and the
scrip master URL published on the same site. Segment and request codes were
cross-checked against the `dhanhq` SDK's own constants -- reading them is
fine; importing it at runtime is not.
"""

from __future__ import annotations

from nautilus_india.core.enums import Exchange

BASE_URL = "https://api.dhan.co"

ORDERS_PATH = "/v2/orders"
POSITIONS_PATH = "/v2/positions"
HOLDINGS_PATH = "/v2/holdings"
FUNDS_PATH = "/v2/fundlimit"
TRADES_PATH = "/v2/trades"

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"

MARKET_FEED_WSS = "wss://api-feed.dhan.co"
DEPTH_FEED_WSS = "wss://depth-api-feed.dhan.co/twentydepth"

# Exchange-segment codes, as they appear in the binary feed header.
SEGMENT_IDX = 0
SEGMENT_NSE_EQ = 1
SEGMENT_NSE_FNO = 2
SEGMENT_NSE_CURR = 3
SEGMENT_BSE_EQ = 4
SEGMENT_MCX = 5
SEGMENT_BSE_CURR = 7
SEGMENT_BSE_FNO = 8

# Dhan's REST vocabulary for the same segments.
SEGMENT_NAMES = {
    SEGMENT_IDX: "IDX_I",
    SEGMENT_NSE_EQ: "NSE_EQ",
    SEGMENT_NSE_FNO: "NSE_FNO",
    SEGMENT_NSE_CURR: "NSE_CURRENCY",
    SEGMENT_BSE_EQ: "BSE_EQ",
    SEGMENT_MCX: "MCX_COMM",
    SEGMENT_BSE_CURR: "BSE_CURRENCY",
    SEGMENT_BSE_FNO: "BSE_FNO",
}

# Which Nautilus venue each segment belongs to.
SEGMENT_EXCHANGE = {
    SEGMENT_IDX: Exchange.NSE,
    SEGMENT_NSE_EQ: Exchange.NSE,
    SEGMENT_NSE_FNO: Exchange.NSE,
    SEGMENT_NSE_CURR: Exchange.NSE,
    SEGMENT_BSE_EQ: Exchange.BSE,
    SEGMENT_BSE_CURR: Exchange.BSE,
    SEGMENT_BSE_FNO: Exchange.BSE,
    SEGMENT_MCX: Exchange.MCX,
}

# Subscription request codes for the market feed.
REQUEST_TICKER = 15
REQUEST_QUOTE = 17
REQUEST_DEPTH = 19
REQUEST_FULL = 21
REQUEST_DISCONNECT = 12

# The feed accepts at most this many instruments per subscription packet, and
# the packet is padded to it regardless of how many are sent.
SUBSCRIBE_GROUP_SIZE = 100

# -- the order path ----------------------------------------------------------
# Dhan's REST vocabulary. Spelled once, here, so no caller has to remember
# which of these are Dhan's words and which are ours.

# Name -> feed segment code. The REST API answers with the NAME ("NSE_FNO")
# and the provider's reverse map is keyed on the CODE, so a report cannot
# resolve an instrument without this direction too.
SEGMENT_CODES = {name: code for code, name in SEGMENT_NAMES.items()}

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"

# LIMIT is the only order type this adapter sends. See `orders.py`: Dhan
# converts an API MARKET order into a limit order with market-protection
# pricing, so a "market" order is a limit order at a price the caller did not
# choose.
ORDER_TYPE_LIMIT = "LIMIT"

# CNC and MTF are refused by the F&O segment; INTRADAY and MARGIN are the two
# that work there. INTRADAY is the default because it is the only one whose
# margin this package models.
PRODUCT_INTRADAY = "INTRADAY"
PRODUCT_MARGIN = "MARGIN"
PRODUCT_CNC = "CNC"

VALIDITY_DAY = "DAY"
VALIDITY_IOC = "IOC"

# `correlationId` is the ONLY field that round-trips a caller's own id: it
# comes back on GET /v2/orders and on the order-update socket, so it is where
# the ClientOrderId has to live. Dhan caps it at 30 characters. A default
# Nautilus id (`O-19700101-000000-001-000-1`) is 27 and fits with three to
# spare; a UUID one is 36 and does not.
CORRELATION_ID_MAX_LEN = 30
