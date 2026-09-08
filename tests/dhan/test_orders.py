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


def test_the_client_order_id_rides_in_correlation_id():
    """The only field that round-trips: it comes back on GET /v2/orders and on
    the order-update socket, so it is the only way to match a fill to the
    order that caused it."""
    got = orders.correlation_id(ClientOrderId("O-19700101-000000-001-000-1"))
    assert got == "O-19700101-000000-001-000-1"
    assert len(got) <= 30


def test_an_id_too_long_for_dhan_is_refused_not_truncated():
    """Dhan caps correlationId at 30 characters. A truncated id round-trips as
    a DIFFERENT id, so the fill it returns on matches no order -- which reads
    as an unexplained position rather than as a bug."""
    with pytest.raises(Unsendable, match="30"):
        orders.correlation_id(ClientOrderId("O-" + "9" * 40))


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


def test_an_ordinary_modify_sends_an_empty_leg_name():
    """`legName` applies to BO and CO only, but the field is Dhan's and a
    modify replaces the order's terms -- so it is sent empty rather than
    omitted, which is a payload Dhan does not recognise."""
    payload = orders.modify_payload(
        order_id="1", client_id="C", quantity_units=65, price="1.00",
        trigger_price="", validity="DAY", order_type="LIMIT",
    )
    assert payload["legName"] == ""


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
    report = orders.order_status_report(
        _order_row(correlationId="O-1", quantity=65), nifty_option, ACCOUNT, UUID4(), 0
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


def test_the_payload_matches_dhan_s_documented_request_field_for_field(nifty_option):
    """The documented request structure is the field list to build against.
    Dhan validates in order -- quantity, then the IP, then the instrument -- so
    a payload missing a required field fails with 'quantity is required' and
    never reaches the check that would have named the real problem."""
    documented = set(corpus.body("order_placement_request"))
    assert set(_payload(nifty_option)) == documented


def test_every_documented_order_type_is_sendable(nifty_option):
    """LIMIT, MARKET, STOP_LOSS and STOP_LOSS_MARKET. All four are Dhan's."""
    assert set(orders.ORDER_TYPE.values()) == {
        "LIMIT", "MARKET", "STOP_LOSS", "STOP_LOSS_MARKET"
    }


def test_a_market_order_is_sent_as_market(nifty_option):
    """Dhan documents MARKET, so this adapter sends it. Note what Dhan then
    does with it -- see `place_payload` -- but that is the venue's behaviour to
    disclose, not a reason to refuse the caller's order."""
    payload = _payload(nifty_option, _market_order(nifty_option))
    assert payload["orderType"] == "MARKET"
    # Documented as an empty string on a market order, not a zero: a zero is a
    # price, and no order was placed at one.
    assert payload["price"] == ""
    assert payload["triggerPrice"] == ""


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
    assert payload["price"] == ""


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


def test_no_disclosed_quantity_is_an_empty_string(nifty_option):
    """Dhan's own sample sends "" for the fields that do not apply."""
    assert _payload(nifty_option)["disclosedQuantity"] == ""


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


def test_the_modify_payload_matches_dhan_s_documented_request(nifty_option):
    documented = set(corpus.body("order_modification_request"))
    payload = orders.modify_payload(
        order_id="1", client_id="C", quantity_units=65, price="1.00",
        trigger_price="", validity="DAY", order_type="LIMIT",
    )
    assert set(payload) == documented


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
