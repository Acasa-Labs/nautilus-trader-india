"""The order-update socket: what a message means, without any socket.

WHY IT EXISTS. Without this stream a fill is learned at the next
reconciliation rather than when it happens, and the gap is exactly the window
in which a strategy acts on a position it does not yet know it holds.

NEVER OBSERVED. `wss://api-order-update.dhan.co` is a production host: the
sandbox token is refused there, and the live token was deliberately not used
while another process records a live feed on that account. So everything here
is built to Dhan's published message shape, and written to survive being
wrong about it -- an unknown status raises rather than defaulting, and the two
casings Dhan's own page disagrees with itself about are both accepted.

THE SOCKET SPEAKS A DIFFERENT DIALECT FROM REST. Same facts, other words:

    REST                     socket
    transactionType BUY/SELL TxnType    B / S
    productType     INTRADAY Product    C / I / M / F / V / B
    orderType       LIMIT    OrderType  LMT / MKT / SL / SLM
    orderStatus     PENDING  Status     "Pending" -- or "PENDING"; see below

Dhan's page carries both casings for `Status`: its sample message says
`"Cancelled"` while the parameter table beneath lists the enum in upper case,
which is also what the REST order book returns. Keyed on one casing a reader
sees no status at all from the other, so this matches case-insensitively.

IT DOES NOT CARRY A TRADE ID. `TradedQty` and `AvgTradedPrice` say how much
filled and at what average, but there is no per-fill identity, so this module
deliberately does not build a `FillReport`. A fill on the socket is a signal
to go and ask `GET /v2/trades/{orderId}`, which does carry `exchangeTradeId`.
Inventing an id here would put a fabricated trade in a reconciliation.

THE AUTH FRAME IS A CREDENTIAL. It carries the access token, so it must never
be logged whole -- `redacted_auth_message` is what may be.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from nautilus_trader.model.enums import OrderSide, OrderStatus
from nautilus_trader.model.identifiers import ClientOrderId, VenueOrderId

from nautilus_india.dhan.constants import SEGMENT_CODES
from nautilus_india.dhan.orders import ORDER_STATUS, client_order_id_for, ist_to_ns

# Dhan's own code for "send me order updates".
LOGIN_MESSAGE_CODE = 42

# The envelope. Anything else on the wire is not an order and must not become
# an event -- reading a heartbeat as one invents a change that did not happen.
ORDER_ALERT = "order_alert"

# Case-insensitive, because Dhan's page uses both. Built from the REST map so
# the two readers cannot drift apart.
_STATUS = {name.upper(): status for name, status in ORDER_STATUS.items()}

_SIDE = {"B": OrderSide.BUY, "S": OrderSide.SELL}

# `Remarks` is how the stream flags a bracket leg. The same key carries "NR"
# on an ordinary order.
_SUPER_ORDER_REMARK = "super order"

# (Exchange, Segment) as the socket spells them -> the REST segment name, from
# which `SEGMENT_CODES` gives the feed's numeric code. NSE commodity is absent
# for the same reason it is unroutable everywhere else: Dhan publishes no code.
_REST_SEGMENT_NAME = {
    ("NSE", "E"): "NSE_EQ", ("NSE", "D"): "NSE_FNO", ("NSE", "C"): "NSE_CURRENCY",
    ("NSE", "I"): "IDX_I", ("BSE", "E"): "BSE_EQ", ("BSE", "D"): "BSE_FNO",
    ("BSE", "C"): "BSE_CURRENCY", ("BSE", "I"): "IDX_I", ("MCX", "M"): "MCX_COMM",
}


def auth_message(client_id: str, token: str) -> dict:
    """The frame that must be sent before anything arrives."""
    return {
        "LoginReq": {
            "MsgCode": LOGIN_MESSAGE_CODE,
            "ClientId": client_id,
            "Token": token,
        },
        "UserType": "SELF",
    }


def redacted_auth_message(client_id: str, token: str) -> str:
    """What may be logged. The token is deliberately absent."""
    return (
        f"LoginReq(MsgCode={LOGIN_MESSAGE_CODE}, ClientId={client_id!r}, "
        "Token=<redacted>) UserType='SELF'"
    )


@dataclass(frozen=True, slots=True)
class OrderUpdate:
    """One order-alert message, in this package's own vocabulary."""

    venue_order_id: VenueOrderId
    order_status: OrderStatus
    order_side: OrderSide
    security_id: str
    segment_code: int | None
    quantity: int
    traded_quantity: int
    remaining_quantity: int
    average_price: Decimal | None
    price: Decimal | None
    correlation_id: str
    reason: str
    ts_event: int
    leg_number: int | None
    is_super_order_leg: bool

    def client_order_id_for(
        self, candidates: Iterable[ClientOrderId]
    ) -> ClientOrderId | None:
        """Which of our orders this is about, or None.

        The socket echoes whatever went out, which for a long ClientOrderId is
        the DERIVED id -- so this is the same recomputation the REST reports
        do, never a cast. None means the order is not ours.
        """
        return client_order_id_for(self.correlation_id, candidates)


