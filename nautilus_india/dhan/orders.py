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
way. 24550.05 is not representable in binary floating point. A field that
does not apply is sent as `""`, which is what Dhan's sample does -- not as
`0`, because 0 is a price and no order was placed at one.

THE SURFACE IS DHAN'S DOCUMENTED ONE. <https://dhanhq.co/docs/v2/orders/>
specifies every request and response field by field, with the enum values
for each, so that is what this builds to: four order types, six product
types, two validities, the AMO window, and the disclosed quantity. The
corpus keeps those documented shapes in `tests/dhan/fixtures/envelope/
documented/`, requests included, and a test asserts the payload built here
carries exactly the fields Dhan lists.

ONE THING TO KNOW ABOUT A MARKET ORDER, which is disclosure rather than a
refusal: Dhan converts an API MARKET order into a limit order with
market-protection pricing, so it fills at a limit the caller did not name.
That is the venue's behaviour, it is measured, and it is logged when one is
sent -- but it is not a reason to refuse an order Dhan documents and the
caller asked for.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.reports import (
    FillReport,
    OrderStatusReport,
    PositionStatusReport,
)
from nautilus_trader.model.currencies import INR
from nautilus_trader.model.enums import (
    LiquiditySide,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    TimeInForce,
)
from nautilus_trader.model.identifiers import AccountId, ClientOrderId, TradeId, VenueOrderId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.orders import Order

