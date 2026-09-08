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

import hashlib
import re
from collections.abc import Iterable
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
    TriggerType,
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


# Dhan accepts alphanumerics, spaces, hyphens and underscores -- measured.
# The docs' own note is "[^a-zA-Z0-9 _-]", whose leading caret NEGATES the
# class, so it cannot be read literally.
_SAFE_CORRELATION = re.compile(r"^[A-Za-z0-9 _-]+$")

# 9 bytes -> 18 hex characters, inside the measured 25-character limit with
# room to spare. 72 bits: at a million orders the chance of any collision is
# about one in ten billion, and a collision would attach one order's fills to
# another's position, so the margin is deliberate.
_DIGEST_BYTES = 9


def correlation_id(client_order_id: ClientOrderId) -> str:
    """The value to send as `correlationId`, derived when it has to be.

    An id Dhan will take is sent AS IS, so it stays readable in Dhan's own
    order book. Anything longer than the measured 25 characters, or carrying a
    character outside the measured charset, is hashed instead.

    HASHED RATHER THAN TRUNCATED, AND RATHER THAN REFUSED. Truncating sends a
    DIFFERENT id, and the fill that comes back on it matches no order --
    which reads as an unexplained position rather than as a bug. Refusing
    would deny every ordinary order, because Nautilus's generator cannot
    produce one that fits: the default is 27 characters and the format's fixed
    parts alone exceed the limit.

    HASHED RATHER THAN COUNTED, so the derivation is deterministic. A counter
    would need a map, the map would need persisting, and a restart between a
    submission and its answer is exactly when the id matters most --
    `GET /v2/orders/external/{id}` is the only way to find an order whose
    placement timed out.

    The cost is that the id in Dhan's own UI is opaque. `client_order_id_for`
    is how it maps back.
    """
    value = client_order_id.value
    if len(value) <= CORRELATION_ID_MAX_LEN and _SAFE_CORRELATION.match(value):
        return value
    return hashlib.blake2s(value.encode(), digest_size=_DIGEST_BYTES).hexdigest()


def client_order_id_for(
    correlation: str, candidates: Iterable[ClientOrderId]
) -> ClientOrderId | None:
    """Which of our orders a `correlationId` belongs to, or None.

    By recomputation rather than by reversal -- that is what makes the
    deterministic derivation load-bearing rather than incidental.

    None is a real answer and not a failure: an order placed from Dhan's own
    app, or by a session whose orders this one never saw, carries a
    correlationId that is not ours. Guessing at the nearest of our own would
    attach a stranger's fill to our position.
    """
    if not correlation:
        return None
    for candidate in candidates:
        if correlation_id(candidate) == correlation:
            return candidate
    return None


def _price_string(value: object) -> str:
    """A price for the wire, or `""` when the field does not apply.

    Used by the super-order and forever-order payloads, where every price
    field Dhan documents is REQUIRED, so the empty case barely arises. On
    /v2/orders it does arise, and there an empty string is refused -- see
    `place_payload`, which omits the key instead.
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
    """The body of POST /v2/orders.

    A FIELD THAT DOES NOT APPLY IS OMITTED, NOT SENT EMPTY. Dhan's documented
    request structure sends `""` for `price`, `triggerPrice`,
    `disclosedQuantity`, `amoTime`, `boProfitValue` and `boStopLossValue` when
    they do not apply. Sent that way the order is REFUSED -- DH-905, "Missing
    required fields, bad values for parameters etc.", naming nothing. The
    identical payload with those keys absent is accepted. Measured in the
    sandbox on 2026-09-08 by sending both; the empty strings are the only
    difference between the two.
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

    payload = {
        "dhanClientId": client_id,
        "correlationId": correlation_id(order.client_order_id),
        "transactionType": _SIDE[order.side],
        "exchangeSegment": segment_name(instrument),
        "productType": _checked(product_type, PRODUCT_TYPES, "productType"),
        "orderType": dhan_type,
        "validity": validity_for(order.time_in_force),
        "securityId": str(security_id),
        "quantity": str(units_for(instrument, order.quantity)),
        "afterMarketOrder": after_market_order,
    }
    if price is not None:
        payload["price"] = str(price)
    if trigger is not None:
        payload["triggerPrice"] = str(trigger)
    # Dhan counts the disclosed quantity in units too, so a lot figure here
    # would disclose a sixty-fifth of what was meant.
    display = getattr(order, "display_qty", None)
    if display:
        payload["disclosedQuantity"] = str(units_for(instrument, display))
    if after_market_order:
        # Conditionally required, and only once the order IS an AMO. Sending a
        # window on an ordinary order claims something the caller did not.
        payload["amoTime"] = _checked(amo_time, AMO_TIMES, "amoTime")
    return payload


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
    """The body of PUT /v2/orders/{order-id}.

    A modify REPLACES the order's terms rather than patching them, so every
    field that APPLIES is stated -- an omitted one that applies is not "leave
    it alone", it is a term the venue decides for itself.

    A field that does NOT apply is omitted rather than emptied, for the same
    measured reason as `place_payload`: sent as `""` the modify is refused
    with DH-905 naming nothing. Verified against the sandbox on 2026-09-08 by
    sending both forms at the same order.

    `legName` names which leg of a bracket or cover order is being changed;
    it is absent for an ordinary order.
    """
    payload = {
        "dhanClientId": client_id,
        "orderId": str(order_id),
        "orderType": order_type,
        "quantity": str(quantity_units),
        "price": str(price),
        "validity": validity,
    }
    if leg_name:
        payload["legName"] = _checked(leg_name, LEG_NAMES, "legName")
    if trigger_price:
        payload["triggerPrice"] = str(trigger_price)
    return payload


