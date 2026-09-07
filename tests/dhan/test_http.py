"""The HTTP seam. Driven through httpx.MockTransport; no socket is opened."""

import httpx
import pytest

from nautilus_india.dhan.errors import Ambiguous, IPNotWhitelisted, OrderRejected, TransportError
from nautilus_india.dhan.http import DhanHttpClient


def _client(handler) -> DhanHttpClient:
    return DhanHttpClient(
        "CLIENT1", "token-abc",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_a_success_body_returns_its_data():
    async def handler(request):
        return httpx.Response(200, json={"status": "success", "data": {"orderId": "42"}})

    assert await _client(handler).post("/v2/orders", {}) == {"orderId": "42"}


async def test_the_credential_travels_in_headers_never_in_the_body():
    """A bearer token in a JSON body is a bearer token in every request log
    that ever captures one."""
    seen = {}

    async def handler(request):
        seen["headers"] = dict(request.headers)
        seen["content"] = request.content.decode()
        return httpx.Response(200, json={"status": "success", "data": {}})

    await _client(handler).post("/v2/orders", {"securityId": "1"})
    assert seen["headers"]["access-token"] == "token-abc"
    assert seen["headers"]["client-id"] == "CLIENT1"
    assert "token-abc" not in seen["content"]


async def test_a_200_carrying_a_failure_body_is_a_rejection():
    """Dhan answers 200 for failures. A status-code reader would call this a
    success and hand a caller an empty dict."""
    async def handler(request):
        return httpx.Response(200, json={
            "status": "failed",
            "remarks": {"error_code": "DH-901", "error_message": "Insufficient funds"},
        })

    with pytest.raises(OrderRejected, match="Insufficient funds"):
        await _client(handler).post("/v2/orders", {})


async def test_a_4xx_body_is_still_parsed_for_its_reason():
    """No raise_for_status. A 4xx from Dhan still carries the reason in its
    body, and raising on the code throws away the only description."""
    async def handler(request):
        return httpx.Response(400, json={
            "status": "failed",
            "remarks": {"error_code": "DH-905", "error_message": "Invalid IP"},
        })

    with pytest.raises(IPNotWhitelisted):
        await _client(handler).post("/v2/orders", {})


async def test_a_write_that_times_out_is_ambiguous_not_rejected():
    """THE important case. The order may be working at the exchange."""
    async def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(Ambiguous) as exc:
        await _client(handler).post("/v2/orders", {})
    assert "/v2/orders" in str(exc.value)
    assert not isinstance(exc.value, OrderRejected)


async def test_a_write_whose_body_is_unreadable_is_ambiguous():
    """Dhan answered, but with something we cannot interpret. We do not know
    whether it acted, so we must not say it did not."""
    async def handler(request):
        return httpx.Response(200, content=b"<html>gateway error</html>")

    with pytest.raises(Ambiguous):
        await _client(handler).post("/v2/orders", {})


async def test_a_read_that_times_out_is_NOT_ambiguous():
    """A GET changes nothing, so a failed GET is just a failed read. Marking
    it ambiguous would make every flaky poll look like an order in limbo and
    train the operator to ignore the signal."""
    async def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(TransportError) as exc:
        await _client(handler).get("/v2/positions")
    assert not isinstance(exc.value, Ambiguous)


async def test_a_read_whose_body_is_unreadable_is_a_transport_error():
    async def handler(request):
        return httpx.Response(200, content=b"not json")

    with pytest.raises(TransportError) as exc:
        await _client(handler).get("/v2/positions")
    assert not isinstance(exc.value, Ambiguous)


async def test_the_token_is_redacted_from_repr():
    """Never in a log, at any level."""
    client = DhanHttpClient("CLIENT1", "super-secret-token")
    assert "super-secret-token" not in repr(client)
    assert "CLIENT1" in repr(client)
    await client.aclose()
