"""What goes on the wire, and what must never get that far.

DHAN CANNOT BE ASKED WHETHER A PAYLOAD IS RIGHT. Placing an order needs a
whitelisted static IP, and the only machine holding these credentials has a
dynamic residential address. So no test here is a round trip; each one is a
measured behaviour or a published field list, and says which.
"""


import pytest
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import ClientOrderId, StrategyId, TraderId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import LimitOrder, MarketOrder

from nautilus_india.dhan import orders
from nautilus_india.dhan.orders import Unsendable


def _limit_order(instrument, price, side=OrderSide.BUY, quantity=None,
                 client_order_id="O-1", time_in_force=TimeInForce.GTC):
    return LimitOrder(
        trader_id=TraderId("TESTER-000"), strategy_id=StrategyId("S-001"),
        instrument_id=instrument.id, client_order_id=ClientOrderId(client_order_id),
        order_side=side, quantity=quantity or Quantity.from_int(1), price=price,
        init_id=UUID4(), ts_init=0, time_in_force=time_in_force,
    )


def _market_order(instrument, side=OrderSide.BUY):
    return MarketOrder(
        trader_id=TraderId("TESTER-000"), strategy_id=StrategyId("S-001"),
        instrument_id=instrument.id, client_order_id=ClientOrderId("O-2"),
        order_side=side, quantity=Quantity.from_int(1), init_id=UUID4(), ts_init=0,
    )


def _payload(instrument, order=None):
    return orders.place_payload(
        order=order or _limit_order(instrument, Price.from_str("100.25")),
        instrument=instrument, security_id="43492",
        client_id="CLIENT1", product_type="INTRADAY",
    )


# -- units, prices and identifiers -------------------------------------------


def test_quantity_is_sent_in_units_not_lots(nifty_option):
    """Nautilus counts CONTRACTS and Dhan counts UNITS. Sending lots orders a
    sixty-fifth of what was meant."""
    assert orders.units_for(nifty_option, Quantity.from_int(2)) == 130


def test_a_price_keeps_every_digit_it_arrived_with(nifty_option):
    """24550.05 does not survive a round trip through binary floating point,
    and a limit one paisa off is a different order."""
    payload = _payload(
        nifty_option, _limit_order(nifty_option, Price.from_str("24550.05"))
    )
    assert payload["price"] == "24550.05"
    assert not isinstance(payload["price"], float)


def test_the_quantity_on_the_wire_is_a_string_too(nifty_option):
    """Dhan's own documented request structure sends quantity and price as
    strings, and a string is the only form that cannot be rounded on the way."""
    assert _payload(nifty_option)["quantity"] == "65"


def test_a_short_client_order_id_rides_in_correlation_id_unchanged():
    """The only field that round-trips: it comes back on GET /v2/orders and on
    the order-update socket, so it is the only way to match a fill to the
    order that caused it."""
    assert orders.correlation_id(ClientOrderId("nti-1")) == "nti-1"


def test_an_id_too_long_for_dhan_is_derived_never_truncated():
    """Nautilus's generator cannot produce an id that fits -- the default is
    27 characters and the venue takes 25 -- so refusing them would deny every
    ordinary order, and truncating one would send a DIFFERENT id whose fill
    matches nothing. It is hashed instead: deterministic, so no map has to be
    stored and nothing breaks across a restart."""
    got = orders.correlation_id(ClientOrderId("O-19700101-000000-001-000-1"))
    assert len(got) <= orders.CORRELATION_ID_MAX_LEN
    assert got != "O-19700101-000000-001-000-1"[: orders.CORRELATION_ID_MAX_LEN]


def test_the_derived_id_is_deterministic():
    """The whole reason for hashing rather than counting: the same order id
    yields the same correlationId in a later process, so a timed-out
    submission can still be found with GET /orders/external/{id}."""
    a = orders.correlation_id(ClientOrderId("O-19700101-000000-001-000-1"))
    b = orders.correlation_id(ClientOrderId("O-19700101-000000-001-000-1"))
    assert a == b


def test_different_orders_get_different_derived_ids():
    """A collision would attach one order's fills to another's position."""
    seen = {
        orders.correlation_id(ClientOrderId(f"O-19700101-000000-001-000-{n}"))
        for n in range(2000)
    }
    assert len(seen) == 2000


