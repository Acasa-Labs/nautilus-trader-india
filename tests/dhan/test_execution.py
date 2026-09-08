"""The execution client. Driven through httpx.MockTransport; no socket opens.

THE EVIDENCE RULE IS THE POINT OF THIS FILE. Three outcomes, and the third is
the one that matters:

    never sent                  -> OrderDenied
    definitive venue rejection  -> OrderRejected
    may have reached the venue  -> NOTHING; reconcile

Emitting OrderRejected on a timeout reports a working order as dead, and the
position that follows is one nobody chose.
"""

import asyncio

import httpx
import pytest
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.messages import SubmitOrder
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import ClientOrderId, StrategyId, TraderId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import LimitOrder, MarketOrder

from nautilus_india.dhan.config import LIVE_ORDERS_ENV, DhanExecClientConfig
from nautilus_india.dhan.execution import DhanExecutionClient
from nautilus_india.dhan.http import DhanHttpClient
from tests.dhan import corpus

GENERATORS = (
    "generate_order_denied", "generate_order_rejected", "generate_order_submitted",
    "generate_order_accepted", "generate_order_canceled", "generate_order_updated",
    "generate_order_cancel_rejected", "generate_order_modify_rejected",
)


@pytest.fixture
def live_env(monkeypatch):
    """Both switches on. Without this fixture nothing is sent, by design."""
    monkeypatch.setenv(LIVE_ORDERS_ENV, "1")


@pytest.fixture
def recorded(monkeypatch):
    """Record the events the client generates instead of publishing them.

    The alternative -- a whole TradingNode -- would make these integration
    tests of Nautilus rather than tests of this adapter's decision.
    """
    seen = []
    for name in GENERATORS:
        monkeypatch.setattr(
            DhanExecutionClient, name,
            (lambda n: lambda self, **kwargs: seen.append((n, kwargs)))(name),
            raising=False,
        )
    return seen


def _names(recorded):
    return [name for name, _ in recorded]


