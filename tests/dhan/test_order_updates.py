"""The order-update socket: what a message means, without any socket.

NEVER OBSERVED. `wss://api-order-update.dhan.co` is a production host, the
sandbox token is refused there, and the live token was deliberately not used
while another process records a live feed on that account. So every rule here
comes from Dhan's published message shape, and the parser is written to
survive being wrong about it -- unknown statuses raise rather than default,
and the two casings Dhan's own page disagrees on are both accepted.
"""

import json

import pytest
from nautilus_trader.model.enums import OrderSide, OrderStatus
from nautilus_trader.model.identifiers import ClientOrderId

from nautilus_india.dhan import order_updates
from tests.dhan import corpus


def _message(**overrides):
    body = json.loads(json.dumps(corpus.body("order_update_message")))
    body["Data"].update(overrides)
    return body


# -- authorising -------------------------------------------------------------


def test_the_auth_message_is_the_documented_shape():
    msg = order_updates.auth_message("CLIENT1", "tok")
    assert msg == {"LoginReq": {"MsgCode": 42, "ClientId": "CLIENT1",
                                "Token": "tok"}, "UserType": "SELF"}


def test_the_auth_message_carries_the_token_and_must_never_be_logged():
    """It is a credential in a frame. The redaction is what may be logged."""
    assert "tok" not in order_updates.redacted_auth_message("CLIENT1", "tok")
    assert "CLIENT1" in order_updates.redacted_auth_message("CLIENT1", "tok")


# -- reading a message -------------------------------------------------------


def test_a_message_that_is_not_an_order_alert_is_ignored():
    """The stream carries other traffic. Reading a heartbeat as an order would
    invent an event for an order that did not change."""
    assert order_updates.parse({"Type": "heartbeat"}) is None
    assert order_updates.parse({}) is None


def test_the_venue_order_id_comes_from_orderno_not_exchorderno():
    """`OrderNo` is Dhan's id and is what every REST endpoint addresses an
    order by. `ExchOrderNo` is the exchange's own and matches nothing here."""
    update = order_updates.parse(_message())
    assert update.venue_order_id.value == "1124091136546"


@pytest.mark.parametrize(
    ("sent", "expected"),
    [("Cancelled", OrderStatus.CANCELED), ("CANCELLED", OrderStatus.CANCELED),
     ("Pending", OrderStatus.ACCEPTED), ("PENDING", OrderStatus.ACCEPTED),
     ("Traded", OrderStatus.FILLED), ("TRADED", OrderStatus.FILLED),
     ("Rejected", OrderStatus.REJECTED), ("Transit", OrderStatus.SUBMITTED)],
)
def test_both_casings_of_a_status_are_read(sent, expected):
    """Dhan's own page disagrees with itself: the sample message says
    "Cancelled" and the parameter table on the same page lists the enum as
    CANCELLED, which is also what the REST order book returns. A reader keyed
    on one casing sees no status at all from the other."""
    assert order_updates.parse(_message(Status=sent)).order_status is expected


def test_an_unknown_status_raises_rather_than_defaulting():
    """The same rule as the REST reader. A status read as accepted when it
    means rejected leaves the engine waiting for a fill that is never coming."""
    with pytest.raises(ValueError, match="Sideways"):
        order_updates.parse(_message(Status="Sideways"))


@pytest.mark.parametrize(("sent", "side"),
                         [("B", OrderSide.BUY), ("S", OrderSide.SELL)])
def test_the_side_is_a_single_letter_here(sent, side):
    """REST says BUY and SELL; the socket says B and S. Same field, two
    vocabularies."""
    assert order_updates.parse(_message(TxnType=sent)).order_side is side


def test_the_traded_quantity_and_average_are_read():
    update = order_updates.parse(_message(Status="Traded", TradedQty=5,
                                          AvgTradedPrice=13.5))
    assert update.traded_quantity == 5
    assert str(update.average_price) == "13.5"


def test_a_correlation_id_maps_back_to_our_order():
    """The socket echoes whatever went out, which for a long ClientOrderId is
    the DERIVED id -- so this is the same recomputation the REST reports use,
    not a cast."""
    from nautilus_india.dhan.orders import correlation_id

    ours = ClientOrderId("O-19700101-000000-001-000-1")
    update = order_updates.parse(_message(CorrelationId=correlation_id(ours)))
    assert update.client_order_id_for([ours]) == ours


def test_an_empty_correlation_id_maps_to_nothing():
    """Dhan's own sample carries `""` -- an order placed from their app."""
    assert order_updates.parse(_message()).client_order_id_for([]) is None


def test_the_zero_date_sentinel_is_unset_here_too():
    """`ExpiryDate: "0001-01-01 00:00:00"` appears in Dhan's own sample."""
    update = order_updates.parse(_message(ExchOrderTime="0001-01-01 00:00:00"))
    assert update.ts_event == 0


def test_a_real_timestamp_is_read_as_india():
    update = order_updates.parse(_message(ExchOrderTime="2024-09-11 14:39:29"))
    from datetime import UTC, datetime
    assert datetime.fromtimestamp(update.ts_event / 1e9, UTC).isoformat() == \
        "2024-09-11T09:09:29+00:00"


def test_a_super_order_leg_is_identified():
    """`Remarks: "Super Order"` plus `LegNo` is how the stream says a message
    belongs to one leg of a bracket rather than to a plain order."""
    update = order_updates.parse(_message(Remarks="Super Order", LegNo=2))
    assert update.is_super_order_leg is True
    assert update.leg_number == 2


def test_an_ordinary_order_is_not_a_super_order_leg():
    assert order_updates.parse(_message(Remarks="NR")).is_super_order_leg is False


def test_the_security_id_and_segment_are_carried_for_routing():
    """A message names its instrument the way the feed does, not the way the
    REST order book does -- there is no `exchangeSegment` string here."""
    update = order_updates.parse(_message(SecurityId="14366", Exchange="NSE",
                                          Segment="E"))
    assert update.security_id == "14366"
    assert update.segment_code == 1        # NSE + E is NSE_EQ