def test_the_derived_id_uses_a_charset_dhan_accepts():
    """Measured: alphanumerics, spaces, hyphens and underscores are accepted.
    The docs' own note is `[^a-zA-Z0-9 _-]`, whose leading caret negates the
    class, so it cannot be read literally. Hex is inside every reading."""
    got = orders.correlation_id(ClientOrderId("O-19700101-000000-001-000-1"))
    assert got.isalnum() and got.isascii()


def test_a_report_maps_its_correlation_id_back_to_the_order():
    """A hash nothing can reverse is a hash that loses the fill. The mapping
    back is by recomputation over the orders we know about, which is why the
    derivation has to be deterministic."""
    ours = [ClientOrderId("O-19700101-000000-001-000-1"),
            ClientOrderId("O-19700101-000000-001-000-2")]
    sent = orders.correlation_id(ours[1])
    assert orders.client_order_id_for(sent, ours) == ours[1]


def test_an_unknown_correlation_id_maps_to_nothing():
    """An order placed from Dhan's own app, or by a session whose orders we
    never saw. Guessing at one of ours would attach a stranger's fill to our
    position."""
    assert orders.client_order_id_for("SCRUBBEDID-1788847931670", []) is None


def test_a_short_id_still_maps_back_to_itself():
    ours = [ClientOrderId("nti-1")]
    assert orders.client_order_id_for("nti-1", ours) == ClientOrderId("nti-1")


# -- the four refusals -------------------------------------------------------


def test_the_order_type_sent_is_always_limit(nifty_option):
    assert _payload(nifty_option)["orderType"] == "LIMIT"


def test_what_dhan_does_to_a_market_order_is_disclosed_not_hidden(nifty_option):
    """Dhan converts an API MARKET order into a LIMIT order with
    market-protection pricing, so it fills at a limit the caller did not name.
    That is measured, and it is the venue's behaviour to disclose -- the order
    is still sent, because Dhan documents MARKET and the caller asked for it.
    The note exists so the client can say so at submission."""
    assert "market-protection" in orders.MARKET_ORDER_NOTE
    assert _payload(nifty_option, _market_order(nifty_option))["orderType"] == "MARKET"


def test_gtc_becomes_day_because_the_exchange_has_no_other_answer():
    """NSE rests nothing overnight on this endpoint: every order dies at the
    close whatever is asked for. Dhan's GTT equivalent is a different endpoint
    (/v2/forever/orders) and is not in this adapter."""
    assert orders.validity_for(TimeInForce.GTC) == "DAY"
    assert orders.validity_for(TimeInForce.DAY) == "DAY"
    assert orders.validity_for(TimeInForce.IOC) == "IOC"


@pytest.mark.parametrize(
    "tif",
    [TimeInForce.FOK, TimeInForce.GTD, TimeInForce.AT_THE_OPEN, TimeInForce.AT_THE_CLOSE],
)
def test_a_validity_dhan_does_not_have_is_refused(tif):
    """Dhan takes DAY and IOC. Mapping anything else onto one of them makes
    the order rest when the caller asked it not to, or the reverse."""
    with pytest.raises(Unsendable):
        orders.validity_for(tif)


def test_the_segment_comes_from_the_instrument_not_a_guess(nifty_option):
    assert orders.segment_name(nifty_option) == "NSE_FNO"


def test_an_instrument_with_no_feed_segment_cannot_be_routed(nse_commodity_option):
    """NSE commodity has no published segment code -- Dhan's own SDK defines
    none. Guessing at 6 names a DIFFERENT instrument, and Dhan answers 200
    with empty data for one that does not exist, so the guess would read as a
    quiet market rather than an error."""
    with pytest.raises(Unsendable, match="segment"):
        orders.segment_name(nse_commodity_option)


# -- the payload itself ------------------------------------------------------


