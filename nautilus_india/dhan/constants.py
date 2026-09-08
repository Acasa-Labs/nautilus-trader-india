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

# The four order types Dhan documents. SL-L and SL-M additionally need a
# `triggerPrice`; see `orders.py`.
ORDER_TYPE_LIMIT = "LIMIT"
ORDER_TYPE_MARKET = "MARKET"
ORDER_TYPE_STOP_LOSS = "STOP_LOSS"
ORDER_TYPE_STOP_LOSS_MARKET = "STOP_LOSS_MARKET"

# The six product types. CNC and MTF are refused by the F&O segment; INTRADAY
# and MARGIN are the two that work there, and INTRADAY is this package's
# default because it is the only one whose margin `core.margin` models.
PRODUCT_CNC = "CNC"
PRODUCT_INTRADAY = "INTRADAY"
PRODUCT_MARGIN = "MARGIN"
PRODUCT_MTF = "MTF"
PRODUCT_CO = "CO"
PRODUCT_BO = "BO"
PRODUCT_TYPES = frozenset(
    {PRODUCT_CNC, PRODUCT_INTRADAY, PRODUCT_MARGIN, PRODUCT_MTF, PRODUCT_CO, PRODUCT_BO}
)

VALIDITY_DAY = "DAY"
VALIDITY_IOC = "IOC"

# When an after-market order is released. Conditionally required, and only
# once `afterMarketOrder` is true.
AMO_TIMES = frozenset({"PRE_OPEN", "OPEN", "OPEN_30", "OPEN_60"})

# Which leg of a bracket or cover order a modify addresses. Dhan has no other
# handle on a leg.
LEG_NAMES = frozenset({"ENTRY_LEG", "TARGET_LEG", "STOP_LOSS_LEG"})

# Splits a quantity over the F&O freeze limit into several orders, which the
# exchange would otherwise reject outright. Same body as a placement.
ORDER_SLICING_PATH = "/v2/orders/slicing"

# Looks an order up by the caller's OWN id. Dhan documents it "in case the
# user has missed order id due to unforeseen reason" -- which is the only way
# back to an order whose submission answer never arrived.
ORDERS_EXTERNAL_PATH = "/v2/orders/external"

# `correlationId` is the ONLY field that round-trips a caller's own id: it
# comes back on GET /v2/orders and on the order-update socket, so it is where
# a ClientOrderId has to live.
#
# TWENTY-FIVE, MEASURED -- the docs say 30. Binary-searched in Dhan's sandbox
# on 2026-09-08: 25 characters is accepted and 26 is refused, with DH-905 and
# no indication which field was wrong. The docs' own charset note is
# "[^a-zA-Z0-9 _-]", whose leading caret NEGATES the class, so it cannot be
# read literally either; spaces, hyphens and underscores are all accepted.
#
# The consequence is not cosmetic. A DEFAULT Nautilus ClientOrderId
# (`O-19700101-000000-001-000-1`) is 27 characters, so it does not fit and
# never will -- the format's fixed parts alone exceed the limit.
CORRELATION_ID_MAX_LEN = 25

# -- super orders ------------------------------------------------------------
# Entry, target and stop loss as ONE request, sharing one orderId. Legs are
# addressed by the pair (orderId, legName) and by nothing else.
SUPER_ORDERS_PATH = "/v2/super/orders"

LEG_ENTRY = "ENTRY_LEG"
LEG_TARGET = "TARGET_LEG"
LEG_STOP_LOSS = "STOP_LOSS_LEG"

# A super order takes LIMIT or MARKET only -- no stop entry -- and four of the
# six product types. CO and BO are absent: a super order IS the bracket.
SUPER_ORDER_TYPES = frozenset({ORDER_TYPE_LIMIT, ORDER_TYPE_MARKET})
SUPER_PRODUCT_TYPES = frozenset(
    {PRODUCT_CNC, PRODUCT_INTRADAY, PRODUCT_MARGIN, PRODUCT_MTF}
)

# -- forever orders ----------------------------------------------------------
# Dhan's Good-Till-Triggered order, and the ONLY way to rest anything past the
# close: /v2/orders takes DAY and IOC and nothing else.
#
# The list path is `/v2/forever/orders`, NOT the `/v2/forever/all` that Dhan's
# own cURL sample shows -- probed 2026-09-08, that one answers 404. The
# endpoint table on the same page and dhanhq 2.2.0 both agree with the value
# below. See tests/dhan/fixtures/envelope/captured/gateway_not_found.json.
FOREVER_ORDERS_PATH = "/v2/forever/orders"

ORDER_FLAG_SINGLE = "SINGLE"
ORDER_FLAG_OCO = "OCO"
ORDER_FLAGS = frozenset({ORDER_FLAG_SINGLE, ORDER_FLAG_OCO})

# Forever orders rest in the demat account, so only the delivery product types
# apply. INTRADAY cannot outlive the day it is named after.
FOREVER_PRODUCT_TYPES = frozenset({PRODUCT_CNC, PRODUCT_MTF})
FOREVER_ORDER_TYPES = frozenset({ORDER_TYPE_LIMIT, ORDER_TYPE_MARKET})

# A forever order's `legName` means something DIFFERENT from a super order's:
# TARGET_LEG is a SINGLE order and an OCO's first leg, STOP_LOSS_LEG is an
# OCO's second. There is no ENTRY_LEG to send.
FOREVER_LEG_NAMES = frozenset({LEG_TARGET, LEG_STOP_LOSS})
