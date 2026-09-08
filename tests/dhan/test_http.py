"""The HTTP seam. Driven through httpx.MockTransport; no socket is opened.

The bodies come from the captured corpus rather than from literals, so this
file cannot drift back to asserting a shape nobody has seen. What it adds on
top of `test_errors.py` is the TRANSPORT half of the split: what happens when
there is no body to classify at all.
"""

import httpx
import pytest

from nautilus_india.dhan.errors import (
    Ambiguous,
    DhanApiError,
    DhanError,
    IPNotWhitelisted,
    TransportError,
)
from nautilus_india.dhan.http import DhanHttpClient
from tests.dhan import corpus


def _client(handler) -> DhanHttpClient:
    return DhanHttpClient(
        "CLIENT1", "token-abc",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _answering(name: str):
    """A handler replying with one captured body, at the status it arrived on."""
    fx = corpus.fixture(name)

    async def handler(request):
        return httpx.Response(fx.http_status, json=fx.body)

    return handler


async def test_a_bare_object_comes_back_as_itself():
    """There is no envelope on this endpoint family, so there is nothing to
    unwrap. The old client returned `body["data"]` and would have handed the
    caller an empty dict."""
    assert await _client(_answering("fundlimit")).get("/v2/fundlimit") == corpus.body("fundlimit")


async def test_a_bare_array_comes_back_as_a_list():
    """The return type is not a dict. Anything annotated `dict` here is
    wrong for four of the endpoints this client exists to call."""
    assert await _client(_answering("orders_list")).get("/v2/orders") == []


async def test_an_order_acknowledgement_is_not_a_rejection():
    """THE REGRESSION, at the seam. This body used to raise OrderRejected."""
    body = await _client(_answering("order_accepted")).post("/v2/orders", {})
    assert body["orderStatus"] == "PENDING"


async def test_the_credential_travels_in_headers_never_in_the_body():
    """A bearer token in a JSON body is a bearer token in every request log
    that ever captures one."""
    seen = {}

    async def handler(request):
        seen["headers"] = dict(request.headers)
        seen["content"] = request.content.decode()
        return httpx.Response(200, json=corpus.body("order_accepted"))

    await _client(handler).post("/v2/orders", {"securityId": "1"})
    assert seen["headers"]["access-token"] == "token-abc"
    assert seen["headers"]["client-id"] == "CLIENT1"
    assert "token-abc" not in seen["content"]


async def test_a_200_carrying_a_failure_body_is_still_a_failure():
    """`/v2/ip/getIP` answers 200 with an error inside a bare array. A
    status-code reader calls this a success and hands the caller the error
    itself as data."""
    with pytest.raises(DhanApiError, match="Something went wrong"):
        await _client(_answering("ip_getip")).get("/v2/ip/getIP")


async def test_a_4xx_body_is_still_parsed_for_its_reason():
    """No raise_for_status. A 4xx from Dhan still carries the reason in its
    body, and raising on the code throws away the only description."""
    with pytest.raises(IPNotWhitelisted):
        await _client(_answering("order_invalid_ip")).post("/v2/orders", {})


async def test_a_500_body_is_parsed_too():
    """`/v2/holdings` answers 500 for an account that simply has none."""
    with pytest.raises(DhanApiError) as exc:
        await _client(_answering("holdings")).get("/v2/holdings")
    assert exc.value.code == "DH-1111"


async def test_a_write_that_times_out_is_ambiguous_not_rejected():
    """THE important case. The order may be working at the exchange."""
    async def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(Ambiguous) as exc:
        await _client(handler).post("/v2/orders", {})
    assert "/v2/orders" in str(exc.value)
    assert not isinstance(exc.value, DhanApiError)


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


async def test_a_failure_body_on_a_write_is_never_ambiguous():
    """A body IS an answer. Ambiguity is the absence of one, and widening it
    to cover definite refusals would send every rejection to reconciliation."""
    with pytest.raises(DhanError) as exc:
        await _client(_answering("order_quantity_required")).post("/v2/orders", {})
    assert not isinstance(exc.value, Ambiguous)


async def test_the_token_is_redacted_from_repr():
    """Never in a log, at any level."""
    client = DhanHttpClient("CLIENT1", "super-secret-token")
    assert "super-secret-token" not in repr(client)
    assert "CLIENT1" in repr(client)
    await client.aclose()


@pytest.mark.parametrize("verb", ["put", "delete"])
async def test_every_write_verb_is_ambiguous_on_a_timeout(verb):
    """A cancel and a modify are writes too. A DELETE that times out may have
    cancelled the order; reporting it either way is a guess, and the guess
    that it did not is how a live order gets forgotten."""
    async def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    client = _client(handler)
    with pytest.raises(Ambiguous):
        if verb == "put":
            await client.put("/v2/orders/1", {})
        else:
            await client.delete("/v2/orders/1")


@pytest.mark.parametrize("verb", ["put", "delete"])
async def test_every_write_verb_uses_its_own_method(verb):
    seen = {}

    async def handler(request):
        seen["method"] = request.method
        return httpx.Response(202, json=corpus.body("order_cancelled"))

    client = _client(handler)
    if verb == "put":
        await client.put("/v2/orders/1", {})
    else:
        await client.delete("/v2/orders/1")
    assert seen["method"] == verb.upper()
