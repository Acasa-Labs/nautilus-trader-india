"""The closed vocabularies. Broker-neutral by design.

THE VENUE IS THE EXCHANGE, NOT THE BROKER. `Venue("NSE")`, never
`Venue("KITE")`. A strategy names where the contract trades, so the same
strategy runs on Kite or Dhan without an edit; the broker is the `ClientId`.
Getting this backwards would make every strategy broker-specific, and the
mistake is invisible until you try to move one.

PRODUCT TYPES ARE NORMALISED, not passed through. Kite says CNC/MIS/NRML and
Dhan says CNC/INTRADAY/MARGIN for overlapping meanings. Each adapter
translates at its own edge; nothing broker-specific is spelled here.
"""

from __future__ import annotations

from enum import Enum

from nautilus_trader.model.identifiers import Venue


class Exchange(Enum):
    """Where a contract trades. This becomes the Nautilus `Venue`."""

    NSE = "NSE"
    BSE = "BSE"
    MCX = "MCX"

    def venue(self) -> Venue:
        return Venue(self.value)


class InstrumentClass(Enum):
    """What kind of contract it is.

    Decides the symbol shape and the Nautilus instrument type -- see
    `symbology` and `instruments`.
    """

    EQUITY = "EQUITY"
    INDEX = "INDEX"
    FUTURE = "FUTURE"
    OPTION = "OPTION"


class ProductType(Enum):
    """Normalised product intent.

    INTRADAY squares off the same day; DELIVERY does not; MARGIN is a
    leveraged carry. Each adapter maps these onto its broker's own words.
    """

    INTRADAY = "INTRADAY"
    DELIVERY = "DELIVERY"
    MARGIN = "MARGIN"


class Validity(Enum):
    DAY = "DAY"
    IOC = "IOC"