def test_the_payload_carries_every_field_dhan_requires(nifty_option):
    """Dhan validates in order -- quantity, then the IP, then the instrument --
    so a payload missing a required field never reaches the checks that matter
    and fails with 'quantity is required' instead of the real reason."""
    payload = _payload(nifty_option)
    assert set(payload) >= {
        "dhanClientId", "correlationId", "transactionType", "exchangeSegment",
        "productType", "orderType", "validity", "securityId", "quantity", "price",
    }
    assert payload["dhanClientId"] == "CLIENT1"
    assert payload["securityId"] == "43492"
    assert payload["transactionType"] == "BUY"
    assert payload["exchangeSegment"] == "NSE_FNO"
    assert payload["productType"] == "INTRADAY"
    assert payload["validity"] == "DAY"


def test_a_sell_is_sent_as_sell(nifty_option):
    order = _limit_order(nifty_option, Price.from_str("100.25"), side=OrderSide.SELL)
    assert _payload(nifty_option, order)["transactionType"] == "SELL"


def test_a_modify_payload_names_the_order_and_the_new_terms():
    payload = orders.modify_payload(
        order_id="112111182045", client_id="CLIENT1", quantity_units=130,
        price="24550.05", trigger_price="", validity="DAY", order_type="LIMIT",
    )
    assert payload["orderId"] == "112111182045"
    assert payload["quantity"] == "130"
    assert payload["price"] == "24550.05"
    assert payload["orderType"] == "LIMIT"


def test_an_ordinary_modify_omits_the_leg_name():
    """`legName` applies to BO and CO only. Sent empty the whole modify is
    refused -- measured against the sandbox, which rejected exactly this
    payload and accepted the same one without the empty keys."""
    payload = orders.modify_payload(
        order_id="1", client_id="C", quantity_units=65, price="1.00",
        trigger_price="", validity="DAY", order_type="LIMIT",
    )
    assert "legName" not in payload
    assert "triggerPrice" not in payload


def test_nothing_in_the_payload_is_a_float(nifty_option):
    """A float anywhere is a value that has already been through a C double."""
    for key, value in _payload(nifty_option).items():
        assert not isinstance(value, float), key


# -- reading Dhan's answers back ---------------------------------------------
#
# Every row below is a `documented-never-observed` fixture: this account has
# never traded, so no order, trade or position row has ever arrived here.
# The field names are Dhan's; the numbers are overridden per test to say what
# each one is about.

from datetime import UTC, datetime  # noqa: E402
from decimal import Decimal  # noqa: E402

from nautilus_trader.model.enums import OrderStatus, PositionSide  # noqa: E402
from nautilus_trader.model.identifiers import AccountId  # noqa: E402

from tests.dhan import corpus  # noqa: E402

ACCOUNT = AccountId("DHAN-CLIENT1")


def _order_row(**overrides):
    return dict(corpus.body("order_book_row")) | overrides


def test_a_dhan_timestamp_is_read_as_india_not_utc():
    """Dhan stamps with no zone, in IST. Read as UTC it lands five and a half
    hours early -- most of a session -- so a fill drops onto the wrong trading
    day and reconciliation compares two different days."""
    ns = orders.ist_to_ns("2021-11-24 13:33:03")
    assert datetime.fromtimestamp(ns / 1e9, UTC).isoformat() == "2021-11-24T08:03:03+00:00"


@pytest.mark.parametrize("stamp", ["", None])
def test_an_empty_timestamp_is_zero_not_an_error(stamp):
    """Dhan sends "" and null on a row that never reached the exchange."""
    assert orders.ist_to_ns(stamp) == 0


def test_every_documented_order_status_maps():
    """Dhan publishes seven."""
    assert set(orders.ORDER_STATUS) == {
        "TRANSIT", "PENDING", "REJECTED", "CANCELLED", "PART_TRADED",
        "TRADED", "EXPIRED",
    }
    assert orders.ORDER_STATUS["TRADED"] is OrderStatus.FILLED
    assert orders.ORDER_STATUS["PART_TRADED"] is OrderStatus.PARTIALLY_FILLED
    assert orders.ORDER_STATUS["TRANSIT"] is OrderStatus.SUBMITTED
    assert orders.ORDER_STATUS["PENDING"] is OrderStatus.ACCEPTED


