from nautilus_trader.model.identifiers import Venue

from nautilus_india.core.enums import Exchange, InstrumentClass, ProductType, Validity


def test_exchange_maps_to_a_nautilus_venue():
    assert Exchange.NSE.venue() == Venue("NSE")
    assert Exchange.BSE.venue() == Venue("BSE")
    assert Exchange.MCX.venue() == Venue("MCX")


def test_the_venue_is_the_exchange_not_the_broker():
    """A strategy names the exchange, so it runs on either broker unchanged.

    If the venue were KITE or DHAN, every strategy would be written against
    a broker and moving it would mean rewriting it. This assertion is the
    guard on that design decision.
    """
    assert {e.value for e in Exchange} == {"NSE", "BSE", "MCX"}
    assert "KITE" not in {e.value for e in Exchange}
    assert "DHAN" not in {e.value for e in Exchange}


def test_instrument_classes_cover_what_indian_venues_list():
    assert {c.value for c in InstrumentClass} == {
        "EQUITY", "INDEX", "FUTURE", "OPTION",
    }


def test_product_and_validity_vocabularies_are_closed():
    assert ProductType.INTRADAY.value == "INTRADAY"
    assert ProductType.DELIVERY.value == "DELIVERY"
    assert Validity.DAY.value == "DAY"
    assert Validity.IOC.value == "IOC"