async def _client(handler, instrument, *, live_orders=True):
    """A client whose transport is a handler and whose provider is preloaded.

    The provider is filled by hand rather than by downloading a 34 MB scrip
    master: this is a test of the order path, and a test that needs the
    network is a test that does not run.
    """
    clock = LiveClock()
    msgbus = MessageBus(trader_id=TraderId("TESTER-000"), clock=clock)
    cache = Cache()
    cache.add_instrument(instrument)
    client = DhanExecutionClient(
        loop=asyncio.get_running_loop(),
        name="DHAN",
        config=DhanExecClientConfig(
            client_id="CLIENT1", access_token="tok", live_orders=live_orders
        ),
        msgbus=msgbus,
        cache=cache,
        clock=clock,
    )
    client._http = DhanHttpClient(
        "CLIENT1", "tok",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    client._provider.add(instrument)
    client._provider._security_ids[instrument.id] = "43492"
    client._provider._by_security[("43492", 2)] = instrument.id
    return client


def _order(instrument, client_order_id="O-1"):
    return LimitOrder(
        trader_id=TraderId("TESTER-000"), strategy_id=StrategyId("S-001"),
        instrument_id=instrument.id, client_order_id=ClientOrderId(client_order_id),
        order_side=OrderSide.BUY, quantity=Quantity.from_int(1),
        price=Price.from_str("100.25"), init_id=UUID4(), ts_init=0,
        time_in_force=TimeInForce.GTC,
    )


def _submit(order):
    return SubmitOrder(
        trader_id=order.trader_id, strategy_id=order.strategy_id, order=order,
        command_id=UUID4(), ts_init=0,
    )


def _market_submit(instrument):
    order = MarketOrder(
        trader_id=TraderId("TESTER-000"), strategy_id=StrategyId("S-001"),
        instrument_id=instrument.id, client_order_id=ClientOrderId("O-3"),
        order_side=OrderSide.BUY, quantity=Quantity.from_int(1),
        init_id=UUID4(), ts_init=0,
    )
    return _submit(order)


def _acknowledging(recorder=None):
    async def handler(request):
        if recorder is not None:
            recorder.append(request)
        return httpx.Response(200, json=corpus.body("order_accepted"))
    return handler


# -- the three outcomes ------------------------------------------------------


async def test_an_accepted_order_is_submitted_then_accepted(
    nifty_option, live_env, recorded
):
    """THE REGRESSION THIS WHOLE ADAPTER SITS AFTER. Dhan's acknowledgement
    used to read as a rejection: two orders accepted at the exchange, two
    rejections recorded, zero fills, an empty position book."""
    client = await _client(_acknowledging(), nifty_option)
    await client._submit_order(_submit(_order(nifty_option)))
    assert _names(recorded) == ["generate_order_submitted", "generate_order_accepted"]
    assert recorded[-1][1]["venue_order_id"].value == "112111182198"


async def test_a_venue_rejection_is_reported_as_a_rejection(
    nifty_option, live_env, recorded
):
    """Definitive: Dhan understood the request and declined it, so nothing is
    working at the exchange."""
    async def handler(request):
        return httpx.Response(400, json=corpus.body("order_quantity_required"))

    client = await _client(handler, nifty_option)
    await client._submit_order(_submit(_order(nifty_option)))
    assert _names(recorded) == ["generate_order_submitted", "generate_order_rejected"]


async def test_a_timeout_emits_nothing_after_submitted(
    nifty_option, live_env, recorded
):
    """The order may be working at the exchange. Saying it is dead is how a
    position nobody chose gets opened."""
    async def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    client = await _client(handler, nifty_option)
    await client._submit_order(_submit(_order(nifty_option)))
    assert _names(recorded) == ["generate_order_submitted"]


async def test_an_unreadable_body_emits_nothing_either(
    nifty_option, live_env, recorded
):
    """Dhan answered, but with something we cannot interpret. We do not know
    whether it acted, so we must not say it did not."""
    async def handler(request):
        return httpx.Response(200, content=b"<html>gateway error</html>")

    client = await _client(handler, nifty_option)
    await client._submit_order(_submit(_order(nifty_option)))
    assert _names(recorded) == ["generate_order_submitted"]


# -- everything that stops before a request ----------------------------------


async def test_without_the_environment_variable_nothing_is_sent(
    nifty_option, recorded
):
    """Denied, not rejected: the request never left this process."""
    sent = []
    client = await _client(_acknowledging(sent), nifty_option)
    await client._submit_order(_submit(_order(nifty_option)))
    assert _names(recorded) == ["generate_order_denied"]
    assert sent == []


async def test_without_the_config_flag_nothing_is_sent(
    nifty_option, live_env, recorded
):
    sent = []
    client = await _client(_acknowledging(sent), nifty_option, live_orders=False)
    await client._submit_order(_submit(_order(nifty_option)))
    assert _names(recorded) == ["generate_order_denied"]
    assert sent == []


async def test_a_denial_names_both_switches(nifty_option, recorded):
    """An operator reading the event has to learn what is missing from it."""
    client = await _client(_acknowledging(), nifty_option, live_orders=False)
    await client._submit_order(_submit(_order(nifty_option)))
    reason = recorded[0][1]["reason"]
    assert "live_orders" in reason
    assert LIVE_ORDERS_ENV in reason


async def test_an_unknown_instrument_is_denied_before_any_request(
    nifty_option, live_env, recorded
):
    """Dhan answers 200 with empty data for an id it does not know, which
    reads as a quiet market rather than an error. So the id is resolved from
    the master or the order does not go."""
    sent = []
    client = await _client(_acknowledging(sent), nifty_option)
    client._provider._security_ids.clear()
    await client._submit_order(_submit(_order(nifty_option)))
    assert _names(recorded) == ["generate_order_denied"]
    assert sent == []


async def test_a_market_order_is_denied_before_any_request(
    nifty_option, live_env, recorded
):
    """Dhan would fill it at a market-protection limit the caller never saw."""
    sent = []
    client = await _client(_acknowledging(sent), nifty_option)
    await client._submit_order(_market_submit(nifty_option))
    assert _names(recorded) == ["generate_order_denied"]
    assert sent == []
    assert "market-protection" in recorded[0][1]["reason"]


async def test_an_unroutable_instrument_is_denied_before_any_request(
    nse_commodity_option, live_env, recorded
):
    """23,870 NSE commodity contracts are listed and none can be routed."""
    sent = []
    client = await _client(_acknowledging(sent), nse_commodity_option)
    client._provider._security_ids[nse_commodity_option.id] = "99999"
    await client._submit_order(_submit(_order(nse_commodity_option)))
    assert _names(recorded) == ["generate_order_denied"]
    assert sent == []


async def test_nothing_is_denied_after_being_submitted(
    nifty_option, live_env, recorded
):
    """A denial is a claim that nothing was sent, which stops being true the
    moment anything is -- and Nautilus will not accept DENIED after
    SUBMITTED either."""
    client = await _client(_acknowledging(), nifty_option)
    await client._submit_order(_submit(_order(nifty_option)))
    names = _names(recorded)
    assert "generate_order_denied" not in names[names.index("generate_order_submitted"):]


# -- the client-level failure ------------------------------------------------


async def test_an_ip_failure_degrades_the_client_not_just_the_order(
    nifty_option, live_env, recorded
):
    """Every later order fails identically, so retrying is 250 useless
    requests a minute. The second order is refused without a request."""
    sent = []

    async def handler(request):
        sent.append(request)
        return httpx.Response(400, json=corpus.body("order_invalid_ip"))

    client = await _client(handler, nifty_option)
    await client._submit_order(_submit(_order(nifty_option)))
    await client._submit_order(_submit(_order(nifty_option, "O-2")))
    assert len(sent) == 1, "the second order reached Dhan after an IP failure"
    assert client.is_degraded_by_ip is True
    assert _names(recorded)[-1] == "generate_order_denied"


# -- cancelling and modifying ------------------------------------------------

from nautilus_trader.execution.messages import (  # noqa: E402
    CancelAllOrders,
    CancelOrder,
    ModifyOrder,
)
from nautilus_trader.model.identifiers import VenueOrderId  # noqa: E402


def _cancel(instrument, venue_order_id="112111182045"):
    return CancelOrder(
        trader_id=TraderId("TESTER-000"), strategy_id=StrategyId("S-001"),
        instrument_id=instrument.id, client_order_id=ClientOrderId("O-1"),
        venue_order_id=VenueOrderId(venue_order_id) if venue_order_id else None,
        command_id=UUID4(), ts_init=0,
    )


def _modify(instrument, venue_order_id="112111182045"):
    return ModifyOrder(
        trader_id=TraderId("TESTER-000"), strategy_id=StrategyId("S-001"),
        instrument_id=instrument.id, client_order_id=ClientOrderId("O-1"),
        venue_order_id=VenueOrderId(venue_order_id) if venue_order_id else None,
        quantity=Quantity.from_int(2), price=Price.from_str("24550.05"),
        trigger_price=None, command_id=UUID4(), ts_init=0,
    )


def _cancelling(seen=None):
    async def handler(request):
        if seen is not None:
            seen.append((request.method, request.url.path))
        return httpx.Response(202, json=corpus.body("order_cancelled"))
    return handler


async def test_a_cancel_reaches_the_order_by_its_venue_id(
    nifty_option, live_env, recorded
):
    seen = []
    client = await _client(_cancelling(seen), nifty_option)
    await client._cancel_order(_cancel(nifty_option))
    assert seen == [("DELETE", "/v2/orders/112111182045")]
    assert _names(recorded) == ["generate_order_canceled"]


async def test_a_cancel_that_times_out_emits_nothing(nifty_option, live_env, recorded):
    """A cancel is a write. If we do not know whether it landed, saying the
    order is cancelled is how a live order gets forgotten."""
    async def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    client = await _client(handler, nifty_option)
    await client._cancel_order(_cancel(nifty_option))
    assert _names(recorded) == []


async def test_a_refused_cancel_is_a_cancel_rejection(nifty_option, live_env, recorded):
    async def handler(request):
        return httpx.Response(400, json=corpus.body("order_quantity_required"))

    client = await _client(handler, nifty_option)
    await client._cancel_order(_cancel(nifty_option))
    assert _names(recorded) == ["generate_order_cancel_rejected"]


async def test_a_cancel_with_no_venue_id_is_not_sent(nifty_option, live_env, recorded):
    """Dhan addresses a cancel by its own order id and has no other handle.
    Guessing one would cancel somebody else's order."""
    seen = []
    client = await _client(_cancelling(seen), nifty_option)
    await client._cancel_order(_cancel(nifty_option, venue_order_id=None))
    assert seen == []
    assert _names(recorded) == ["generate_order_cancel_rejected"]


async def test_cancel_all_cancels_each_working_order_it_knows_about(
    nifty_option, live_env, recorded
):
    """Dhan has no cancel-all on /v2/orders. `DELETE /v2/positions` exits
    POSITIONS, which is a different and much larger action -- it would close
    holdings the command never mentioned."""
    seen = []
    client = await _client(_cancelling(seen), nifty_option)
    client._open_venue_order_ids = lambda instrument_id, side: [
        VenueOrderId("1"), VenueOrderId("2")
    ]
    await client._cancel_all_orders(CancelAllOrders(
        trader_id=TraderId("TESTER-000"), strategy_id=StrategyId("S-001"),
        instrument_id=nifty_option.id, order_side=OrderSide.NO_ORDER_SIDE,
        command_id=UUID4(), ts_init=0,
    ))
    assert [path for _, path in seen] == ["/v2/orders/1", "/v2/orders/2"]


async def test_cancel_all_with_nothing_working_sends_nothing(
    nifty_option, live_env, recorded
):
    seen = []
    client = await _client(_cancelling(seen), nifty_option)
    await client._cancel_all_orders(CancelAllOrders(
        trader_id=TraderId("TESTER-000"), strategy_id=StrategyId("S-001"),
        instrument_id=nifty_option.id, order_side=OrderSide.NO_ORDER_SIDE,
        command_id=UUID4(), ts_init=0,
    ))
    assert seen == []


async def test_a_modify_sends_the_new_price_exactly(nifty_option, live_env, recorded):
    seen = {}

    async def handler(request):
        seen["method"], seen["path"] = request.method, request.url.path
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"orderId": "112111182045",
                                         "orderStatus": "TRANSIT"})

    client = await _client(handler, nifty_option)
    await client._modify_order(_modify(nifty_option))
    assert seen["method"] == "PUT"
    assert seen["path"] == "/v2/orders/112111182045"
    assert '"24550.05"' in seen["body"], seen["body"]
    # Lots on the way in, units on the wire: 2 x 65.
    assert '"130"' in seen["body"], seen["body"]
    assert _names(recorded) == ["generate_order_updated"]


async def test_a_modify_that_times_out_emits_nothing(nifty_option, live_env, recorded):
    async def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    client = await _client(handler, nifty_option)
    await client._modify_order(_modify(nifty_option))
    assert _names(recorded) == []


async def test_a_refused_modify_is_a_modify_rejection(nifty_option, live_env, recorded):
    async def handler(request):
        return httpx.Response(400, json=corpus.body("order_quantity_required"))

    client = await _client(handler, nifty_option)
    await client._modify_order(_modify(nifty_option))
    assert _names(recorded) == ["generate_order_modify_rejected"]


async def test_a_modify_with_no_venue_id_is_not_sent(nifty_option, live_env, recorded):
    seen = []
    client = await _client(_cancelling(seen), nifty_option)
    await client._modify_order(_modify(nifty_option, venue_order_id=None))
    assert seen == []
    assert _names(recorded) == ["generate_order_modify_rejected"]