# -- reading Dhan's answers back ---------------------------------------------

# Dhan stamps with NO ZONE, in India Standard Time. Read as UTC these land
# five and a half hours early -- most of a session -- so a fill drops onto
# the previous trading day and reconciliation compares two different days.
_DHAN_TIME = "%Y-%m-%d %H:%M:%S"
_DHAN_DATE = "%Y-%m-%d"
_NANOS_PER_SECOND = 1_000_000_000

# Dhan's "never happened" marker. An order that was rejected before reaching
# the exchange comes back with `exchangeTime: "0001-01-01 00:00:00"` and
# `drvExpiryDate: "0001-01-01"` -- sentinels, not times. The docs show `null`
# for these fields; the API sends these. Parsed literally the first is
# -62135618008000000000 nanoseconds, and Nautilus accepts a negative timestamp
# WITHOUT COMPLAINT, so the report would carry a date in year 1 and nothing
# would notice. Measured on a real rejected order, sandbox, 2026-09-08.
_ZERO_DATE_PREFIX = "0001-01-01"

# Dhan's seven documented statuses. `CLOSED` appears on super orders, which
# this adapter does not place.
# The statuses on which `omsErrorDescription` is a reason rather than a note.
# MEASURED: on a healthy resting order the field reads "CONFIRMED", which is
# not an error and not a reason -- reported as `cancel_reason` it says a
# working order was cancelled because it was confirmed.
_DEAD_STATUSES = frozenset({"REJECTED", "CANCELLED", "EXPIRED"})

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
    """A Dhan timestamp as UNIX nanoseconds. Unset is 0, never a real instant.

    Three things count as unset: absent, empty, and Dhan's `0001-01-01`
    sentinel -- see `_ZERO_DATE_PREFIX` for why the last one matters.
    """
    if not stamp or str(stamp).startswith(_ZERO_DATE_PREFIX):
        return 0
    text = str(stamp)
    fmt = _DHAN_TIME if " " in text else _DHAN_DATE
    parsed = datetime.strptime(text, fmt).replace(tzinfo=IST)
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
    client_order_ids: Iterable[ClientOrderId] = (),
) -> OrderStatusReport:
    """One row of GET /v2/orders as a Nautilus report.

    `client_order_ids` are the orders this session knows about, used to map
    Dhan's echoed `correlationId` back to ours. It has to be a lookup rather
    than a cast, because the value on the wire may be a DERIVED id -- see
    `correlation_id` -- and because Dhan generates its own when none is sent.
    """
    correlation = str(row.get("correlationId") or "")
    average = row.get("averageTradedPrice")
    return OrderStatusReport(
        account_id=account_id,
        instrument_id=instrument.id,
        venue_order_id=VenueOrderId(str(row["orderId"])),
        # None is a real answer: an order placed from Dhan's own app, or one
        # whose correlationId Dhan generated itself, is not ours. The row is
        # still reported -- dropping it would hide a position the account
        # really holds, which is the case reconciliation exists to catch.
        client_order_id=client_order_id_for(correlation, client_order_ids),
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
        # Nautilus refuses a report that carries a trigger price and no trigger
        # TYPE. Dhan does not publish what its stops watch, so DEFAULT says
        # "the venue's own" rather than claiming last-price or mark-price --
        # either of which would be this adapter inventing a detail.
        trigger_type=(
            TriggerType.DEFAULT if row.get("triggerPrice") else TriggerType.NO_TRIGGER
        ),
        avg_px=Decimal(str(average)) if average else None,
        # The only place Dhan says why a row was refused -- but ONLY once the
        # row is actually dead. See `_DEAD_STATUSES`.
        cancel_reason=(
            str(row["omsErrorDescription"])
            if row.get("omsErrorDescription")
            and str(row.get("orderStatus")) in _DEAD_STATUSES
            else None
        ),
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
