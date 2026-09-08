"""Super orders: entry, target and stop loss as one request.

WHAT MAKES THEM DIFFERENT FROM THREE ORDERS. One `orderId` covers all three
legs, and every leg in `legDetails` carries the ENTRY's id -- so anything
keying its own orders on `orderId` alone collapses three into one. Dhan's own
modify and cancel take the pair `(orderId, legName)`, and this module does the
same.

Built to https://dhanhq.co/docs/v2/super-order/, which specifies every request
and response. Nothing here has been sent to Dhan: this account cannot place an
order without a whitelisted static IP.
"""

import pytest
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.enums import OrderSide, OrderStatus, TimeInForce, TriggerType
from nautilus_trader.model.identifiers import AccountId, ClientOrderId, StrategyId, TraderId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import LimitOrder, StopMarketOrder

from nautilus_india.dhan import super_orders
from nautilus_india.dhan.orders import Unsendable
from tests.dhan import corpus

ACCOUNT = AccountId("DHAN-CLIENT1")


def _entry(instrument, price="1500.00"):
    return LimitOrder(
        trader_id=TraderId("TESTER-000"), strategy_id=StrategyId("S-001"),
        instrument_id=instrument.id, client_order_id=ClientOrderId("O-1"),
        order_side=OrderSide.BUY, quantity=Quantity.from_int(1),
        price=Price.from_str(price), init_id=UUID4(), ts_init=0,
        time_in_force=TimeInForce.DAY,
    )


def _target(instrument, price="1600.00"):
    return LimitOrder(
        trader_id=TraderId("TESTER-000"), strategy_id=StrategyId("S-001"),
        instrument_id=instrument.id, client_order_id=ClientOrderId("O-2"),
        order_side=OrderSide.SELL, quantity=Quantity.from_int(1),
        price=Price.from_str(price), init_id=UUID4(), ts_init=0,
        time_in_force=TimeInForce.DAY,
    )


def _stop(instrument, trigger="1400.00"):
    return StopMarketOrder(
        trader_id=TraderId("TESTER-000"), strategy_id=StrategyId("S-001"),
        instrument_id=instrument.id, client_order_id=ClientOrderId("O-3"),
        order_side=OrderSide.SELL, quantity=Quantity.from_int(1),
        trigger_price=Price.from_str(trigger), trigger_type=TriggerType.LAST_PRICE,
        init_id=UUID4(), ts_init=0, time_in_force=TimeInForce.DAY,
    )


def _payload(instrument, **kwargs):
    return super_orders.place_payload(
        entry=kwargs.pop("entry", None) or _entry(instrument),
        target=kwargs.pop("target", None) or _target(instrument),
        stop_loss=kwargs.pop("stop_loss", None) or _stop(instrument),
        instrument=instrument, security_id="43492", client_id="CLIENT1",
        product_type=kwargs.pop("product_type", "INTRADAY"), **kwargs,
    )


# -- placing -----------------------------------------------------------------


def test_the_payload_matches_dhan_s_documented_request_field_for_field(nifty_option):
    documented = set(corpus.body("super_order_request"))
    assert set(_payload(nifty_option)) == documented


def test_all_three_legs_ride_in_one_request(nifty_option):
    """That is what a super order IS. Sent as three orders there is no OCO
    between them, and a filled target leaves the stop working."""
    payload = _payload(nifty_option)
    assert payload["price"] == "1500.00"
    assert payload["targetPrice"] == "1600.00"
    assert payload["stopLossPrice"] == "1400.00"


def test_the_quantity_is_the_entry_s_in_units(nifty_option):
    """One quantity covers the whole structure; the legs do not carry their
    own. Sending lots would order a sixty-fifth of what was meant."""
    assert _payload(nifty_option)["quantity"] == "65"


def test_no_trailing_stop_is_a_jump_of_zero_not_an_absent_field(nifty_option):
    """`trailingJump` is marked required, and Dhan reads an omitted or zero
    one as 'do not trail'. Leaving the field out is the same request with less
    said, so it is stated."""
    assert _payload(nifty_option)["trailingJump"] == "0"


def test_a_trailing_jump_is_sent_when_asked_for(nifty_option):
    assert _payload(nifty_option, trailing_jump="10.00")["trailingJump"] == "10.00"


