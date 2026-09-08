"""What Dhan actually returns, and what `classify` must make of it.

EVERY BODY HERE IS A REAL ONE. Not one is written by hand, because the bug
these tests exist to prevent is precisely a hand-written one: `classify`
used to parse `{"status": "success", "data": ...}` on success and
`{"status": ..., "remarks": {"error_code", "error_message"}}` on failure, an
envelope invented rather than observed. The suite was green throughout,
because the tests asserted the same invented shape the code implemented. A
successful order read as a rejection.

So the corpus is the specification. See `fixtures/envelope/README.md`.

THE STATUS CODE IS NEVER THE ANSWER, in either direction: `/v2/ip/getIP`
carries an error at 200, `/v2/holdings` at 500 and the order endpoints at
400 carry bodies worth reading. Nothing here reads a code.
"""

import pytest

from nautilus_india.dhan import errors as err
from nautilus_india.dhan.errors import (
    Ambiguous,
    DhanApiError,
    DhanError,
    IPNotWhitelisted,
    RateLimited,
    TransportError,
    classify,
)
from tests.dhan import corpus

ALL = corpus.all_fixtures()
ALL_IDS = [f.id for f in ALL]


# --------------------------------------------------------------------------
# The corpus itself. A fabricated body must not be able to enter quietly.
# --------------------------------------------------------------------------


def test_the_corpus_is_not_empty():
    """A loader bug that found nothing would make every parametrized test
    below vacuously pass, which is the one failure this file cannot see."""
    assert len(ALL) >= 15


@pytest.mark.parametrize("fx", ALL, ids=ALL_IDS)
def test_every_fixture_declares_a_provenance_matching_its_tier(fx):
    """The directory is the provenance claim. Fixtures are captured, never
    fabricated; a body with no stated origin is indistinguishable from one
    somebody believed in."""
    assert fx.provenance in {"captured-live", "recorded-elsewhere", "documented-never-observed"}
    assert fx.provenance == {
        "captured": "captured-live",
        "recorded": "recorded-elsewhere",
        "documented": "documented-never-observed",
    }[fx.tier]


# --------------------------------------------------------------------------
# Success. Four shapes, and the body is the payload in three of them.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["fundlimit", "profile"])
def test_a_bare_object_is_itself_the_payload(name):
    """No envelope to unwrap: `/v2/fundlimit` and `/v2/profile` return the
    payload directly."""
    assert classify(corpus.body(name)) == corpus.body(name)


@pytest.mark.parametrize(
    "name", ["orders_list", "positions", "trades", "super_orders", "order_unknown_id"]
)
def test_a_bare_array_is_itself_the_payload(name):
    """These four list endpoints answer with a bare array, and this account
    has never traded, so every one is empty. Empty is an answer."""
    assert classify(corpus.body(name)) == []


def test_an_order_id_that_cannot_exist_is_an_empty_list_not_an_error():
    """`GET /v2/orders/00000000` answers 200 with `[]` -- not 404, not an
    error body. So a status query cannot tell 'no such order' from 'nothing
    to report', and an empty status response is never evidence that an order
    was rejected."""
    assert classify(corpus.body("order_unknown_id")) == []


def test_the_envelope_family_is_unwrapped_to_its_data():
    """`{"status": "success", "data": ...}` IS real -- on the option-chain
    and market-feed endpoints. The vendor notes' table says nothing returns
    it, which holds for the order, portfolio, funds and profile endpoints and
    not in general. Both shapes have to be read."""
    assert classify(corpus.body("expirylist")) == corpus.body("expirylist")["data"]
    assert classify(corpus.body("expirylist"))[0] == "2026-09-08"


def test_the_envelope_data_may_be_a_list_or_an_object():
    """Two endpoints, two different `data` types. Anything assuming a dict
    breaks on the other one."""
    assert isinstance(classify(corpus.body("expirylist")), list)
    assert isinstance(classify(corpus.body("marketfeed_ltp")), dict)