def _status_of(raw: str) -> OrderStatus:
    try:
        return _STATUS[str(raw).upper()]
    except KeyError:
        raise ValueError(
            f"the order-update socket reported status {raw!r}, which this "
            f"adapter does not map. Known: {sorted(_STATUS)}. Refusing to "
            "default: a status read as accepted when it means rejected leaves "
            "the engine waiting for a fill that is never coming."
        ) from None


def _decimal_or_none(value: Any) -> Decimal | None:
    """Dhan sends 0 for a price no order had. Rendered literally that is a
    fill at zero."""
    if not value:
        return None
    return Decimal(str(value))


def _segment_code(data: dict) -> int | None:
    """The feed's numeric segment, from the socket's own two fields.

    There is no `exchangeSegment` string here -- the message names an exchange
    and a segment letter separately, which is the scrip master's spelling
    rather than the REST API's.
    """
    # `NSE` + `E` is the REST name `NSE_EQ`; the two do not concatenate into
    # it, so the pair is mapped explicitly. These are the same nine
    # combinations `providers._SEGMENT_CODE` enumerates from the live master.
    rest_name = _REST_SEGMENT_NAME.get(
        (str(data.get("Exchange", "")), str(data.get("Segment", "")))
    )
    if rest_name is None:
        return None
    return SEGMENT_CODES.get(rest_name)


def parse(message: dict) -> OrderUpdate | None:
    """One socket frame as an `OrderUpdate`, or None if it is not one.

    None rather than an exception for a non-order frame: the stream carries
    other traffic, and a heartbeat is not a failure.
    """
    if not isinstance(message, dict) or message.get("Type") != ORDER_ALERT:
        return None
    data = message.get("Data")
    if not isinstance(data, dict):
        return None

    remarks = str(data.get("Remarks") or "")
    return OrderUpdate(
        # `OrderNo` is Dhan's id and what every REST endpoint addresses an
        # order by. `ExchOrderNo` is the exchange's own and matches nothing.
        venue_order_id=VenueOrderId(str(data["OrderNo"])),
        order_status=_status_of(data.get("Status", "")),
        order_side=_SIDE.get(str(data.get("TxnType", "")).upper(), OrderSide.NO_ORDER_SIDE),
        security_id=str(data.get("SecurityId") or ""),
        segment_code=_segment_code(data),
        quantity=int(data.get("Quantity") or 0),
        traded_quantity=int(data.get("TradedQty") or 0),
        remaining_quantity=int(data.get("RemainingQuantity") or 0),
        average_price=_decimal_or_none(data.get("AvgTradedPrice")),
        price=_decimal_or_none(data.get("Price")),
        correlation_id=str(data.get("CorrelationId") or ""),
        # "CONFIRMED" on a healthy order -- the same field, and the same trap,
        # as `omsErrorDescription` on the REST order book.
        reason=str(data.get("ReasonDescription") or ""),
        ts_event=ist_to_ns(data.get("ExchOrderTime") or data.get("LastUpdatedTime")),
        leg_number=int(data["LegNo"]) if data.get("LegNo") is not None else None,
        is_super_order_leg=_SUPER_ORDER_REMARK in remarks.lower(),
    )


__all__ = [
    "LOGIN_MESSAGE_CODE",
    "ORDER_ALERT",
    "OrderUpdate",
    "auth_message",
    "parse",
    "redacted_auth_message",
]
