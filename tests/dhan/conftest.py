"""Instruments the execution tests share.

Built through the package's own `core.instruments` helpers, so a test cannot
assert against a contract shape the adapter would never mint. The Dhan
`info` keys are then set the way `DhanInstrumentProvider` sets them -- a
fixture that carried different ones would be testing a provider that does
not exist.
"""

from datetime import date
from decimal import Decimal

import pytest

from nautilus_india.core.enums import Exchange, InstrumentClass
from nautilus_india.core.instruments import option_contract
from nautilus_india.core.symbology import ContractKey
from nautilus_india.dhan.providers import UNKNOWN_SEGMENT_CODE

LOT = 65


def _option(segment_code: int, security_id: str = "43492") -> object:
    key = ContractKey(
        underlying="NIFTY",
        instrument_class=InstrumentClass.OPTION,
        expiry=date(2026, 9, 29),
        strike=Decimal("24550"),
        right="CE",
    )
    built = option_contract(key, Exchange.NSE, ts_init=0, lot_size=LOT)
    kind = type(built)
    data = kind.to_dict(built)
    # The lot lives in the multiplier and `lot_size` stays 1 -- see CLAUDE.md
    # on why passing it as both squares the position.
    data["multiplier"] = str(LOT)
    data["info"] = dict(built.info or {}) | {
        "security_id": security_id,
        "segment": "D",
        "segment_code": segment_code,
    }
    return kind.from_dict(data)


@pytest.fixture
def nifty_option():
    """A routable NSE F&O contract: segment code 2 is NSE_FNO."""
    return _option(segment_code=2)


@pytest.fixture
def nse_commodity_option():
    """NSE commodity: real, listed, and with no published feed segment.

    23,870 OPTFUT rows are in the master and none of them can be routed --
    Dhan publishes no segment code and its own SDK defines none.
    """
    return _option(segment_code=UNKNOWN_SEGMENT_CODE, security_id="99999")