def test_an_unknown_status_raises_rather_than_defaulting(nifty_option):
    """A status read as accepted when it means rejected leaves the engine
    waiting for a fill that is never coming."""
    with pytest.raises(ValueError, match="SOMETHING_NEW"):
        orders.order_status_report(
            _order_row(orderStatus="SOMETHING_NEW", quantity=65),
            nifty_option, ACCOUNT, UUID4(), 0,
        )


def test_an_order_report_counts_in_lots_not_units(nifty_option):
    """Dhan's `quantity` is units. Reporting it as the Nautilus quantity
    multiplies the position by the lot size."""
    report = orders.order_status_report(
        _order_row(quantity=130, filledQty=65, price=100.25, orderStatus="PART_TRADED"),
        nifty_option, ACCOUNT, UUID4(), 7,
    )
    assert report.quantity == Quantity.from_int(2)
    assert report.filled_qty == Quantity.from_int(1)
    assert report.price == Price.from_str("100.25")
    assert report.order_status is OrderStatus.PARTIALLY_FILLED


def test_a_quantity_that_is_not_whole_lots_raises(nifty_option):
    """Dhan and this package's snapshot of Dhan's own master disagree about
    the lot. A rounded answer is a position report that is quietly wrong, and
    the engine would trade the difference."""
    with pytest.raises(ValueError, match="65"):
        orders.order_status_report(
            _order_row(quantity=100), nifty_option, ACCOUNT, UUID4(), 0
        )


def test_the_client_order_id_comes_back_from_correlation_id(nifty_option):
    """Short enough to ride as itself, so the value on the wire is the id --
    but it is still matched against what we know rather than cast, because a
    correlationId Dhan generated itself looks exactly the same."""
    report = orders.order_status_report(
        _order_row(correlationId="O-1", quantity=65), nifty_option, ACCOUNT,
        UUID4(), 0, [ClientOrderId("O-1")],
    )
    assert report.client_order_id == ClientOrderId("O-1")


def test_a_row_with_no_correlation_id_still_reports(nifty_option):
    """An order placed from Dhan's own app carries none. Dropping the row
    would hide a position the account really holds -- the exact case
    reconciliation exists to catch."""
    report = orders.order_status_report(
        _order_row(correlationId="", quantity=65), nifty_option, ACCOUNT, UUID4(), 0
    )
    assert report.client_order_id is None


def test_a_zero_price_is_reported_as_absent(nifty_option):
    """Dhan sends `price` on a MARKET order and `averageTradedPrice` on an
    unfilled one regardless, both 0.0. Rendered literally that is a limit of
    zero and a fill at zero, neither of which any order had."""
    report = orders.order_status_report(
        _order_row(quantity=65, price=0.0), nifty_option, ACCOUNT, UUID4(), 0
    )
    assert report.price is None
    assert report.avg_px is None


def test_a_rejected_row_carries_dhan_s_own_reason(nifty_option):
    """The first thing looked at on a REJECTED row, and the only place Dhan
    says why."""
    report = orders.order_status_report(
        _order_row(orderStatus="REJECTED", quantity=65,
                   omsErrorDescription="RMS:Margin Exceeds"),
        nifty_option, ACCOUNT, UUID4(), 0,
    )
    assert report.cancel_reason == "RMS:Margin Exceeds"


def test_a_fill_report_uses_the_exchange_trade_id(nifty_option):
    """`exchangeTradeId` is the only per-fill identity Dhan gives. Keying on
    orderId instead collapses a partially filled order's fills into one and
    loses every fill after the first."""
    report = orders.fill_report(
        dict(corpus.body("trade_book_row")) | {"tradedQuantity": 65, "tradedPrice": 100.25},
        nifty_option, ACCOUNT, UUID4(), 0,
    )
    assert report.trade_id.value == "15112111182045"
    assert report.last_qty == Quantity.from_int(1)
    assert report.last_px == Price.from_str("100.25")


def test_a_fill_report_does_not_invent_a_commission(nifty_option):
    """GET /v2/trades carries no charge figure and the margin calculator
    returns `brokerage: 0.0`, so Dhan does not tell us. This package CAN
    model the charge, and putting that estimate here would launder our own
    number into a broker record."""
    report = orders.fill_report(
        dict(corpus.body("trade_book_row")) | {"tradedQuantity": 65},
        nifty_option, ACCOUNT, UUID4(), 0,
    )
    assert report.commission.currency.code == "INR"
    assert report.commission.as_decimal() == Decimal(0)


