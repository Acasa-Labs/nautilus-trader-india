"""Dhan's order path as pure functions: payloads out, reports in.

NOTHING HERE PERFORMS I/O. Every rule about units, prices, identifiers and
enums lives here so it can be tested without a client, an event loop or a
socket -- and so the client is left holding only the sequencing decisions.

EVERY REFUSAL IS A REFUSAL TO SEND. `Unsendable` is raised before anything
leaves the process, which is why the client can translate it into
`OrderDenied` without further thought: nothing reached the venue, so nothing
is in flight. An error that arrives FROM Dhan is a different thing entirely
and never appears here.

DHAN COUNTS UNITS; NAUTILUS COUNTS CONTRACTS. `order.quantity` is lots and
`instrument.multiplier` is the lot, so the wire figure is the product.
Sending lots orders a sixty-fifth of what was meant; sending the lot twice
squares the position.

THE PRICE GOES ON THE WIRE AS A STRING. Dhan's own documented request
structure sends `price` and `quantity` as strings, and a string is the only
form that carries a Decimal without routing it through a C double on the
way. 24550.05 is not representable in binary floating point.
"""

from __future__ import annotations

from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.orders import Order

from nautilus_india.dhan.constants import (
    CORRELATION_ID_MAX_LEN,
    ORDER_TYPE_LIMIT,
    SEGMENT_NAMES,
    SIDE_BUY,
    SIDE_SELL,
    VALIDITY_DAY,
    VALIDITY_IOC,
)
from nautilus_india.dhan.providers import UNKNOWN_SEGMENT_CODE


class Unsendable(ValueError):
    """This request will not be sent, and the reason is local.

    Every raise site is a check that runs before any I/O. A caller may
    therefore report the order denied without asking Dhan anything: there is
    nothing at the venue to ask about.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# Dhan takes DAY and IOC. GTC maps to DAY rather than being refused because
# NSE rests nothing overnight on this endpoint -- every order dies at the
# close whatever is asked for, so DAY is not a downgrade, it is the only
# thing the venue does. Dhan's GTT equivalent is /v2/forever/orders, which
# this adapter does not implement.
_VALIDITY = {
    TimeInForce.GTC: VALIDITY_DAY,
    TimeInForce.DAY: VALIDITY_DAY,
    TimeInForce.IOC: VALIDITY_IOC,
}

_SIDE = {OrderSide.BUY: SIDE_BUY, OrderSide.SELL: SIDE_SELL}


def validity_for(time_in_force: TimeInForce) -> str:
    """Dhan's validity for a Nautilus time in force. Raises on the rest."""
    try:
        return _VALIDITY[time_in_force]
    except KeyError:
        raise Unsendable(
            f"Dhan's order API takes DAY and IOC only, and {time_in_force!r} is "
            "neither. Mapping it onto one of them would make the order rest when "
            "the caller asked it not to, or the reverse."
        ) from None


def segment_name(instrument: Instrument) -> str:
    """Dhan's REST name for the instrument's segment. Raises rather than guess."""
    code = (instrument.info or {}).get("segment_code", UNKNOWN_SEGMENT_CODE)
    name = SEGMENT_NAMES.get(code)
    if name is None:
        raise Unsendable(
            f"{instrument.id} carries no Dhan feed segment (code {code!r}), so "
            "there is no exchangeSegment to send. NSE commodity is the known "
            "case: Dhan publishes no code for it and its own SDK defines none. "
            "Refusing to guess -- a wrong segment names a DIFFERENT instrument, "
            "and Dhan answers 200 with empty data for one that does not exist, "
            "so the guess would read as a quiet market rather than an error."
        )
    return name


def units_for(instrument: Instrument, quantity: Quantity) -> int:
    """Contracts to the units Dhan counts in.

    `quantity` is lots and `multiplier` is the lot size -- see CLAUDE.md on
    why `lot_size` is 1 and the lot lives in the multiplier.
    """
    return int(quantity.as_decimal() * instrument.multiplier.as_decimal())


def correlation_id(client_order_id: ClientOrderId) -> str:
    """The ClientOrderId, checked against Dhan's 30-character cap.

    Refused rather than truncated: a truncated id round-trips as a DIFFERENT
    id, so the fill it comes back on matches no order and reads as an
    unexplained position rather than as a bug.
    """
    value = client_order_id.value
    if len(value) > CORRELATION_ID_MAX_LEN:
        raise Unsendable(
            f"client order id {value!r} is {len(value)} characters and Dhan's "
            f"correlationId takes at most {CORRELATION_ID_MAX_LEN}. It is the "
            "only field that round-trips a caller's own id, so it cannot be "
            "truncated. Set `use_uuid_client_order_ids=False` on the trader: "
            "the default id is 27 characters and fits."
        )
    return value


def place_payload(
    order: Order,
    instrument: Instrument,
    security_id: str,
    client_id: str,
    product_type: str,
) -> dict:
    """The body of POST /v2/orders. Raises before building an unsound one."""
    if order.order_type is not OrderType.LIMIT:
        raise Unsendable(
            f"{order.order_type!r} is not sent to Dhan. Dhan converts an API "
            "MARKET order into a LIMIT order with market-protection pricing, so "
            "a 'market' order fills at a limit the caller did not choose and "
            "cannot see. This adapter sends LIMIT only; name your price."
        )
    return {
        "dhanClientId": client_id,
        "correlationId": correlation_id(order.client_order_id),
        "transactionType": _SIDE[order.side],
        "exchangeSegment": segment_name(instrument),
        "productType": product_type,
        "orderType": ORDER_TYPE_LIMIT,
        "validity": validity_for(order.time_in_force),
        "securityId": str(security_id),
        "quantity": str(units_for(instrument, order.quantity)),
        "price": str(order.price),
        "disclosedQuantity": "0",
        "afterMarketOrder": False,
    }


def modify_payload(
    order_id: str,
    client_id: str,
    quantity_units: int,
    price: str,
    validity: str,
) -> dict:
    """The body of PUT /v2/orders/{order-id}.

    `legName` is deliberately absent. It is required only for BO and CO
    orders, which this adapter does not place, and sending an empty one has
    never been tested against the venue.
    """
    return {
        "dhanClientId": client_id,
        "orderId": str(order_id),
        "orderType": ORDER_TYPE_LIMIT,
        "quantity": str(quantity_units),
        "price": str(price),
        "disclosedQuantity": "0",
        "validity": validity,
    }
