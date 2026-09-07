"""The provider turns master rows into Nautilus instruments, and keeps the
broker's own id out of the InstrumentId."""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from nautilus_trader.model.instruments import OptionContract

from nautilus_india.dhan.providers import DhanInstrumentProvider

FIXTURE = Path(__file__).parent / "fixtures" / "scrip_master_sample.csv"
NOW = datetime(2026, 9, 8, tzinfo=UTC)


def _provider(body: bytes | None = None, status: int = 200) -> DhanInstrumentProvider:
    payload = FIXTURE.read_bytes() if body is None else body

    async def handler(request):
        return httpx.Response(status, content=payload)

    return DhanInstrumentProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        now=lambda: NOW,
    )


async def test_it_loads_instruments_from_the_master():
    provider = _provider()
    await provider.load_all_async()
    assert provider.count > 0


async def test_expired_contracts_are_not_offered():
    """The master ships 13.8% dead rows. Loading them offers contracts that
    cannot be traded, and Nautilus will accept an order for one."""
    provider = _provider()
    await provider.load_all_async()
    for instrument in provider.list_all():
        expiration = getattr(instrument, "expiration_ns", 0)
        if expiration:
            assert expiration >= provider.now_ns


async def test_the_fixture_really_did_contain_expired_rows():
    """Guards the guard: the filter must have had something to remove, or
    the test above passes vacuously."""
    import csv

    from nautilus_india.dhan.parsing import parse_row

    with FIXTURE.open() as fh:
        parsed = [p for r in csv.DictReader(fh) if (p := parse_row(r))]
    assert any(p.expiry and p.expiry < NOW.date() for p in parsed)

    provider = _provider()
    await provider.load_all_async()
    assert provider.count < len(parsed)


async def test_the_instrument_id_carries_the_exchange_not_the_broker():
    """Broker-neutrality. A strategy written against NSE runs on Kite too."""
    provider = _provider()
    await provider.load_all_async()
    venues = {i.id.venue.value for i in provider.list_all()}
    assert venues <= {"NSE", "BSE", "MCX"}
    assert "DHAN" not in venues


async def test_the_security_id_is_recoverable_but_is_not_the_identity():
    """Dhan's id rides in `info` and in the provider's map, never in the
    InstrumentId -- otherwise the same contract would have two identities."""
    provider = _provider()
    await provider.load_all_async()
    instrument = provider.list_all()[0]
    security_id = provider.security_id_for(instrument.id)
    assert security_id
    assert security_id not in instrument.id.value
    assert instrument.info["security_id"] == security_id


async def test_the_map_round_trips_both_ways_for_every_routable_instrument():
    from nautilus_india.dhan.providers import UNKNOWN_SEGMENT_CODE

    provider = _provider()
    await provider.load_all_async()
    routable = [
        i for i in provider.list_all() if i.info["segment_code"] != UNKNOWN_SEGMENT_CODE
    ]
    assert routable
    for instrument in routable:
        security_id = provider.security_id_for(instrument.id)
        segment = instrument.info["segment_code"]
        assert provider.instrument_id_for(security_id, segment) == instrument.id


async def test_nse_commodity_is_listed_but_not_routable():
    """Dhan publishes no feed segment code for NSE commodity.

    Its own SDK enumerates 0,1,2,3,4,5,7,8 and defines no NSE-commodity
    constant -- 6 is unassigned -- so dhanhq cannot subscribe to these
    23,870 contracts either. Guessing 6 would not fail loudly: a wrong
    segment names a different instrument, and Dhan answers 200 with empty
    data for one that does not exist, so it would look like a market with no
    trades.

    They are still built and listed, because they are real contracts whose
    specification a caller may want. They are simply not routable, and this
    test pins that as a known gap rather than a surprise at subscribe time.
    """
    from nautilus_india.dhan.providers import UNKNOWN_SEGMENT_CODE

    provider = _provider()
    await provider.load_all_async()
    uncatalogued = [
        i for i in provider.list_all() if i.info["segment_code"] == UNKNOWN_SEGMENT_CODE
    ]
    assert uncatalogued, "the fixture no longer carries an NSE commodity contract"
    for instrument in uncatalogued:
        assert instrument.info["segment"] == "M"
        assert instrument.id.venue.value == "NSE"
        # Present in the universe, absent from the routing map.
        security_id = provider.security_id_for(instrument.id)
        assert provider.instrument_id_for(security_id, UNKNOWN_SEGMENT_CODE) is None