def test_a_short_position_reports_short_and_positive(nifty_option):
    """`netQty` is signed and Nautilus takes a side plus a magnitude. A
    negative Quantity is rejected outright, so the sign has to move."""
    report = orders.position_status_report(
        dict(corpus.body("position_row")) | {
            "netQty": -130, "positionType": "SHORT", "sellAvg": 100.25, "buyAvg": 0.0
        },
        nifty_option, ACCOUNT, UUID4(), 0,
    )
    assert report.position_side is PositionSide.SHORT
    assert report.quantity == Quantity.from_int(2)
    assert report.avg_px_open == Decimal("100.25")


def test_a_closed_position_reports_flat(nifty_option):
    """Dhan keeps a squared-off contract in the book at netQty 0 for the rest
    of the session. Reported as anything but flat, the engine would close a
    position that is already gone."""
    report = orders.position_status_report(
        dict(corpus.body("position_row")) | {"netQty": 0, "positionType": "CLOSED"},
        nifty_option, ACCOUNT, UUID4(), 0,
    )
    assert report.position_side is PositionSide.FLAT
    assert report.quantity == Quantity.from_int(0)


def test_a_long_position_reports_the_surviving_side_s_average(nifty_option):
    """A short was entered by SELLING, so reporting its buyAvg shows the price
    it is being closed at as the price it was opened at."""
    report = orders.position_status_report(
        dict(corpus.body("position_row")) | {
            "netQty": 65, "positionType": "LONG", "buyAvg": 12.5, "sellAvg": 99.0
        },
        nifty_option, ACCOUNT, UUID4(), 0,
    )
    assert report.avg_px_open == Decimal("12.5")


def test_an_unknown_position_type_raises(nifty_option):
    with pytest.raises(ValueError, match="positionType"):
        orders.position_status_report(
            dict(corpus.body("position_row")) | {"positionType": "SIDEWAYS"},
            nifty_option, ACCOUNT, UUID4(), 0,
        )


# -- the full documented order surface ---------------------------------------
#
# https://dhanhq.co/docs/v2/orders/ specifies four order types, five product
# types, two validities and the AMO window, request and response, field by
# field. That is the surface this adapter builds to.

from nautilus_trader.model.enums import OrderType, TriggerType  # noqa: E402
from nautilus_trader.model.orders import StopLimitOrder, StopMarketOrder  # noqa: E402


def _stop_limit(instrument, price="100.25", trigger="99.00"):
    return StopLimitOrder(
        trader_id=TraderId("TESTER-000"), strategy_id=StrategyId("S-001"),
        instrument_id=instrument.id, client_order_id=ClientOrderId("O-4"),
        order_side=OrderSide.SELL, quantity=Quantity.from_int(1),
        price=Price.from_str(price), trigger_price=Price.from_str(trigger),
        trigger_type=TriggerType.LAST_PRICE, init_id=UUID4(), ts_init=0,
        time_in_force=TimeInForce.DAY,
    )


def _stop_market(instrument, trigger="99.00"):
    return StopMarketOrder(
        trader_id=TraderId("TESTER-000"), strategy_id=StrategyId("S-001"),
        instrument_id=instrument.id, client_order_id=ClientOrderId("O-5"),
        order_side=OrderSide.SELL, quantity=Quantity.from_int(1),
        trigger_price=Price.from_str(trigger), trigger_type=TriggerType.LAST_PRICE,
        init_id=UUID4(), ts_init=0, time_in_force=TimeInForce.DAY,
    )


def test_the_payload_sends_no_field_dhan_does_not_document(nifty_option):
    """Everything sent is a field Dhan names. The reverse does NOT hold -- see
    the omission test below."""
    documented = set(corpus.body("order_placement_request"))
    assert set(_payload(nifty_option)) <= documented