def test_a_super_order_takes_limit_or_market_only(nifty_option):
    """No stop entry: Dhan documents LIMIT and MARKET for this endpoint, and a
    STOP_LOSS entry would be silently something else."""
    with pytest.raises(Unsendable):
        _payload(nifty_option, entry=_stop(nifty_option))


def test_a_product_type_a_super_order_cannot_carry_is_refused(nifty_option):
    """CNC, INTRADAY, MARGIN and MTF. CO and BO are absent because a super
    order IS the bracket -- asking for one inside the other is not a thing."""
    with pytest.raises(Unsendable, match="productType"):
        _payload(nifty_option, product_type="BO")


def test_a_target_below_the_stop_is_refused_on_a_buy(nifty_option):
    """A bracket whose target is under its stop is not a bracket. Dhan would
    take it and one leg would fire immediately, closing a position the caller
    had just opened."""
    with pytest.raises(Unsendable, match="target"):
        _payload(nifty_option, target=_target(nifty_option, "1300.00"))


def test_a_target_above_the_stop_is_refused_on_a_sell(nifty_option):
    """The same check, mirrored. A short brackets the other way up."""
    entry = LimitOrder(
        trader_id=TraderId("TESTER-000"), strategy_id=StrategyId("S-001"),
        instrument_id=nifty_option.id, client_order_id=ClientOrderId("O-9"),
        order_side=OrderSide.SELL, quantity=Quantity.from_int(1),
        price=Price.from_str("1500.00"), init_id=UUID4(), ts_init=0,
        time_in_force=TimeInForce.DAY,
    )
    with pytest.raises(Unsendable, match="target"):
        super_orders.place_payload(
            entry=entry, target=_target(nifty_option, "1600.00"),
            stop_loss=_stop(nifty_option, "1400.00"), instrument=nifty_option,
            security_id="43492", client_id="CLIENT1", product_type="INTRADAY",
        )


def test_the_legs_must_face_the_other_way_from_the_entry(nifty_option):
    """A target on the same side as the entry doubles the position instead of
    closing it."""
    with pytest.raises(Unsendable, match="side"):
        super_orders.place_payload(
            entry=_entry(nifty_option), target=_entry(nifty_option),
            stop_loss=_stop(nifty_option), instrument=nifty_option,
            security_id="43492", client_id="CLIENT1", product_type="INTRADAY",
        )


# -- modifying, per leg ------------------------------------------------------


def test_an_entry_leg_modify_carries_the_whole_order(nifty_option):
    documented = set(corpus.body("super_order_modify_entry_request"))
    payload = super_orders.modify_payload(
        order_id="112111182045", client_id="CLIENT1", leg_name="ENTRY_LEG",
        order_type="LIMIT", quantity_units=40, price="1300",
        target_price="1450", stop_loss_price="1350", trailing_jump="20",
    )
    assert set(payload) == documented


def test_a_target_leg_modify_carries_only_the_target(nifty_option):
    """Sending the entry's fields too would ask to change terms the venue has
    already fixed -- once the entry is TRADED they cannot move at all."""
    documented = set(corpus.body("super_order_modify_target_request"))
    payload = super_orders.modify_payload(
        order_id="112111182045", client_id="CLIENT1", leg_name="TARGET_LEG",
        target_price="1450",
    )
    assert set(payload) == documented


def test_a_stop_leg_modify_always_states_its_trailing_jump():
    """Omitted or zero CANCELS the trail, and there is no value meaning
    'leave it alone'. So the caller's intent is always spelled out."""
    documented = set(corpus.body("super_order_modify_stop_request"))
    payload = super_orders.modify_payload(
        order_id="112111182045", client_id="CLIENT1", leg_name="STOP_LOSS_LEG",
        stop_loss_price="1350", trailing_jump="20",
    )
    assert set(payload) == documented
    assert payload["trailingJump"] == "20"


def test_an_unknown_leg_is_refused():
    with pytest.raises(Unsendable, match="legName"):
        super_orders.modify_payload(
            order_id="1", client_id="C", leg_name="MIDDLE_LEG", target_price="1"
        )


# -- cancelling --------------------------------------------------------------


def test_cancelling_a_leg_addresses_the_pair():
    assert super_orders.cancel_path("112111182045", "TARGET_LEG") == (
        "/v2/super/orders/112111182045/TARGET_LEG"
    )


