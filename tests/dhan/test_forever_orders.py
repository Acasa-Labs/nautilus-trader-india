"""Forever orders: Dhan's Good-Till-Triggered, and the only thing that rests.

`/v2/orders` takes DAY and IOC and nothing else, so an order meant to outlive
the session has to be a forever order. `orderFlag` decides whether it is one
resting order (SINGLE) or a pair where either cancels the other (OCO).

Built to https://dhanhq.co/docs/v2/forever/, which specifies every request and
response. Nothing here has been sent to Dhan.
"""

import pytest
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.enums import OrderSide, OrderStatus, TimeInForce, TriggerType
from nautilus_trader.model.identifiers import AccountId, ClientOrderId, StrategyId, TraderId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import LimitOrder, StopLimitOrder

from nautilus_india.dhan import forever_orders
from nautilus_india.dhan.orders import Unsendable
from tests.dhan import corpus

ACCOUNT = AccountId("DHAN-CLIENT1")


def _stop_limit(instrument, price="1428.00", trigger="1427.00", cid="O-1"):
    return StopLimitOrder(
        trader_id=TraderId("TESTER-000"), strategy_id=StrategyId("S-001"),
        instrument_id=instrument.id, client_order_id=ClientOrderId(cid),
        order_side=OrderSide.BUY, quantity=Quantity.from_int(1),
        price=Price.from_str(price), trigger_price=Price.from_str(trigger),
        trigger_type=TriggerType.LAST_PRICE, init_id=UUID4(), ts_init=0,
        time_in_force=TimeInForce.GTC,
    )


def _payload(instrument, **kwargs):
    return forever_orders.place_payload(
        order=kwargs.pop("order", None) or _stop_limit(instrument),
        instrument=instrument, security_id="43492", client_id="CLIENT1",
        product_type=kwargs.pop("product_type", "CNC"), **kwargs,
    )


# -- placing -----------------------------------------------------------------


def test_the_payload_matches_dhan_s_documented_request_field_for_field(nifty_option):
    documented = set(corpus.body("forever_order_request"))
    assert set(_payload(nifty_option, second_leg=_stop_limit(
        nifty_option, "1420.00", "1419.00", "O-2"))) == documented


def test_a_single_forever_order_carries_one_price_and_one_trigger(nifty_option):
    payload = _payload(nifty_option)
    assert payload["orderFlag"] == "SINGLE"
    assert payload["price"] == "1428.00"
    assert payload["triggerPrice"] == "1427.00"


def test_a_single_order_leaves_the_oco_fields_empty(nifty_option):
    """`price1`, `triggerPrice1` and `quantity1` are the OCO's second leg.
    Filling them on a SINGLE would ask for an order that was not requested."""
    payload = _payload(nifty_option)
    assert payload["price1"] == ""
    assert payload["triggerPrice1"] == ""
    assert payload["quantity1"] == ""


def test_a_second_leg_makes_it_an_oco(nifty_option):
    """Either leg firing cancels the other. That is the whole point, and it is
    what a caller cannot build out of two separate forever orders."""
    payload = _payload(nifty_option,
                       second_leg=_stop_limit(nifty_option, "1420.00", "1419.00", "O-2"))
    assert payload["orderFlag"] == "OCO"
    assert payload["price1"] == "1420.00"
    assert payload["triggerPrice1"] == "1419.00"
    assert payload["quantity1"] == "65"


def test_a_forever_order_needs_a_trigger(nifty_option):
    """`triggerPrice` is required: a forever order is a GOOD TILL TRIGGERED
    order, and without a trigger there is nothing for it to wait for."""
    plain = LimitOrder(
        trader_id=TraderId("TESTER-000"), strategy_id=StrategyId("S-001"),
        instrument_id=nifty_option.id, client_order_id=ClientOrderId("O-8"),
        order_side=OrderSide.BUY, quantity=Quantity.from_int(1),
        price=Price.from_str("100.00"), init_id=UUID4(), ts_init=0,
        time_in_force=TimeInForce.GTC,
    )
    with pytest.raises(Unsendable, match="trigger"):
        _payload(nifty_option, order=plain)