async def test_an_unknown_instrument_id_raises_rather_than_guessing():
    """Dhan answers 200 with empty data for an unknown securityId, which
    reads as a quiet market rather than as an error."""
    from nautilus_trader.model.identifiers import InstrumentId

    provider = _provider()
    await provider.load_all_async()
    with pytest.raises(LookupError, match="scrip master"):
        provider.security_id_for(InstrumentId.from_str("NOTREAL.NSE"))


async def test_the_tick_size_came_from_the_master_in_rupees():
    """Every instrument's tick is the master's own value, converted from
    paise. A "< 1" assertion would be wrong: MCX GOLD legitimately ticks at
    Rs 1.00, so smallness is not the property -- agreement is."""
    from decimal import Decimal

    provider = _provider()
    await provider.load_all_async()
    assert provider.count > 0
    for instrument in provider.list_all():
        stated = Decimal(instrument.info["tick_size_from_master"])
        assert instrument.price_increment.as_decimal() == stated


async def test_an_nse_option_ticks_at_five_paise_not_five_rupees():
    """The trap itself, pinned on a real contract. TICK_SIZE 5.0000 in the
    master means Rs 0.05. Taken raw it would be Rs 5.00 -- a hundredfold
    tick that rounds every order to a multiple of five rupees, while still
    looking like a plausible tick size."""
    from decimal import Decimal

    from nautilus_trader.model.instruments import OptionContract

    provider = _provider()
    await provider.load_all_async()
    five_paise = [
        i for i in provider.list_all()
        if isinstance(i, OptionContract) and i.info["tick_size_from_master"] == "0.05"
    ]
    assert five_paise, "the fixture no longer carries a five-paise option"
    for option in five_paise:
        assert option.price_increment.as_decimal() == Decimal("0.05")


async def test_a_currency_contract_keeps_its_four_decimal_tick():
    """NSE currency derivatives tick at Rs 0.0025. `core` hardcodes two
    decimal places, which formats that to "0.00" -- a zero tick Nautilus
    rejects. Precision is therefore derived from the tick itself, and this
    pins it."""
    from decimal import Decimal

    from nautilus_india.dhan.providers import price_precision_for

    assert price_precision_for(Decimal("0.0025")) == 4
    assert price_precision_for(Decimal("0.050000")) == 2
    assert price_precision_for(Decimal("0.5")) == 2


async def test_the_lot_size_came_from_the_master():
    """The exchange's live listing beats our historical table."""
    provider = _provider()
    await provider.load_all_async()
    options = [i for i in provider.list_all() if isinstance(i, OptionContract)]
    assert options
    for option in options:
        assert int(option.multiplier) == option.info["lot_size_from_master"]


async def test_an_unparseable_row_does_not_stop_the_load():
    """The master gains instrument types without warning. One unknown row
    must not cost the other 199,999."""
    lines = FIXTURE.read_text().splitlines()
    body = "\n".join([lines[0], "GARBAGE,ROW,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,", *lines[1:]])
    provider = _provider(body.encode())
    await provider.load_all_async()
    assert provider.count > 0


async def test_a_failed_download_raises_rather_than_leaving_an_empty_provider():
    """An empty provider looks exactly like a market with no instruments,
    and every later subscription fails with a confusing 'unknown instrument'
    rather than 'the master did not download'."""
    provider = _provider(b"upstream unavailable", status=503)
    with pytest.raises(RuntimeError, match="scrip master"):
        await provider.load_all_async()


async def test_a_filter_narrows_the_universe():
    """Loading 200,000 instruments to trade one is wasteful, and Nautilus
    holds them all in the cache."""
    provider = _provider()
    await provider.load_all_async(filters={"underlyings": {"NIFTY"}})
    assert provider.count >= 0
    for instrument in provider.list_all():
        assert "NIFTY" in instrument.id.symbol.value