@pytest.mark.parametrize("name", ["charts_unknown_security", "marketfeed_unknown_id"])
def test_an_unknown_instrument_is_an_empty_success_not_a_failure(name):
    """An id that does not exist answers 200 with empty collections, byte-
    identical to a holiday. Raising here would make a quiet market look like
    a broken request; the caller has to notice emptiness itself."""
    classify(corpus.body(name))  # must not raise


def test_a_documented_order_acknowledgement_is_a_success():
    """THE REGRESSION. This is the body a live order returns, and the old
    `classify` raised OrderRejected('unspecified failure') for it: two orders
    accepted at the exchange, two rejections recorded, zero fills, an empty
    position book.

    The fixture is `documented-never-observed` -- this account cannot place
    an order without a whitelisted static IP -- so the VALUES are illustrative
    and only the field names are Dhan's. Re-capture it the day a real order is
    accepted.
    """
    assert classify(corpus.body("order_accepted")) == {
        "orderId": "112111182198",
        "orderStatus": "PENDING",
    }


# --------------------------------------------------------------------------
# Failure. Three shapes, none of which is announced by the status code.
# --------------------------------------------------------------------------


def test_an_error_object_carries_its_reason_code_and_type():
    """`{errorType, errorCode, errorMessage}` at the TOP level -- not under
    `remarks`, which is where the old code looked and why every error
    degraded to 'unspecified failure'."""
    with pytest.raises(DhanApiError) as exc:
        classify(corpus.body("holdings"))
    assert exc.value.reason == "No holdings available"
    assert exc.value.code == "DH-1111"
    assert exc.value.error_type == "HOLDING_ERROR"


def test_an_error_inside_a_bare_array_is_still_an_error():
    """`/v2/ip/getIP` answers 200 with `[{"message": ..., "status": "ERROR"}]`.
    This is the body that disproves 'a list means success' -- the container
    is not the discriminator."""
    with pytest.raises(DhanApiError, match="Something went wrong"):
        classify(corpus.body("ip_getip"))


def test_that_array_error_is_not_read_as_an_ip_failure():
    """It arrives from the IP endpoint and says nothing about whitelisting.
    Keying on the endpoint rather than the message would degrade the client
    on a body that carries no such claim."""
    with pytest.raises(DhanApiError) as exc:
        classify(corpus.body("ip_getip"))
    assert not isinstance(exc.value, IPNotWhitelisted)


def test_the_envelope_family_reports_failure_without_any_error_field():
    """The shape that breaks the rule the vendor notes propose -- 'an error is
    a body carrying errorMessage, or status ERROR'. This body has neither:
    it is `status: "failed"` with the message hidden under `data` as a
    {code: message} map. That rule would return `{"813": "Invalid
    SecurityId"}` to the caller as data."""
    with pytest.raises(DhanApiError) as exc:
        classify(corpus.body("expirylist_invalid_security"))
    assert exc.value.reason == "Invalid SecurityId"
    assert exc.value.code == "813"


def test_a_rate_limit_is_the_only_retryable_outcome():
    """Tripped for real against the per-second chart limit. There is no
    Retry-After header, so a caller has to pace itself."""
    with pytest.raises(RateLimited) as exc:
        classify(corpus.body("rate_limited"))
    assert exc.value.code == "DH-904"
    assert "rate limit" in exc.value.reason.lower()


def test_an_ip_failure_degrades_the_whole_client():
    """Not a per-order failure: every later order fails identically, so
    retrying is 250 useless requests a minute."""
    with pytest.raises(IPNotWhitelisted):
        classify(corpus.body("order_invalid_ip"))


def test_the_same_code_with_another_message_is_an_ordinary_failure():
    """DH-905 came back for 'Invalid IP', for 'quantity is required' and for
    a 90-day span limit -- it is the generic Input_Exception and carries no
    information. Keying on it would degrade the client on a typo."""
    for name in ("order_quantity_required", "charts_span_too_long"):
        with pytest.raises(DhanApiError) as exc:
            classify(corpus.body(name))
        assert exc.value.code == "DH-905"
        assert not isinstance(exc.value, IPNotWhitelisted), name