def test_cancelling_the_entry_leg_cancels_everything():
    """Dhan: 'Cancelling main order ID cancels all legs.' The entry leg IS the
    main order id, so this is the whole-structure cancel."""
    assert super_orders.cancel_path("1", "ENTRY_LEG").endswith("/1/ENTRY_LEG")


def test_the_irreversibility_of_a_leg_cancel_is_stated():
    """Dhan: 'If particular target or stop loss leg is cancelled, then the
    same cannot be added again.' A caller has to be able to read that without
    going to the docs, because nothing in the API will stop them."""
    assert "cannot be added again" in super_orders.LEG_CANCEL_IS_IRREVERSIBLE


# -- reading the super order book -------------------------------------------


def _rows():
    """Dhan's sample rows, addressed to the option these tests use.

    The sample is an equity, so its quantity of 10 is not a whole number of a
    NIFTY lot -- the quantity is restated in lots-worth of units rather than
    the guard in `lots_for` being loosened, because that guard is the one
    stopping a rounded position report.
    """
    rows = corpus.body("super_order_row")
    return [
        dict(row) | {"securityId": "43492", "exchangeSegment": "NSE_FNO",
                     "quantity": 65, "remainingQuantity": 65, "price": 1500.00}
        for row in rows
    ]


def test_the_book_yields_one_report_per_leg(nifty_option):
    """Three legs, three reports. One report for the parent would hide the
    target and the stop, which are the orders actually resting."""
    reports = super_orders.reports_from(_rows(), lambda row: nifty_option, ACCOUNT, 0)
    assert len(reports) == 3
    assert {r.order_status for r in reports} == {OrderStatus.ACCEPTED}


def test_each_leg_gets_an_id_of_its_own(nifty_option):
    """Every leg carries the ENTRY's orderId, so reports keyed on it alone
    would collide and two of the three would be lost. Dhan addresses a leg by
    the pair, and so does the id built here."""
    reports = super_orders.reports_from(_rows(), lambda row: nifty_option, ACCOUNT, 0)
    ids = {r.venue_order_id.value for r in reports}
    assert ids == {
        "5925022734212", "5925022734212:TARGET_LEG", "5925022734212:STOP_LOSS_LEG"
    }


def test_a_leg_report_faces_the_other_way_from_the_entry(nifty_option):
    reports = {r.venue_order_id.value: r for r in
               super_orders.reports_from(_rows(), lambda row: nifty_option, ACCOUNT, 0)}
    assert reports["5925022734212"].order_side is OrderSide.BUY
    assert reports["5925022734212:TARGET_LEG"].order_side is OrderSide.SELL


def test_a_leg_price_is_read_from_the_leg_not_the_parent(nifty_option):
    reports = {r.venue_order_id.value: r for r in
               super_orders.reports_from(_rows(), lambda row: nifty_option, ACCOUNT, 0)}
    assert reports["5925022734212"].price == Price.from_str("1500.00")
    assert reports["5925022734212:TARGET_LEG"].price == Price.from_str("1550.00")
    assert reports["5925022734212:STOP_LOSS_LEG"].price == Price.from_str("1400.00")


def test_a_row_whose_instrument_is_unknown_is_skipped(nifty_option):
    reports = super_orders.reports_from(_rows(), lambda row: None, ACCOUNT, 0)
    assert reports == []


@pytest.mark.parametrize("spelling", ["totalQuatity", "totalQuantity"])
def test_either_spelling_of_the_leg_quantity_is_read(spelling, nifty_option):
    """Dhan's DOCS spell it `totalQuatity`. Whether the API does is unverified
    and unverifiable here -- this account has never placed a super order, so
    the book is empty and the field has never arrived.

    Assuming either way is unsafe: `availabelBalance` on /v2/fundlimit is
    misspelled in the LIVE API, captured rather than inferred, so Dhan does
    both. Reading one spelling and being wrong yields a leg for no quantity,
    which reads as a bracket with nothing in it.
    """
    rows = _rows()
    for leg in rows[0]["legDetails"]:
        leg.pop("totalQuatity", None)
        leg[spelling] = 130
    reports = {r.venue_order_id.value: r for r in
               super_orders.reports_from(rows, lambda row: nifty_option, ACCOUNT, 0)}
    assert reports["5925022734212:TARGET_LEG"].quantity == Quantity.from_int(2)
