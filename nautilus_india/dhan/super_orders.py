"""Super orders: entry, target and stop loss as one request.

WHAT MAKES ONE DIFFERENT FROM THREE ORDERS. Sent separately there is no OCO
between the legs, so a filled target leaves the stop working and the next move
opens a position nobody chose. A super order is the venue holding that
relationship for us.

ONE `orderId` COVERS ALL THREE LEGS. Every entry in `legDetails` carries the
ENTRY's id, so anything keying its own orders on `orderId` alone collapses
three into one and loses two. Dhan's own modify and cancel take the pair
`(orderId, legName)`; the reports built here give each leg the composite id
`"{orderId}:{legName}"` so a caller can tell them apart, and `cancel_path`
takes the pair.

A LEG CANCEL CANNOT BE UNDONE. Dhan: "if particular target or stop loss leg is
cancelled, then the same cannot be added again." Nothing in the API refuses a
second attempt, so the warning lives here where a caller will read it.

WHAT CAN STILL BE CHANGED SHRINKS OVER TIME. `ENTRY_LEG` modifies the whole
super order, but only while the entry is PENDING or PART_TRADED. Once it is
TRADED, only the target and stop legs move, and only their price and trailing
jump.

Built to https://dhanhq.co/docs/v2/super-order/, which specifies every request
and every response. Nothing here has been sent to Dhan: this account cannot
place an order without a whitelisted static IP.
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.reports import OrderStatusReport
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.model.identifiers import AccountId, ClientOrderId, VenueOrderId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price
from nautilus_trader.model.orders import Order

from nautilus_india.dhan.constants import (
    LEG_ENTRY,
    LEG_STOP_LOSS,
    LEG_TARGET,
    SIDE_BUY,
    SUPER_ORDER_TYPES,
    SUPER_ORDERS_PATH,
    SUPER_PRODUCT_TYPES,
)
from nautilus_india.dhan.orders import (
    ORDER_STATUS,
    Unsendable,
    _checked,
    _price_string,
    correlation_id,
    ist_to_ns,
    lots_for,
    order_type_for,
    segment_name,
    units_for,
)

LEG_CANCEL_IS_IRREVERSIBLE = (
    "Cancelling a TARGET_LEG or STOP_LOSS_LEG on its own is irreversible. In "
    "Dhan's own words: if particular target or stop loss leg is cancelled, "
    "then the same cannot be added again -- and the position is left holding "
    "one side of its bracket. Cancel ENTRY_LEG to cancel all three."
)

# `totalQuatity` is how Dhan's DOCUMENTATION spells the leg quantity. Whether
# the API spells it that way is UNVERIFIED and unverifiable here: this account
# has never placed a super order, so GET /v2/super/orders answers `[]`, and
# dhanhq 2.2.0 returns raw dicts and never names the field either.
#
# It is not safe to assume either way. `availabelBalance` on /v2/fundlimit is
# misspelled in the LIVE API -- captured, not inferred -- so a typo in the
# docs and a typo in the API are both things Dhan does. Reading only one
# spelling and being wrong yields a leg for no quantity, which reads as a
# bracket with nothing in it. So all three are tried, and nothing here depends
# on which is real.
LEG_QUANTITY_KEYS = ("totalQuatity", "totalQuantity", "remainingQuantity")

_LEG_NAMES = frozenset({LEG_ENTRY, LEG_TARGET, LEG_STOP_LOSS})


def _opposite(side: OrderSide) -> OrderSide:
    return OrderSide.SELL if side is OrderSide.BUY else OrderSide.BUY


def place_payload(
    entry: Order,
    target: Order,
    stop_loss: Order,
    instrument: Instrument,
    security_id: str,
    client_id: str,
    product_type: str,
    *,
    trailing_jump: str = "0",
) -> dict:
    """The body of POST /v2/super/orders.

    Every price Dhan marks required is present, `trailingJump` included -- an
    omitted or zero jump means "do not trail", and leaving the field out says
    the same thing with less clarity.
    """
    dhan_type = order_type_for(entry.order_type)
    if dhan_type not in SUPER_ORDER_TYPES:
        raise Unsendable(
            f"a super order's entry takes {sorted(SUPER_ORDER_TYPES)} and this "
            f"one is {dhan_type}. Dhan documents no stop entry for this "
            "endpoint, so the order would be something other than what was asked."
        )
    if target.side is not _opposite(entry.side) or stop_loss.side is not _opposite(entry.side):
        raise Unsendable(
            "a super order's target and stop must face the opposite side from "
            f"its entry ({entry.side!r}). On the same side they double the "
            "position instead of closing it."
        )

    target_price = target.price
    stop_price = stop_loss.trigger_price if stop_loss.has_trigger_price else stop_loss.price
    if entry.side is OrderSide.BUY and target_price <= stop_price:
        raise Unsendable(
            f"a long super order needs its target ({target_price}) above its "
            f"stop ({stop_price}). Inverted, one leg fires the moment the entry "
            "fills and closes a position that was just opened."
        )
    if entry.side is OrderSide.SELL and target_price >= stop_price:
        raise Unsendable(
            f"a short super order needs its target ({target_price}) below its "
            f"stop ({stop_price}). Inverted, one leg fires the moment the entry "
            "fills and closes a position that was just opened."
        )

    return {
        "dhanClientId": client_id,
        "correlationId": correlation_id(entry.client_order_id),
        "transactionType": SIDE_BUY if entry.side is OrderSide.BUY else "SELL",
        "exchangeSegment": segment_name(instrument),
        "productType": _checked(product_type, SUPER_PRODUCT_TYPES, "productType"),
        "orderType": dhan_type,
        "securityId": str(security_id),
        # One quantity covers the whole structure; the legs do not carry theirs.
        "quantity": str(units_for(instrument, entry.quantity)),
        "price": _price_string(entry.price if entry.has_price else None),
        "targetPrice": _price_string(target_price),
        "stopLossPrice": _price_string(stop_price),
        "trailingJump": str(trailing_jump),
    }


def modify_payload(
    order_id: str,
    client_id: str,
    leg_name: str,
    *,
    order_type: str | None = None,
    quantity_units: int | None = None,
    price: str | None = None,
    target_price: str | None = None,
    stop_loss_price: str | None = None,
    trailing_jump: str | None = None,
) -> dict:
    """The body of PUT /v2/super/orders/{order-id}, for ONE leg.

    Each leg sends only its own fields. Sending the entry's on a target modify
    would ask to change terms the venue has already fixed -- once the entry is
    TRADED they cannot move at all, and the request is refused rather than
    partially applied.
    """
    leg = _checked(leg_name, _LEG_NAMES, "legName")
    payload: dict = {"dhanClientId": client_id, "orderId": str(order_id), "legName": leg}

    if leg == LEG_ENTRY:
        # The entry leg modifies the WHOLE super order, so it states all of it.
        payload.update(
            orderType=order_type,
            quantity=str(quantity_units),
            price=str(price),
            targetPrice=target_price,
            stopLossPrice=stop_loss_price,
            trailingJump=trailing_jump,
        )
    elif leg == LEG_TARGET:
        payload["targetPrice"] = target_price
    else:
        payload["stopLossPrice"] = stop_loss_price
        # Omitted or zero CANCELS the trail, and no value means "leave it
        # alone" -- so the caller's intent is always spelled out.
        payload["trailingJump"] = trailing_jump if trailing_jump is not None else "0"
    return payload


def cancel_path(order_id: str, leg_name: str) -> str:
    """DELETE /v2/super/orders/{order-id}/{order-leg}.

    Cancelling ENTRY_LEG cancels all three. Cancelling either other leg is
    irreversible -- see `LEG_CANCEL_IS_IRREVERSIBLE`.
    """
    leg = _checked(leg_name, _LEG_NAMES, "legName")
    return f"{SUPER_ORDERS_PATH}/{order_id}/{leg}"


def leg_venue_order_id(order_id: str, leg_name: str) -> VenueOrderId:
    """A leg's own id, since Dhan gives all three the entry's.

    The entry keeps the bare id, because that is what Dhan answers a placement
    with and what a whole-structure cancel addresses.
    """
    if leg_name == LEG_ENTRY:
        return VenueOrderId(str(order_id))
    return VenueOrderId(f"{order_id}:{leg_name}")


def _leg_quantity(row: dict) -> object:
    for key in LEG_QUANTITY_KEYS:
        if row.get(key):
            return row[key]
    return 0


def _report(
    row: dict,
    instrument: Instrument,
    account_id: AccountId,
    ts_init: int,
    *,
    leg_name: str,
    side: OrderSide,
    quantity: object,
    price: object,
    parent: dict,
) -> OrderStatusReport:
    correlation = str(parent.get("correlationId") or "")
    status = str(row.get("orderStatus") or parent.get("orderStatus") or "")
    if status not in ORDER_STATUS:
        raise ValueError(
            f"Dhan reported super-order status {status!r} on leg {leg_name}, "
            f"which this adapter does not map. Known: {sorted(ORDER_STATUS)}."
        )
    return OrderStatusReport(
        account_id=account_id,
        instrument_id=instrument.id,
        venue_order_id=leg_venue_order_id(parent["orderId"], leg_name),
        # Only the entry round-trips the caller's id; Dhan attaches none to a
        # leg it created itself.
        client_order_id=(
            ClientOrderId(correlation) if correlation and leg_name == LEG_ENTRY else None
        ),
        order_side=side,
        order_type=(
            OrderType.MARKET if parent.get("orderType") == "MARKET" else OrderType.LIMIT
        ),
        time_in_force=TimeInForce.DAY,
        order_status=ORDER_STATUS[status],
        quantity=lots_for(instrument, quantity),
        filled_qty=lots_for(instrument, parent.get("filledQty") if leg_name == LEG_ENTRY else 0),
        price=Price.from_str(str(price)) if price else None,
        avg_px=(
            Decimal(str(parent["averageTradedPrice"]))
            if leg_name == LEG_ENTRY and parent.get("averageTradedPrice")
            else None
        ),
        cancel_reason=(
            str(parent["omsErrorDescription"]) if parent.get("omsErrorDescription") else None
        ),
        report_id=UUID4(),
        ts_accepted=ist_to_ns(parent.get("createTime")),
        ts_last=ist_to_ns(parent.get("updateTime")) or ist_to_ns(parent.get("createTime")),
        ts_init=ts_init,
    )


def reports_from(rows, instrument_for, account_id: AccountId, ts_init: int) -> list:
    """One report per LEG, not one per super order.

    A single report for the parent would hide the target and the stop, which
    are the orders actually resting at the venue and the ones a reconciliation
    needs to see.
    """
    found = []
    for row in rows or []:
        instrument = instrument_for(row)
        if instrument is None:
            continue
        entry_side = OrderSide.BUY if row.get("transactionType") == SIDE_BUY else OrderSide.SELL
        found.append(
            _report(
                row, instrument, account_id, ts_init, leg_name=LEG_ENTRY,
                side=entry_side, quantity=row.get("quantity"), price=row.get("price"),
                parent=row,
            )
        )
        for leg in row.get("legDetails") or []:
            leg_name = str(leg.get("legName") or "")
            if leg_name not in _LEG_NAMES:
                continue
            side = OrderSide.BUY if leg.get("transactionType") == SIDE_BUY else OrderSide.SELL
            found.append(
                _report(
                    leg, instrument, account_id, ts_init, leg_name=leg_name,
                    side=side,
                    # A resting leg reports zero remaining until the entry
                    # fills, so its size comes from the parent's quantity.
                    quantity=_leg_quantity(leg) or row.get("quantity"),
                    price=leg.get("price"), parent=row,
                )
            )
    return found