def test_a_field_that_does_not_apply_is_OMITTED_not_sent_empty(nifty_option):
    """MEASURED, and it contradicts Dhan's own sample. The documented request
    structure sends `""` for the fields that do not apply -- `price`,
    `triggerPrice`, `disclosedQuantity`, `amoTime`, `boProfitValue`,
    `boStopLossValue`. Sent that way the order is REFUSED:

        400 {"errorType":"Input_Exception","errorCode":"DH-905",
             "errorMessage":"Missing required fields, bad values for parameters etc."}

    The identical payload with those keys omitted is accepted. Verified in
    Dhan's sandbox on 2026-09-08 by sending both and comparing; the empty
    strings are the only difference between the two.

    This adapter copied the documented sample, so before this test every order
    it built would have been refused, with an error message naming no field.
    """
    payload = _payload(nifty_option)
    assert "" not in payload.values()
    for absent in ("triggerPrice", "disclosedQuantity", "amoTime",
                   "boProfitValue", "boStopLossValue"):
        assert absent not in payload, absent


def test_a_market_order_omits_the_price_rather_than_emptying_it(nifty_option):
    payload = _payload(nifty_option, _market_order(nifty_option))
    assert "price" not in payload
    assert payload["orderType"] == "MARKET"


def test_every_documented_order_type_is_sendable(nifty_option):
    """LIMIT, MARKET, STOP_LOSS and STOP_LOSS_MARKET. All four are Dhan's."""
    assert set(orders.ORDER_TYPE.values()) == {
        "LIMIT", "MARKET", "STOP_LOSS", "STOP_LOSS_MARKET"
    }


def test_a_market_order_is_sent_as_market(nifty_option):
    """Dhan documents MARKET, so this adapter sends it. Note what Dhan then
    does with it -- see `place_payload` -- but that is the venue's behaviour to
    disclose, not a reason to refuse the caller's order."""
    assert _payload(nifty_option, _market_order(nifty_option))["orderType"] == "MARKET"


def test_a_stop_limit_carries_both_prices(nifty_option):
    """`triggerPrice` is conditionally required for SL-M and SL-L. Without it
    Dhan has no level to trigger on and the order is refused."""
    payload = _payload(nifty_option, _stop_limit(nifty_option))
    assert payload["orderType"] == "STOP_LOSS"
    assert payload["price"] == "100.25"
    assert payload["triggerPrice"] == "99.00"


def test_a_stop_market_carries_only_the_trigger(nifty_option):
    payload = _payload(nifty_option, _stop_market(nifty_option))
    assert payload["orderType"] == "STOP_LOSS_MARKET"
    assert payload["triggerPrice"] == "99.00"
    assert "price" not in payload


@pytest.mark.parametrize(
    "order_type",
    [OrderType.MARKET_IF_TOUCHED, OrderType.LIMIT_IF_TOUCHED,
     OrderType.TRAILING_STOP_MARKET, OrderType.TRAILING_STOP_LIMIT,
     OrderType.MARKET_TO_LIMIT],
)
def test_an_order_type_dhan_does_not_document_is_refused(order_type):
    """Dhan's order endpoint takes four. Mapping a fifth onto one of them
    sends an order the caller did not ask for."""
    with pytest.raises(Unsendable):
        orders.order_type_for(order_type)


def test_a_disclosed_quantity_is_sent_in_units_like_any_other(nifty_option):
    """Dhan counts units here too, and asks for more than 30% of the quantity.
    Sending lots would disclose a sixty-fifth of what was meant."""
    order = LimitOrder(
        trader_id=TraderId("TESTER-000"), strategy_id=StrategyId("S-001"),
        instrument_id=nifty_option.id, client_order_id=ClientOrderId("O-6"),
        order_side=OrderSide.BUY, quantity=Quantity.from_int(4),
        price=Price.from_str("100.25"), init_id=UUID4(), ts_init=0,
        time_in_force=TimeInForce.DAY, display_qty=Quantity.from_int(2),
    )
    payload = _payload(nifty_option, order)
    assert payload["quantity"] == "260"
    assert payload["disclosedQuantity"] == "130"


