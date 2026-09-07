import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

from nautilus_india.dhan.auth import (
    DhanCredentials,
    MalformedTokenError,
    MissingCredentialsError,
    from_env,
    token_expiry,
    token_is_live,
)


def _token(issued: datetime, lifetime_seconds: int = 86_400) -> str:
    """A JWT-shaped token. Only the payload segment is ever read."""
    def seg(obj) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    iat = int(issued.timestamp())
    return f"{seg({'alg': 'HS256'})}.{seg({'iat': iat, 'exp': iat + lifetime_seconds})}.sig"


def test_credentials_come_from_the_environment():
    creds = from_env({"DHAN_CLIENT_ID": "C1", "DHAN_ACCESS_TOKEN": "tok"})
    assert creds == DhanCredentials("C1", "tok")


def test_a_missing_credential_names_the_variable_it_wants():
    """An error that does not say which variable is missing makes the reader
    guess between two."""
    with pytest.raises(MissingCredentialsError, match="DHAN_ACCESS_TOKEN"):
        from_env({"DHAN_CLIENT_ID": "C1"})
    with pytest.raises(MissingCredentialsError, match="DHAN_CLIENT_ID"):
        from_env({"DHAN_ACCESS_TOKEN": "tok"})


def test_the_token_is_redacted_from_repr():
    creds = DhanCredentials("C1", "super-secret")
    assert "super-secret" not in repr(creds)
    assert "C1" in repr(creds)


def test_the_token_expires_24_hours_after_it_was_minted():
    """Measured 2026-09-04/05 and confirmed against GET /v2/profile: exp is
    exactly iat + 86400. NOT a fixed hour -- a token minted during market
    hours dies during market hours."""
    issued = datetime(2026, 9, 4, 11, 30, tzinfo=UTC)
    assert token_expiry(_token(issued)) == issued + timedelta(seconds=86_400)


def test_a_token_is_not_live_once_it_is_inside_the_margin():
    """The margin exists so a session does not start a subscription that
    dies mid-morning. Reporting 'live' up to the last second is how a feed
    drops thirty minutes after a healthy-looking start."""
    issued = datetime(2026, 9, 4, 11, 30, tzinfo=UTC)
    token = _token(issued)
    assert token_is_live(token, now=issued + timedelta(hours=1)) is True
    assert token_is_live(token, now=issued + timedelta(hours=23, minutes=45)) is False
    assert token_is_live(token, now=issued + timedelta(hours=25)) is False


def test_a_token_that_is_not_a_jwt_raises_rather_than_being_assumed_valid():
    """Assuming validity means discovering the problem at the first order."""
    with pytest.raises(MalformedTokenError):
        token_expiry("not-a-jwt")
    with pytest.raises(MalformedTokenError):
        token_expiry("a.b.c")


def test_padding_is_restored_before_decoding():
    """base64url in a JWT is unpadded. A decoder that does not restore the
    padding fails on two payload lengths out of three, which reads as an
    intermittent credential bug."""
    for lifetime in (86_400, 86_401, 86_402):
        issued = datetime(2026, 9, 4, 11, 30, tzinfo=UTC)
        assert token_expiry(_token(issued, lifetime)) == issued + timedelta(seconds=lifetime)


def test_a_token_with_no_exp_claim_raises():
    """A token we cannot date is one we cannot refuse to use in time."""
    def seg(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    with pytest.raises(MalformedTokenError, match="exp"):
        token_expiry(f"{seg({'alg': 'x'})}.{seg({'iat': 1})}.sig")