def test_an_expired_token_is_an_error_body_like_any_other():
    """A token dies 24 hours after minting, so this arrives mid-session. It
    means the request never reached the exchange -- evidence of NOT SENT,
    which an order path must translate to a denial rather than a rejection.
    """
    with pytest.raises(DhanApiError) as exc:
        classify(corpus.body("renew_token_invalid"))
    assert exc.value.code == "DH-906"
    assert exc.value.reason == "Invalid Token"


# --------------------------------------------------------------------------
# The invented envelope must not survive as a special case.
# --------------------------------------------------------------------------


def test_the_remarks_envelope_is_not_special_cased_any_more():
    """`{"status": ..., "remarks": {...}}` is a shape no Dhan endpoint
    returns. A compatibility branch for it would be a second unobserved rule
    living on next to the observed ones."""
    assert not hasattr(err, "_IP_CODES"), "codes are not diagnostic; classify on the message"
    source = err.__file__
    with open(source) as fh:
        text = fh.read()
    assert "error_message" not in text, "the snake_case remarks fields were invented"
    assert "remarks" not in text.split('"""', 2)[-1], "no code may still read `remarks`"


def test_a_success_status_without_data_returns_the_whole_body():
    """`status: "success"` is a marker on one endpoint family, not a licence
    to unwrap something that is not there."""
    assert classify({"status": "success"}) == {"status": "success"}


# --------------------------------------------------------------------------
# Bodies that are not bodies.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("body", ["a string", 3, 3.5, True, None])
def test_json_that_is_not_an_object_or_an_array_is_a_transport_error(body):
    """Dhan answered with something, but not with anything we can read as a
    response. Naming it as unclassified beats guessing at it."""
    with pytest.raises(TransportError):
        classify(body)


# --------------------------------------------------------------------------
# The invariant the whole taxonomy rests on.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("fx", ALL, ids=ALL_IDS)
def test_no_real_body_can_make_classify_raise_ambiguous(fx):
    """`classify` reads a body. If there IS a body, the request reached Dhan
    and came back -- that is never ambiguous. Ambiguity is a property of the
    TRANSPORT, when no answer arrived at all.

    Collapsing the two is how a timeout gets reported as a rejection, and
    reporting a working order as dead opens a position nobody chose.
    """
    try:
        classify(fx.body)
    except Ambiguous as exc:  # pragma: no cover - the assertion is the point
        pytest.fail(f"classify({fx}) raised Ambiguous: {exc}")
    except DhanError:
        pass  # any other classified outcome is fine


@pytest.mark.parametrize("body", [{}, [], "", 0, None, [{}], {"data": None}])
def test_no_degenerate_body_can_make_classify_raise_ambiguous(body):
    """The same invariant at the edges, where a shape rule is likeliest to
    fall through to a default."""
    try:
        classify(body)
    except Ambiguous as exc:  # pragma: no cover
        pytest.fail(f"classify({body!r}) raised Ambiguous: {exc}")
    except DhanError:
        pass


def test_ambiguous_says_what_may_have_happened():
    """The message is read by a human deciding whether to intervene, so it
    has to say what was in flight, not just that something failed."""
    exc = Ambiguous("POST /v2/orders timed out after 10.0s")
    assert "POST /v2/orders" in str(exc)
    assert isinstance(exc, TransportError)


def test_a_rejection_is_not_catchable_as_a_transport_error():
    """They are siblings on purpose. `except TransportError` is how a caller
    says 'I could not read an answer'; a definitive refusal that got caught
    there would be retried as if nothing had been decided."""
    assert not issubclass(DhanApiError, TransportError)
    assert not issubclass(IPNotWhitelisted, TransportError)
    assert not issubclass(RateLimited, TransportError)
    assert issubclass(Ambiguous, TransportError)
    for cls in (DhanApiError, IPNotWhitelisted, RateLimited, TransportError):
        assert issubclass(cls, DhanError)


def test_the_client_level_outcomes_are_not_catchable_as_an_ordinary_failure():
    """`except DhanApiError` is the order path saying 'the venue refused this
    request'. An IP failure and a rate limit need the opposite handling --
    degrade, and retry -- so neither may be swallowed by that clause,
    whichever order the excepts happen to be written in."""
    assert not issubclass(IPNotWhitelisted, DhanApiError)
    assert not issubclass(RateLimited, DhanApiError)
