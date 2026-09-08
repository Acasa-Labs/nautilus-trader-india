"""Forever orders: Dhan's Good-Till-Triggered, and the only thing that rests.

`/v2/orders` takes DAY and IOC and nothing else, so an order meant to outlive
the session cannot live there. This endpoint is where it lives, and that is
why `orders.validity_for` maps GTC to DAY rather than pretending otherwise --
a resting order is a different request to a different path, not a validity.

A TRIGGER IS REQUIRED. Good-Till-TRIGGERED: without one there is nothing for
the order to wait for, and Dhan marks `triggerPrice` required for that reason.

`orderFlag` DECIDES HOW MANY ORDERS THIS IS. SINGLE rests one. OCO rests two,
where either firing cancels the other -- which is the thing a caller cannot
build out of two separate forever orders, and the reason the second leg rides
in the same request as `price1`, `triggerPrice1` and `quantity1`.

TWO NAMES THAT LIE. On the way OUT, Dhan puts SINGLE or OCO into `orderType`,
not the LIMIT or MARKET the order was sent as -- read literally that is an
order type which does not exist. And `legName` here does NOT mean what it
means on a super order: TARGET_LEG is a SINGLE and an OCO's first leg,
STOP_LOSS_LEG is an OCO's second, and there is no ENTRY_LEG to send.

Built to https://dhanhq.co/docs/v2/forever/, which specifies every request and
every response. Nothing here has been sent to Dhan: this account cannot place
an order without a whitelisted static IP.
"""

from __future__ import annotations

from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.reports import OrderStatusReport
from nautilus_trader.model.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
    TriggerType,
)
from nautilus_trader.model.identifiers import AccountId, ClientOrderId, VenueOrderId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price
from nautilus_trader.model.orders import Order

from nautilus_india.dhan.constants import (
    FOREVER_LEG_NAMES,
    FOREVER_ORDER_TYPES,
    FOREVER_ORDERS_PATH,
    FOREVER_PRODUCT_TYPES,
    ORDER_FLAG_OCO,
    ORDER_FLAG_SINGLE,
    ORDER_FLAGS,
    SIDE_BUY,
    VALIDITY_DAY,
)
from nautilus_india.dhan.orders import (
    ORDER_STATUS,
    Unsendable,
    _checked,
    _price_string,
    correlation_id,
    ist_to_ns,
    lots_for,
    segment_name,
    units_for,
    validity_for,
)

# The ordinary book's seven, plus one that appears only here: a forever order
# that is resting and waiting for its trigger.
FOREVER_ORDER_STATUS = dict(ORDER_STATUS) | {"CONFIRM": OrderStatus.ACCEPTED}


# THE TRIGGER IS NOT THE ORDER TYPE. Dhan's forever request carries BOTH
# `orderType` and `triggerPrice`: the trigger is what the order waits for, the
# order type is what it becomes once it fires. So a Nautilus STOP_LIMIT is a
# LIMIT forever order with a trigger, not a STOP_LOSS -- which this endpoint
# does not accept at all.
_FOREVER_ORDER_TYPE = {
    OrderType.STOP_LIMIT: "LIMIT",
    OrderType.LIMIT: "LIMIT",
    OrderType.STOP_MARKET: "MARKET",
    OrderType.MARKET: "MARKET",
}


def order_type_for_forever(order_type: OrderType) -> str:
    """What the order becomes once its trigger fires."""
    try:
        return _FOREVER_ORDER_TYPE[order_type]
    except KeyError:
        raise Unsendable(
            f"a forever order becomes {sorted(FOREVER_ORDER_TYPES)} once it "
            f"triggers, and {order_type!r} is neither. The trigger is what "
            "makes it conditional; the order type is what happens after."
        ) from None


def _trigger_of(order: Order) -> Price:
    if not order.has_trigger_price:
        raise Unsendable(
            f"{order.client_order_id} has no trigger price, and a forever order "
            "is a GOOD TILL TRIGGERED order -- Dhan marks `triggerPrice` "
            "required because without one there is nothing to wait for. Send a "
            "stop-limit or stop-market order, or place it on /v2/orders as a "
            "day order instead."
        )
    return order.trigger_price


