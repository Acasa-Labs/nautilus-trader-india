"""Dhan answers HTTP 200 for failures, so the BODY is the only evidence."""

import pytest

from nautilus_india.dhan.errors import (
    Ambiguous,
    IPNotWhitelisted,
    OrderRejected,
    RateLimited,
    TransportError,
    classify,
)


def test_a_success_body_yields_its_data():
    assert classify({"status": "success", "data": {"orderId": "123"}}) == {"orderId": "123"}


def test_a_success_body_with_no_data_yields_an_empty_dict():
    """An unknown securityId returns 200 with empty arrays, byte-identical
    to a holiday. Empty is a legitimate answer, not an error."""
    assert classify({"status": "success"}) == {}
    assert classify({"status": "success", "data": None}) == {}


def test_a_rejection_names_its_reason_and_code():
    with pytest.raises(OrderRejected) as exc:
        classify({
            "status": "failed",
            "remarks": {"error_code": "DH-901", "error_message": "Insufficient funds"},
        })
    assert exc.value.reason == "Insufficient funds"
    assert exc.value.code == "DH-901"


def test_remarks_may_be_a_bare_string_and_the_message_survives():
    """Some endpoints return a bare string. A dict-only reader reports every
    one of those as 'unspecified failure' and throws away the only
    description of what went wrong."""
    with pytest.raises(OrderRejected, match="something went wrong"):
        classify({"status": "failed", "remarks": "something went wrong"})


@pytest.mark.parametrize("code", ["DH-905", "DH-808"])
def test_an_ip_code_degrades_the_whole_client(code):
    """Every subsequent order fails identically, so retrying is 250 useless
    requests a minute. This is not a per-order failure."""
    with pytest.raises(IPNotWhitelisted):
        classify({"status": "failed", "remarks": {"error_code": code, "error_message": "x"}})


@pytest.mark.parametrize(
    "message",
    ["Invalid IP", "ip not whitelisted", "Your IP is NOT WHITELISTED for this account"],
)
def test_an_ip_message_is_recognised_even_without_a_known_code(message):
    """Dhan has returned this as prose. Matching only on the code would
    report it as an ordinary rejection and retry forever."""
    with pytest.raises(IPNotWhitelisted):
        classify({"status": "failed", "remarks": {"error_message": message}})


def test_a_rate_limit_is_the_only_retryable_outcome():
    with pytest.raises(RateLimited):
        classify({"status": "failed", "remarks": {"error_code": "DH-904", "error_message": "x"}})


def test_an_unrecognised_failure_is_still_a_rejection_not_a_guess():
    with pytest.raises(OrderRejected, match="unspecified failure"):
        classify({"status": "failed"})


def test_a_non_dict_body_is_a_transport_error():
    with pytest.raises(TransportError):
        classify(["not", "an", "object"])


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"status": "failed"},
        {"status": "failure"},
        {"status": "failed", "remarks": {}},
        {"status": "failed", "remarks": "timeout"},
        {"status": "failed", "remarks": {"error_code": "DH-904"}},
        {"status": "failed", "remarks": {"error_code": "DH-905"}},
        {"status": "failed", "remarks": {"error_message": "gateway timeout"}},
        {"status": "failed", "remarks": {"error_message": "connection reset"}},
        {"status": None},
        {"data": {"orderId": "1"}},
    ],
)
def test_no_body_can_make_classify_raise_ambiguous(body):
    """`classify` reads a body. If there IS a body, the request reached the
    venue and came back -- that is never ambiguous. Ambiguity is a property
    of the TRANSPORT, when no answer arrived at all.

    Asserted behaviourally rather than by grepping the source: a text check
    is satisfied by rewording a docstring, which is not the property that
    matters. What matters is that no input produces the exception -- because
    collapsing the two is exactly how a timeout gets reported as a
    rejection, and reporting a working order as dead opens a position
    nobody chose.
    """
    try:
        classify(body)
    except Ambiguous as exc:  # pragma: no cover - the assertion is the point
        pytest.fail(f"classify({body!r}) raised Ambiguous: {exc}")
    except TransportError:
        pass  # any other classified failure is fine


def test_ambiguous_says_what_may_have_happened():
    """The message is read by a human deciding whether to intervene, so it
    has to say what was in flight, not just that something failed."""
    exc = Ambiguous("POST /v2/orders timed out after 10.0s")
    assert "POST /v2/orders" in str(exc)
    assert isinstance(exc, TransportError)