def test_only_the_delivery_product_types_can_rest(nifty_option):
    """CNC and MTF. An INTRADAY forever order is a contradiction -- it cannot
    outlive the day it is named after -- and Dhan documents neither it nor
    MARGIN for this endpoint."""
    for product in ("INTRADAY", "MARGIN", "CO", "BO"):
        with pytest.raises(Unsendable, match="productType"):
            _payload(nifty_option, product_type=product)


def test_the_quantity_is_in_units_like_everywhere_else(nifty_option):
    assert _payload(nifty_option)["quantity"] == "65"


# -- modifying and cancelling ------------------------------------------------


def test_the_modify_payload_matches_dhan_s_documented_request(nifty_option):
    documented = set(corpus.body("forever_order_modify_request"))
    payload = forever_orders.modify_payload(
        order_id="5132208051112", client_id="CLIENT1", order_flag="SINGLE",
        order_type="LIMIT", leg_name="TARGET_LEG", quantity_units=15,
        price="1421", disclosed_units=1, trigger_price="1420", validity="DAY",
    )
    assert set(payload) == documented


def test_a_forever_leg_name_is_not_a_super_order_leg_name():
    """They share the words and not the meaning. For a forever order
    TARGET_LEG is a SINGLE and an OCO's FIRST leg, STOP_LOSS_LEG is an OCO's
    second, and there is no ENTRY_LEG to send at all."""
    with pytest.raises(Unsendable, match="legName"):
        forever_orders.modify_payload(
            order_id="1", client_id="C", order_flag="SINGLE", order_type="LIMIT",
            leg_name="ENTRY_LEG", quantity_units=1, price="1",
            disclosed_units=0, trigger_price="1", validity="DAY",
        )


def test_an_unknown_order_flag_is_refused():
    with pytest.raises(Unsendable, match="orderFlag"):
        forever_orders.modify_payload(
            order_id="1", client_id="C", order_flag="BOTH", order_type="LIMIT",
            leg_name="TARGET_LEG", quantity_units=1, price="1",
            disclosed_units=0, trigger_price="1", validity="DAY",
        )


def test_cancelling_addresses_the_order_by_id():
    assert forever_orders.cancel_path("5132208051112") == (
        "/v2/forever/orders/5132208051112"
    )


# -- reading the forever book ------------------------------------------------


def _rows():
    rows = corpus.body("forever_order_row")
    return [dict(row) | {"securityId": "43492", "exchangeSegment": "NSE_FNO",
                         "quantity": 65} for row in rows]


def test_the_book_yields_one_report_per_resting_order(nifty_option):
    reports = forever_orders.reports_from(_rows(), lambda row: nifty_option, ACCOUNT, 0)
    assert len(reports) == 1
    assert reports[0].quantity == Quantity.from_int(1)
    assert reports[0].price == Price.from_str("1428.00")
    assert reports[0].trigger_price == Price.from_str("1427.00")


def test_a_resting_order_reports_gtc_not_day(nifty_option):
    """It outlives the session by definition. Reported as DAY, the engine
    would expect it to vanish at the close and be surprised tomorrow."""
    reports = forever_orders.reports_from(_rows(), lambda row: nifty_option, ACCOUNT, 0)
    assert reports[0].time_in_force is TimeInForce.GTC


def test_the_order_flag_is_not_mistaken_for_an_order_type(nifty_option):
    """On the way OUT Dhan puts SINGLE or OCO in `orderType`, not the
    LIMIT/MARKET the order was sent as. Read literally that is an order type
    which does not exist."""
    reports = forever_orders.reports_from(_rows(), lambda row: nifty_option, ACCOUNT, 0)
    assert reports[0].order_type.name in {"LIMIT", "STOP_LIMIT"}


def test_confirm_is_a_status_this_book_can_return(nifty_option):
    """`CONFIRM` appears only here -- a forever order that is resting and
    waiting for its trigger. It is not in the ordinary order book's seven, and
    an unmapped status raises."""
    assert "CONFIRM" in forever_orders.FOREVER_ORDER_STATUS
    reports = forever_orders.reports_from(_rows(), lambda row: nifty_option, ACCOUNT, 0)
    assert reports[0].order_status is OrderStatus.ACCEPTED


def test_a_row_whose_instrument_is_unknown_is_skipped(nifty_option):
    assert forever_orders.reports_from(_rows(), lambda row: None, ACCOUNT, 0) == []