def place_payload(
    order: Order,
    instrument: Instrument,
    security_id: str,
    client_id: str,
    product_type: str,
    *,
    second_leg: Order | None = None,
) -> dict:
    """The body of POST /v2/forever/orders.

    `second_leg` makes it an OCO: two resting orders where either firing
    cancels the other.
    """
    dhan_type = order_type_for_forever(order.order_type)
    trigger = _trigger_of(order)
    return {
        "dhanClientId": client_id,
        "correlationId": correlation_id(order.client_order_id),
        "orderFlag": ORDER_FLAG_OCO if second_leg is not None else ORDER_FLAG_SINGLE,
        "transactionType": SIDE_BUY if order.side is OrderSide.BUY else "SELL",
        "exchangeSegment": segment_name(instrument),
        # Only the delivery types can rest: an INTRADAY forever order is a
        # contradiction, and Dhan documents neither it nor MARGIN here.
        "productType": _checked(product_type, FOREVER_PRODUCT_TYPES, "productType"),
        "orderType": dhan_type,
        "validity": validity_for(order.time_in_force),
        "securityId": str(security_id),
        "quantity": str(units_for(instrument, order.quantity)),
        "disclosedQuantity": (
            str(units_for(instrument, order.display_qty))
            if getattr(order, "display_qty", None)
            else ""
        ),
        "price": _price_string(order.price if order.has_price else None),
        "triggerPrice": _price_string(trigger),
        # The OCO's second leg. Empty on a SINGLE: filling these would ask for
        # an order that was not requested.
        "price1": _price_string(second_leg.price if second_leg is not None else None),
        "triggerPrice1": _price_string(
            _trigger_of(second_leg) if second_leg is not None else None
        ),
        "quantity1": (
            str(units_for(instrument, second_leg.quantity)) if second_leg is not None else ""
        ),
    }


def modify_payload(
    order_id: str,
    client_id: str,
    order_flag: str,
    order_type: str,
    leg_name: str,
    quantity_units: int,
    price: str,
    disclosed_units: int,
    trigger_price: str,
    validity: str,
) -> dict:
    """The body of PUT /v2/forever/orders/{order-id}."""
    return {
        "dhanClientId": client_id,
        "orderId": str(order_id),
        "orderFlag": _checked(order_flag, ORDER_FLAGS, "orderFlag"),
        "orderType": order_type,
        # NOT a super order's legName -- see the module docstring.
        "legName": _checked(leg_name, FOREVER_LEG_NAMES, "legName"),
        "quantity": quantity_units,
        "price": str(price),
        "disclosedQuantity": disclosed_units,
        "triggerPrice": str(trigger_price),
        "validity": validity,
    }


def cancel_path(order_id: str) -> str:
    """DELETE /v2/forever/orders/{order-id}. No leg: the whole order goes."""
    return f"{FOREVER_ORDERS_PATH}/{order_id}"


def reports_from(rows, instrument_for, account_id: AccountId, ts_init: int) -> list:
    """One report per resting order."""
    found = []
    for row in rows or []:
        instrument = instrument_for(row)
        if instrument is None:
            continue
        status = str(row.get("orderStatus") or "")
        if status not in FOREVER_ORDER_STATUS:
            raise ValueError(
                f"Dhan reported forever-order status {status!r}, which this "
                f"adapter does not map. Known: {sorted(FOREVER_ORDER_STATUS)}."
            )
        correlation = str(row.get("correlationId") or "")
        found.append(
            OrderStatusReport(
                account_id=account_id,
                instrument_id=instrument.id,
                venue_order_id=VenueOrderId(str(row["orderId"])),
                client_order_id=ClientOrderId(correlation) if correlation else None,
                order_side=(
                    OrderSide.BUY if row.get("transactionType") == SIDE_BUY
                    else OrderSide.SELL
                ),
                # `orderType` on the way out carries the order FLAG, so it says
                # nothing about LIMIT or MARKET. What is certain is that the
                # order is conditional on its trigger.
                order_type=OrderType.STOP_LIMIT if row.get("price") else OrderType.STOP_MARKET,
                # It outlives the session by definition. Reported as DAY the
                # engine would expect it gone at the close and be surprised.
                time_in_force=TimeInForce.GTC,
                order_status=FOREVER_ORDER_STATUS[status],
                quantity=lots_for(instrument, row.get("quantity")),
                filled_qty=lots_for(instrument, 0),
                price=Price.from_str(str(row["price"])) if row.get("price") else None,
                trigger_price=(
                    Price.from_str(str(row["triggerPrice"]))
                    if row.get("triggerPrice") else None
                ),
                # Dhan does not publish what a forever order's trigger watches,
                # so DEFAULT says "the venue's own" rather than inventing one.
                trigger_type=(
                    TriggerType.DEFAULT if row.get("triggerPrice")
                    else TriggerType.NO_TRIGGER
                ),
                report_id=UUID4(),
                ts_accepted=ist_to_ns(row.get("createTime")),
                ts_last=ist_to_ns(row.get("updateTime")) or ist_to_ns(row.get("createTime")),
                ts_init=ts_init,
            )
        )
    return found


__all__ = [
    "FOREVER_ORDERS_PATH",
    "FOREVER_ORDER_STATUS",
    "VALIDITY_DAY",
    "cancel_path",
    "modify_payload",
    "place_payload",
    "reports_from",
]
