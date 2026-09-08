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


def test_a_market_order_is_refused_rather_than_silently_repriced(nifty_option):
    """Dhan converts an API MARKET order into a LIMIT order with
    market-protection pricing, so it fills at a limit the caller did not
    choose and cannot see."""
    with pytest.raises(Unsendable, match="market-protection"):
        _payload(nifty_option, _market_order(nifty_option))


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
        price="24550.05", validity="DAY",
    )
    assert payload["orderId"] == "112111182045"
    assert payload["quantity"] == "130"
    assert payload["price"] == "24550.05"
    assert payload["orderType"] == "LIMIT"


def test_a_modify_payload_sends_no_leg_name():
    """`legName` is required only for BO and CO orders, which this adapter
    does not place. Sending an empty one has never been tested at the venue."""
    payload = orders.modify_payload(
        order_id="1", client_id="C", quantity_units=65, price="1.00", validity="DAY"
    )
    assert "legName" not in payload


def test_nothing_in_the_payload_is_a_float(nifty_option):
    """A float anywhere is a value that has already been through a C double."""
    for key, value in _payload(nifty_option).items():
        assert not isinstance(value, float), key