from nautilus_india.core.calendar import IST
from nautilus_india.dhan.constants import (
    AMO_TIMES,
    CORRELATION_ID_MAX_LEN,
    LEG_NAMES,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_STOP_LOSS,
    ORDER_TYPE_STOP_LOSS_MARKET,
    PRODUCT_TYPES,
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

# The four Dhan documents, and the Nautilus type each answers to. Nothing
# else is mapped: sending a trailing stop as a plain stop would rest an order
# at a level the caller never chose.
ORDER_TYPE = {
    OrderType.LIMIT: ORDER_TYPE_LIMIT,
    OrderType.MARKET: ORDER_TYPE_MARKET,
    OrderType.STOP_LIMIT: ORDER_TYPE_STOP_LOSS,
    OrderType.STOP_MARKET: ORDER_TYPE_STOP_LOSS_MARKET,
}

# Which of the four carry which price. `triggerPrice` is documented as
# conditionally required "in case of SL-M & SL-L", and without it Dhan has no
# level to trigger on.
_NEEDS_PRICE = {ORDER_TYPE_LIMIT, ORDER_TYPE_STOP_LOSS}
_NEEDS_TRIGGER = {ORDER_TYPE_STOP_LOSS, ORDER_TYPE_STOP_LOSS_MARKET}


def order_type_for(order_type: OrderType) -> str:
    """Dhan's order type for a Nautilus one. Raises on the rest."""
    try:
        return ORDER_TYPE[order_type]
    except KeyError:
        raise Unsendable(
            f"Dhan's order endpoint documents {sorted(set(ORDER_TYPE.values()))} "
            f"and {order_type!r} is none of them. Mapping it onto one of them "
            "would send an order the caller did not ask for -- a trailing stop "
            "sent as a plain stop rests at a level nobody chose."
        ) from None


def _checked(value: str, allowed: frozenset[str], field: str) -> str:
    """A vocabulary Dhan publishes, checked before it is sent.

    Dhan answers a bad one with its generic Input_Exception, which carries no
    information -- DH-905 is returned for "Invalid IP" and for "quantity is
    required" alike -- so the round trip would tell the caller nothing.
    """
    if value not in allowed:
        raise Unsendable(
            f"{field} must be one of {sorted(allowed)}, got {value!r}. Dhan "
            "answers an unknown one with its generic Input_Exception, whose "
            "message names neither the field nor the value."
        )
    return value


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


def _price_string(value: object) -> str:
    """A price for the wire, or `""` when the field does not apply.

    Empty rather than "0": Dhan's own sample sends `""` for a field that does
    not apply, and 0 is a price -- one no order was ever placed at.
    """
    return str(value) if value is not None else ""


def place_payload(
    order: Order,
    instrument: Instrument,
    security_id: str,
    client_id: str,
    product_type: str,
    *,
    after_market_order: bool = False,
    amo_time: str = "",
) -> dict:
    """The body of POST /v2/orders, field for field with Dhan's own.

    Every key Dhan documents is present, because Dhan validates in ORDER --
    quantity, then the IP, then the instrument -- so a payload missing one
    fails with "quantity is required" and never reaches the check that would
    have named the real problem.
    """
    dhan_type = order_type_for(order.order_type)
    price = order.price if dhan_type in _NEEDS_PRICE and order.has_price else None
    trigger = (
        order.trigger_price
        if dhan_type in _NEEDS_TRIGGER and order.has_trigger_price
        else None
    )
    if dhan_type in _NEEDS_TRIGGER and trigger is None:
        raise Unsendable(
            f"{dhan_type} needs a triggerPrice and this order has none. Dhan "
            "documents it as conditionally required for SL-M and SL-L, and "
            "without one the venue has no level to trigger on."
        )
    # Dhan counts the disclosed quantity in units too, so a lot figure here
    # would disclose a sixty-fifth of what was meant.
    display = getattr(order, "display_qty", None)
    return {
        "dhanClientId": client_id,
        "correlationId": correlation_id(order.client_order_id),
        "transactionType": _SIDE[order.side],
        "exchangeSegment": segment_name(instrument),
        "productType": _checked(product_type, PRODUCT_TYPES, "productType"),
        "orderType": dhan_type,
        "validity": validity_for(order.time_in_force),
        "securityId": str(security_id),
        "quantity": str(units_for(instrument, order.quantity)),
        "disclosedQuantity": str(units_for(instrument, display)) if display else "",
        "price": _price_string(price),
        "triggerPrice": _price_string(trigger),
        "afterMarketOrder": after_market_order,
        # Conditionally required, and only once the order IS an AMO. Sending a
        # window on an ordinary order claims something the caller did not.
        "amoTime": _checked(amo_time, AMO_TIMES, "amoTime") if after_market_order else "",
        # Bracket-order legs. Empty unless the product type is BO, which this
        # adapter does not build for the caller -- but the fields are Dhan's
        # and their absence is a payload Dhan does not recognise.
        "boProfitValue": "",
        "boStopLossValue": "",
    }


def modify_payload(
    order_id: str,
    client_id: str,
    quantity_units: int,
    price: str,
    trigger_price: str,
    validity: str,
    order_type: str,
    leg_name: str = "",
) -> dict:
    """The body of PUT /v2/orders/{order-id}, field for field with Dhan's own.

    A modify REPLACES the order's terms rather than patching them, so every
    field Dhan names is sent -- an omitted one is not "leave it alone", it is
    a term the venue decides for itself.

    `legName` names which leg of a bracket or cover order is being changed.
    Dhan has no other handle on a leg, and it is empty for an ordinary order.
    """
    return {
        "dhanClientId": client_id,
        "orderId": str(order_id),
        "orderType": order_type,
        "legName": _checked(leg_name, LEG_NAMES, "legName") if leg_name else "",
        "quantity": str(quantity_units),
        "price": str(price),
        "disclosedQuantity": "",
        "triggerPrice": str(trigger_price),
        "validity": validity,
    }


# -- reading Dhan's answers back ---------------------------------------------

# Dhan stamps with NO ZONE, in India Standard Time. Read as UTC these land
# five and a half hours early -- most of a session -- so a fill drops onto
# the previous trading day and reconciliation compares two different days.
_DHAN_TIME = "%Y-%m-%d %H:%M:%S"
_NANOS_PER_SECOND = 1_000_000_000

# Dhan's seven documented statuses. `CLOSED` appears on super orders, which
# this adapter does not place.
ORDER_STATUS = {
    "TRANSIT": OrderStatus.SUBMITTED,
    "PENDING": OrderStatus.ACCEPTED,
    "REJECTED": OrderStatus.REJECTED,
    "CANCELLED": OrderStatus.CANCELED,
    "PART_TRADED": OrderStatus.PARTIALLY_FILLED,
    "TRADED": OrderStatus.FILLED,
    "EXPIRED": OrderStatus.EXPIRED,
}

_POSITION_SIDE = {
    "LONG": PositionSide.LONG,
    "SHORT": PositionSide.SHORT,
    "CLOSED": PositionSide.FLAT,
}


def ist_to_ns(stamp: str | None) -> int:
    """A Dhan timestamp as UNIX nanoseconds. Empty means unset, not the epoch."""
    if not stamp:
        return 0
    parsed = datetime.strptime(stamp, _DHAN_TIME).replace(tzinfo=IST)
    return int(parsed.timestamp()) * _NANOS_PER_SECOND


def lots_for(instrument: Instrument, units: object) -> Quantity:
    """Dhan's units as a Nautilus quantity, which counts contracts.

    Raises on a remainder rather than rounding. A remainder means Dhan and
    this package's snapshot of Dhan's own master disagree about the lot, and
    a rounded position report is quietly wrong in a way the engine trades on.
    """
    lot = instrument.multiplier.as_decimal()
    count = abs(Decimal(str(units or 0)))
    lots, remainder = divmod(count, lot)
    if remainder:
        raise ValueError(
            f"Dhan reports {count} units of {instrument.id}, which is not a whole "
            f"number of its lot ({lot}). Refusing to round: the lot in this "
            "package's snapshot of the scrip master and the lot the exchange is "
            "using have diverged, and a rounded quantity is a position report "
            "that is wrong in a direction nobody chose."
        )
    return Quantity.from_str(str(int(lots)))


def _price_or_none(value: object) -> Price | None:
    """Dhan sends 0.0 for a price no order had -- a market order's limit, an
    unfilled order's average. Rendered literally that is a limit of zero."""
    if not value:
        return None
    return Price.from_str(str(value))


def _side_of(row: dict) -> OrderSide:
    return OrderSide.BUY if row.get("transactionType") == SIDE_BUY else OrderSide.SELL


def _status_of(row: dict) -> OrderStatus:
    raw = str(row.get("orderStatus") or "")
    try:
        return ORDER_STATUS[raw]
    except KeyError:
        raise ValueError(
            f"Dhan reported order status {raw!r}, which this adapter does not map. "
            f"Known: {sorted(ORDER_STATUS)}. Refusing to default: a status read as "
            "accepted when it means rejected leaves the engine waiting for a fill "
            "that is never coming."
        ) from None


def order_status_report(
    row: dict,
    instrument: Instrument,
    account_id: AccountId,
    report_id: UUID4,
    ts_init: int,
) -> OrderStatusReport:
    """One row of GET /v2/orders as a Nautilus report."""
    correlation = str(row.get("correlationId") or "")
    average = row.get("averageTradedPrice")
    return OrderStatusReport(
        account_id=account_id,
        instrument_id=instrument.id,
        venue_order_id=VenueOrderId(str(row["orderId"])),
        # An order placed from Dhan's own app carries no correlationId.
        # Dropping the row would hide a position the account really holds.
        client_order_id=ClientOrderId(correlation) if correlation else None,
        order_side=_side_of(row),
        # This adapter sends LIMIT only, so a MARKET row can only be an order
        # the account placed somewhere else. Reported as what it is.
        order_type=OrderType.MARKET if row.get("orderType") == "MARKET" else OrderType.LIMIT,
        time_in_force=TimeInForce.IOC if row.get("validity") == VALIDITY_IOC
        else TimeInForce.DAY,
        order_status=_status_of(row),
        quantity=lots_for(instrument, row.get("quantity")),
        filled_qty=lots_for(instrument, row.get("filledQty")),
        price=_price_or_none(row.get("price")),
        trigger_price=_price_or_none(row.get("triggerPrice")),
        avg_px=Decimal(str(average)) if average else None,
        # The only place Dhan says why a row was refused.
        cancel_reason=(str(row["omsErrorDescription"])
                       if row.get("omsErrorDescription") else None),
        report_id=report_id,
        ts_accepted=ist_to_ns(row.get("createTime")),
        ts_last=ist_to_ns(row.get("updateTime")) or ist_to_ns(row.get("createTime")),
        ts_init=ts_init,
    )


def fill_report(
    row: dict,
    instrument: Instrument,
    account_id: AccountId,
    report_id: UUID4,
    ts_init: int,
) -> FillReport:
    """One row of GET /v2/trades as a Nautilus report.

    THE COMMISSION IS ZERO AND THAT IS DELIBERATE. GET /v2/trades carries no
    charge figure and the margin calculator returns `brokerage: 0.0`, so Dhan
    does not tell us. This package CAN model the charge, and putting that
    estimate here would launder our own number into a broker record.
    `core.fees` is where a cost estimate belongs.
    """
    return FillReport(
        account_id=account_id,
        instrument_id=instrument.id,
        venue_order_id=VenueOrderId(str(row["orderId"])),
        # The only per-fill identity Dhan gives. Keying on orderId instead
        # collapses a partially filled order's fills into one.
        trade_id=TradeId(str(row["exchangeTradeId"])),
        order_side=_side_of(row),
        last_qty=lots_for(instrument, row.get("tradedQuantity")),
        last_px=Price.from_str(str(row["tradedPrice"])),
        commission=Money(0, INR),
        # Dhan does not say which side provided liquidity, and claiming one
        # would put a fabricated maker/taker flag on every recorded fill.
        liquidity_side=LiquiditySide.NO_LIQUIDITY_SIDE,
        report_id=report_id,
        ts_event=ist_to_ns(row.get("exchangeTime")) or ist_to_ns(row.get("createTime")),
        ts_init=ts_init,
    )


def position_status_report(
    row: dict,
    instrument: Instrument,
    account_id: AccountId,
    report_id: UUID4,
    ts_init: int,
) -> PositionStatusReport:
    """One row of GET /v2/positions as a Nautilus report."""
    side = _POSITION_SIDE.get(str(row.get("positionType") or ""))
    if side is None:
        raise ValueError(
            f"Dhan reported positionType {row.get('positionType')!r}, which this "
            "adapter does not map. Known: LONG, SHORT, CLOSED."
        )
    net = Decimal(str(row.get("netQty") or 0))
    if not net:
        # Dhan keeps a squared-off contract in the book at netQty 0 for the
        # rest of the session. Anything but flat and the engine closes a
        # position that is already gone.
        side = PositionSide.FLAT
    # The entry is what the SURVIVING side traded at: a short was entered by
    # selling, and reporting its buyAvg shows the price it is being closed at
    # as the price it was opened at.
    average = row.get("buyAvg") if net > 0 else row.get("sellAvg")
    return PositionStatusReport(
        account_id=account_id,
        instrument_id=instrument.id,
        position_side=side,
        quantity=lots_for(instrument, net),
        avg_px_open=Decimal(str(average)) if average else None,
        report_id=report_id,
        ts_last=ist_to_ns(row.get("updateTime")),
        ts_init=ts_init,
    )


# Disclosure, not a refusal. Dhan converts an API MARKET order into a limit
# order with market-protection pricing, measured on a live account, so a
# "market" order fills at a limit the caller never named. The order is still
# sent -- Dhan documents MARKET and the caller asked for it -- and the client
# says this when it sends one.
MARKET_ORDER_NOTE = (
    "Dhan converts an API MARKET order into a LIMIT order with "
    "market-protection pricing, so this order will fill at a limit that "
    "neither you nor this adapter chose. Send a LIMIT order to name your own."
)