def test_the_correlation_id_limit_is_the_measured_one_not_the_documented_one():
    """MEASURED. Dhan documents 30 characters. The API accepts 25 and refuses
    26 -- binary-searched in the sandbox on 2026-09-08, with DH-905 and no
    indication which field was at fault.

    This matters more than five characters sounds: a default Nautilus
    ClientOrderId is 27, so it does NOT fit, and this adapter's own comment
    said it fit "with three to spare".
    """
    assert orders.CORRELATION_ID_MAX_LEN == 25
    assert len("O-19700101-000000-001-000-1") == 27


def test_an_after_market_order_names_its_window(nifty_option):
    """`amoTime` is conditionally required once `afterMarketOrder` is true, and
    Dhan rejects a value outside its four."""
    payload = orders.place_payload(
        order=_limit_order(nifty_option, Price.from_str("100.25")),
        instrument=nifty_option, security_id="43492", client_id="CLIENT1",
        product_type="INTRADAY", after_market_order=True, amo_time="OPEN_30",
    )
    assert payload["afterMarketOrder"] is True
    assert payload["amoTime"] == "OPEN_30"


def test_an_unknown_amo_window_is_refused(nifty_option):
    with pytest.raises(Unsendable, match="amoTime"):
        orders.place_payload(
            order=_limit_order(nifty_option, Price.from_str("100.25")),
            instrument=nifty_option, security_id="43492", client_id="CLIENT1",
            product_type="INTRADAY", after_market_order=True, amo_time="WHENEVER",
        )


def test_an_unknown_product_type_is_refused(nifty_option):
    """Dhan documents six. A seventh is a typo that the venue answers with its
    generic Input_Exception, which says nothing useful."""
    with pytest.raises(Unsendable, match="productType"):
        orders.place_payload(
            order=_limit_order(nifty_option, Price.from_str("100.25")),
            instrument=nifty_option, security_id="43492", client_id="CLIENT1",
            product_type="SWING",
        )


def test_the_modify_payload_sends_no_field_dhan_does_not_document(nifty_option):
    documented = set(corpus.body("order_modification_request"))
    payload = orders.modify_payload(
        order_id="1", client_id="C", quantity_units=65, price="1.00",
        trigger_price="2.00", validity="DAY", order_type="LIMIT",
        leg_name="TARGET_LEG",
    )
    assert set(payload) <= documented


def test_a_modify_can_name_the_leg_it_is_changing():
    """`legName` is conditionally required for BO and CO, and Dhan addresses a
    leg by name -- ENTRY_LEG, TARGET_LEG, STOP_LOSS_LEG."""
    payload = orders.modify_payload(
        order_id="1", client_id="C", quantity_units=65, price="1.00",
        trigger_price="", validity="DAY", order_type="LIMIT",
        leg_name="TARGET_LEG",
    )
    assert payload["legName"] == "TARGET_LEG"


def test_an_unknown_leg_name_is_refused():
    with pytest.raises(Unsendable, match="legName"):
        orders.modify_payload(
            order_id="1", client_id="C", quantity_units=65, price="1.00",
            trigger_price="", validity="DAY", order_type="LIMIT",
            leg_name="MIDDLE_LEG",
        )


def test_a_stop_order_row_reports_its_trigger(nifty_option):
    """REGRESSION. Nautilus refuses a report that carries a trigger price and
    no trigger TYPE, and this converter passed one without the other from the
    day it was written. Nothing caught it because every fixture row has
    `triggerPrice: 0.0` -- an ordinary limit order has no trigger, so the
    branch never ran until a stop order existed.

    Dhan does not publish what its stops watch, so the type is DEFAULT --
    "the venue's own" -- rather than a claim of last-price or mark-price that
    this adapter has no basis for.
    """
    report = orders.order_status_report(
        _order_row(quantity=65, orderType="STOP_LOSS", price=100.25, triggerPrice=99.0),
        nifty_option, ACCOUNT, UUID4(), 0,
    )
    assert report.trigger_price == Price.from_str("99.0")
    assert report.trigger_type.name == "DEFAULT"


def test_a_row_with_no_trigger_claims_none(nifty_option):
    report = orders.order_status_report(
        _order_row(quantity=65), nifty_option, ACCOUNT, UUID4(), 0
    )
    assert report.trigger_price is None
    assert report.trigger_type.name == "NO_TRIGGER"


