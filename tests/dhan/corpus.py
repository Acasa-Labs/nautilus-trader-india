"""Loader for the captured Dhan response-envelope corpus.

Tests name a fixture; this resolves it and carries its provenance along, so a
failure says which real response disagreed rather than which literal did.

See `fixtures/envelope/README.md` for what the three tiers mean and why the
`documented` one is quarantined.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CORPUS_DIR = Path(__file__).parent / "fixtures" / "envelope"
TIERS = ("captured", "sandbox", "recorded", "documented")


@dataclass(frozen=True, slots=True)
class Fixture:
    """One real Dhan response body, with where it came from."""

    tier: str
    name: str
    endpoint: str
    http_status: int
    provenance: str
    body: Any
    path: Path

    @property
    def id(self) -> str:
        return f"{self.tier}/{self.name}"

    def __str__(self) -> str:
        return f"{self.id} ({self.endpoint} -> HTTP {self.http_status})"


def _read(path: Path) -> Fixture:
    record = json.loads(path.read_text())
    return Fixture(
        tier=path.parent.name,
        name=path.stem,
        endpoint=record["endpoint"],
        http_status=record["http_status"],
        provenance=record["provenance"],
        body=record["body"],
        path=path,
    )


def all_fixtures() -> list[Fixture]:
    """Every fixture in every tier, in a stable order."""
    return [_read(p) for tier in TIERS for p in sorted((CORPUS_DIR / tier).glob("*.json"))]


def fixture(name: str) -> Fixture:
    """One fixture by bare name, searched across the tiers."""
    matches = [f for f in all_fixtures() if f.name == name]
    if not matches:
        raise LookupError(f"no fixture named {name!r} in {CORPUS_DIR}")
    if len(matches) > 1:
        raise LookupError(f"{name!r} is ambiguous: {[f.id for f in matches]}")
    return matches[0]


def body(name: str) -> Any:
    """The response body alone, for a test that does not need provenance."""
    return fixture(name).body
