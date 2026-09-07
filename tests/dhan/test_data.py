"""The data client: packets in, Nautilus data out."""

from datetime import date
from decimal import Decimal

import pytest
from nautilus_trader.model.data import QuoteTick, TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.objects import Price

from nautilus_india.core.enums import Exchange, InstrumentClass
from nautilus_india.core.instruments import option_contract
from nautilus_india.core.symbology import ContractKey
from nautilus_india.dhan.data import quote_from_full, trade_from_ticker

CALL = ContractKey("NIFTY", InstrumentClass.OPTION, date(2026, 8, 4), Decimal("24550"), "CE")


@pytest.fixture
def instrument():
    return option_contract(CALL, Exchange.NSE)


def test_a_ticker_packet_becomes_a_trade_tick(instrument):
    packet = {"code": 2, "security_id": 49081, "ltp": 368.1499938964844, "ltq": 50,
              "ltt": 1_757_155_200}
    tick = trade_from_ticker(packet, instrument, ts_init=1)
    assert isinstance(tick, TradeTick)
    assert tick.price == Price.from_str("368.15")
    assert int(tick.size) == 50


def test_the_price_is_rounded_off_the_wire_not_stringified(instrument):
    """368.1499938964844 is what float32 does to 368.15. Carrying it into
    the book makes every comparison against a strike miss."""
    packet = {"code": 2, "security_id": 49081, "ltp": 368.1499938964844, "ltq": 1,
              "ltt": 1_757_155_200}
    tick = trade_from_ticker(packet, instrument, ts_init=1)
    assert str(tick.price) == "368.15"


def test_no_aggressor_is_claimed(instrument):
    """Dhan does not say which side lifted. Claiming one would put a
    fabricated aggressor into every recorded tape."""
    packet = {"code": 2, "security_id": 49081, "ltp": 368.15, "ltq": 1,
              "ltt": 1_757_155_200}
    assert trade_from_ticker(packet, instrument, ts_init=1).aggressor_side is (
        AggressorSide.NO_AGGRESSOR
    )


def test_a_packet_with_no_last_price_yields_no_trade(instrument):
    """A zero LTP is Dhan saying nothing has traded, not a trade at zero."""
    packet = {"code": 2, "security_id": 49081, "ltp": 0.0, "ltq": 0, "ltt": 1_757_155_200}
    assert trade_from_ticker(packet, instrument, ts_init=1) is None


def test_a_full_packet_becomes_a_quote_from_the_top_of_book(instrument):
    packet = {
        "code": 8, "security_id": 49081, "ltp": 368.15, "ltq": 50, "ltt": 1_757_155_200,
        "depth": [
            {"bid_price": 368.10, "ask_price": 368.20, "bid_qty": 100, "ask_qty": 75,
             "bid_orders": 2, "ask_orders": 1},
            {"bid_price": 368.05, "ask_price": 368.25, "bid_qty": 50, "ask_qty": 40,
             "bid_orders": 1, "ask_orders": 1},
        ],
    }
    quote = quote_from_full(packet, instrument, ts_init=1)
    assert isinstance(quote, QuoteTick)
    assert quote.bid_price == Price.from_str("368.10")
    assert quote.ask_price == Price.from_str("368.20")
    assert int(quote.bid_size) == 100
    assert int(quote.ask_size) == 75


def test_a_full_packet_with_an_empty_book_yields_no_quote(instrument):
    """A zero-price level is Dhan's 'no order here', not a real bid of zero.
    Emitting it would put a zero bid in the book and cross every spread."""
    packet = {
        "code": 8, "security_id": 49081, "ltp": 368.15, "ltq": 1, "ltt": 1_757_155_200,
        "depth": [{"bid_price": 0.0, "ask_price": 0.0, "bid_qty": 0, "ask_qty": 0,
                   "bid_orders": 0, "ask_orders": 0}],
    }
    assert quote_from_full(packet, instrument, ts_init=1) is None


def test_a_one_sided_book_yields_no_quote(instrument):
    """A QuoteTick needs both sides. Half a book is common at the open and
    on illiquid strikes; inventing the missing side would fabricate a
    spread that never existed."""
    packet = {
        "code": 8, "security_id": 49081, "ltp": 368.15, "ltq": 1, "ltt": 1_757_155_200,
        "depth": [{"bid_price": 368.10, "ask_price": 0.0, "bid_qty": 100, "ask_qty": 0,
                   "bid_orders": 1, "ask_orders": 0}],
    }
    assert quote_from_full(packet, instrument, ts_init=1) is None


def test_a_full_packet_with_no_depth_yields_no_quote(instrument):
    packet = {"code": 8, "security_id": 49081, "ltp": 368.15, "ltq": 1,
              "ltt": 1_757_155_200, "depth": []}
    assert quote_from_full(packet, instrument, ts_init=1) is None


def test_the_exchange_timestamp_is_used_for_ts_event(instrument):
    """`ltt` is the exchange's own stamp. Using our arrival time as ts_event
    would replay every recorded run at the wrong instant."""
    packet = {"code": 2, "security_id": 49081, "ltp": 368.15, "ltq": 1,
              "ltt": 1_757_155_200}
    tick = trade_from_ticker(packet, instrument, ts_init=999)
    assert tick.ts_event == 1_757_155_200 * 1_000_000_000
    assert tick.ts_init == 999


def test_the_config_and_factory_are_the_public_surface():
    """A user imports a config and a factory, nothing else."""
    import nautilus_india.dhan as dhan

    assert hasattr(dhan, "DhanDataClientConfig")
    assert hasattr(dhan, "DhanLiveDataClientFactory")


def test_the_factory_matches_the_signature_nautilus_calls():
    """`TradingNode.add_data_client_factory` calls `create` with exactly
    these six arguments. A mismatch fails at node build, long after the
    config looked fine."""
    import inspect

    from nautilus_trader.live.factories import LiveDataClientFactory

    from nautilus_india.dhan import DhanLiveDataClientFactory

    assert issubclass(DhanLiveDataClientFactory, LiveDataClientFactory)
    ours = inspect.signature(DhanLiveDataClientFactory.create)
    base = inspect.signature(LiveDataClientFactory.create)
    assert list(ours.parameters) == list(base.parameters)