@pytest.mark.parametrize("sentinel", ["0001-01-01 00:00:00", "0001-01-01"])
def test_dhan_s_zero_date_sentinel_is_unset_not_year_one(sentinel):
    """MEASURED. An order that never reached the exchange comes back with
    `exchangeTime: "0001-01-01 00:00:00"` -- a sentinel, not a time. Parsed
    literally it is -62135618008000000000 nanoseconds, and Nautilus ACCEPTS a
    negative timestamp without complaint, so the report would carry a date in
    year 1 and nothing anywhere would notice.

    Seen on a real rejected order in Dhan's sandbox, 2026-09-08. The docs show
    `null` for these fields; the API sends the sentinel.
    """
    assert orders.ist_to_ns(sentinel) == 0


def test_a_real_timestamp_still_parses():
    """The guard must not swallow ordinary values."""
    assert orders.ist_to_ns("2026-09-08 06:12:15") > 0


def test_a_working_order_reports_no_cancel_reason(nifty_option):
    """MEASURED. `omsErrorDescription` is not always an error. On a healthy
    resting order in Dhan's sandbox it reads "CONFIRMED", and this converter
    put it straight into `cancel_reason` -- so a working order reported the
    reason it was cancelled as "CONFIRMED", which is neither true nor a
    reason.

    The field is only meaningful once the order is actually dead.
    """
    report = orders.order_status_report(
        _order_row(quantity=65, orderStatus="PENDING",
                   omsErrorDescription="CONFIRMED"),
        nifty_option, ACCOUNT, UUID4(), 0,
    )
    assert report.cancel_reason is None


def test_a_rejected_order_still_carries_its_reason(nifty_option):
    """The guard must not swallow the case the field exists for."""
    report = orders.order_status_report(
        _order_row(quantity=65, orderStatus="REJECTED",
                   omsErrorDescription="RMS:Order Price needs to be Circuit Limits"),
        nifty_option, ACCOUNT, UUID4(), 0,
    )
    assert "Circuit Limits" in report.cancel_reason


# -- the invariant that would have caught this once, instead of four times ----


def test_no_payload_builder_ever_sends_an_empty_string(nifty_option):
    """MEASURED, and the same defect four times over.

    Dhan refuses a request carrying `""` for a field that does not apply --
    DH-905, naming nothing. It is refused on POST /v2/orders and on
    PUT /v2/orders/{id} alike, and both were sent that way because Dhan's own
    documented samples show empty strings.

    Fixing the placement builder and leaving the others is exactly what
    happened, and the modify was then rejected by the live sandbox. So this is
    an invariant over ALL of them rather than a case per builder: a new
    payload field that does not apply must be omitted, and this test fails the
    moment one is emptied instead.
    """
    from nautilus_india import dhan as _  # noqa: F401
    from nautilus_india.dhan import forever_orders, super_orders

    payloads = {
        "place": _payload(nifty_option),
        "place_market": _payload(nifty_option, _market_order(nifty_option)),
        "modify": orders.modify_payload(
            order_id="1", client_id="C", quantity_units=65, price="1.00",
            trigger_price="", validity="DAY", order_type="LIMIT"),
        "modify_with_leg": orders.modify_payload(
            order_id="1", client_id="C", quantity_units=65, price="1.00",
            trigger_price="2.00", validity="DAY", order_type="LIMIT",
            leg_name="TARGET_LEG"),
        "super_modify_target": super_orders.modify_payload(
            order_id="1", client_id="C", leg_name="TARGET_LEG", target_price="1"),
        "super_modify_stop": super_orders.modify_payload(
            order_id="1", client_id="C", leg_name="STOP_LOSS_LEG",
            stop_loss_price="1", trailing_jump="2"),
        "forever_modify": forever_orders.modify_payload(
            order_id="1", client_id="C", order_flag="SINGLE", order_type="LIMIT",
            leg_name="TARGET_LEG", quantity_units=1, price="1",
            disclosed_units=0, trigger_price="1", validity="DAY"),
    }
    empty = {name: [k for k, v in body.items() if v == ""]
             for name, body in payloads.items()}
    assert not any(empty.values()), f"empty strings on the wire: {empty}"
