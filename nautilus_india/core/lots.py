"""Contract lot sizes, keyed by EXPIRY and carrying their own provenance.

LOT SIZE ATTACHES TO A CONTRACT, NOT TO A DATE RANGE. A long-dated series
listed under an old regime keeps its original lot after NSE revises. The
archive shows this plainly: the 2026-06-25 expiry carries 25 while
2026-06-30 carries 65. A range table alone silently mis-scales every
long-dated position, so the exact map is consulted first.

A NUMBER WITH NO PROVENANCE IS A GUESS. `lot_size_source` says where each
answer came from and `is_verified_lot_size` says whether this exact expiry
was observed in NSE's own bhavcopy archive or collapsed from a regime. A
wrong lot is a uniform scaling of every P&L, which no downstream assertion
catches -- so the model says how much it trusts itself.
"""

from __future__ import annotations

from datetime import date
from functools import cache
from pathlib import Path

import yaml

_SPEC_PATH = Path(__file__).parent / "data" / "lot_sizes.yaml"


class UnknownLotSizeError(Exception):
    """No sourced lot size covers this underlying and expiry.

    Raised rather than defaulted, because a wrong lot size scales every P&L
    uniformly and no downstream test would notice.
    """


@cache
def _spec() -> dict:
    return yaml.safe_load(_SPEC_PATH.read_text())


def _lookup(underlying: str, expiry: date) -> tuple[int, bool, str]:
    """(lot, observed_directly, provenance). Exact expiry first, then regime."""
    spec = _spec().get(underlying)
    if spec is None:
        raise UnknownLotSizeError(
            f"No lot-size data for {underlying}. Harvest it from the NSE "
            f"F&O bhavcopy archive and add it to {_SPEC_PATH.name}."
        )

    hit = spec.get("observed", {}).get(expiry.isoformat())
    if hit is not None:
        return int(hit), True, "observed in the NSE F&O bhavcopy archive"

    for row in spec.get("regimes", []):
        if date.fromisoformat(row["from"]) <= expiry <= date.fromisoformat(row["to"]):
            return int(row["lot"]), False, (
                f"regime {row['from']}..{row['to']}, collapsed from observed expiries"
            )

    raise UnknownLotSizeError(
        f"No lot size for {underlying} expiring {expiry}. Harvest it from "
        f"the NSE bhavcopy archive and add it to {_SPEC_PATH.name}."
    )


def lot_size(underlying: str, expiry: date) -> int:
    """Contract multiplier for the series expiring on `expiry`."""
    return _lookup(underlying, expiry)[0]


def is_verified_lot_size(underlying: str, expiry: date) -> bool:
    """True when this exact expiry was observed in NSE's archive."""
    return _lookup(underlying, expiry)[1]


def lot_size_source(underlying: str, expiry: date) -> str:
    """Where this lot size came from. A number with no provenance is a guess."""
    return _lookup(underlying, expiry)[2]
