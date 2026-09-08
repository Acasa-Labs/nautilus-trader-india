"""The Dhan error taxonomy, and the one place a response body is interpreted.

THE BODY IS THE ONLY EVIDENCE, and this module reads nothing else. Dhan
answers 200 for failures -- `/v2/ip/getIP` carries an error at HTTP 200 --
and carries perfectly readable failures at 400 and 500. It also answers 200
with empty collections for an instrument that does not exist, byte-identical
to a holiday. A status code decides nothing here.

FIVE SHAPES, ALL MEASURED. The corpus is `tests/dhan/fixtures/envelope/`;
its README says where each body came from.

    {dhanClientId, availabelBalance, ...}     bare object -- the body IS the
                                              payload. /v2/fundlimit,
                                              /v2/profile, POST /v2/orders.
    [...]                                     bare array. /v2/orders,
                                              /v2/positions, /v2/trades,
                                              /v2/super/orders.
    {status: "success", data: ...}            an envelope, on the option-chain
                                              and market-feed endpoints only.
    {errorType, errorCode, errorMessage}      an error, TOP LEVEL -- never
                                              under `remarks`.
    {status: "failed", data: {code: message}} an error with no error field at
                                              all, on the envelope endpoints.
    [{message, status: "ERROR"}]              an error INSIDE a bare array.

NEITHER THE CONTAINER NOR THE CODE IS THE DISCRIMINATOR. A bare array is
usually a success, but `/v2/ip/getIP` returns an error in one. `DH-905` was
returned for "Invalid IP", for "quantity is required" and for a 90-day span
limit -- it is the generic Input_Exception and carries no information, so
this module classifies on the MESSAGE. And an error body need carry no error
field: `{"status": "failed", "data": {"813": "Invalid SecurityId"}}` is a
failure whose message hides under `data`.

FOUR OUTCOMES, because they need four different responses:

  DhanApiError      the venue refused THIS request and said why. The session
                    goes on.
  IPNotWhitelisted  this account cannot place orders from here AT ALL. Every
                    subsequent order fails identically, so retrying is 250
                    useless requests a minute and the client must degrade.
  RateLimited       retryable, and the only one that is. Measured: no
                    Retry-After header comes with it.
  TransportError    we could not read an answer. Never guessed at.

They are SIBLINGS, not a chain. `except DhanApiError` is an order path saying
"the venue refused this"; if an IP failure or a rate limit could be caught
there, the handling each needs -- degrade, and retry -- would depend on which
order somebody wrote the except clauses in.

WHY `Ambiguous` IS NOT RAISED HERE. `classify` reads a body; if there is a
body, the request reached Dhan and came back, which is never ambiguous.
Ambiguity is a property of the TRANSPORT -- a timeout, a reset connection, a
body that is not JSON. Keeping the two apart is what stops a timeout being
reported as a rejection, and reporting a working order as dead is how a
position nobody chose gets opened.

There is deliberately NO real transport in this module. Every consumer's test
suite imports the taxonomy from here, and a real HTTP client in that import
graph is a code path a test could reach by accident.
"""

from __future__ import annotations

from typing import Any


class DhanError(RuntimeError):
    """Base for everything this adapter raises about Dhan.

    Carries the venue's own words. `code` and `error_type` are frequently
    absent or useless -- see the note on DH-905 above -- so nothing may
    require them, and `reason` is what an operator reads.
    """

    def __init__(self, reason: str, code: str = "", error_type: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code
        self.error_type = error_type


class TransportError(DhanError):
    """No answer we can read. Not a statement about what the venue did."""


class Ambiguous(TransportError):
    """No answer arrived, so we do not know whether the venue acted.

    Raised by the transport on a write that timed out, was reset, or came
    back unreadable -- never by `classify`. A caller must NOT translate this
    into a rejection: the order may be working at the exchange. It is
    resolved by asking Dhan what it holds, never by assuming.
    """


class DhanApiError(DhanError):
    """A definitive refusal of this request, by Dhan or the exchange.

    Definitive is the point: the request was understood and declined, so
    nothing is in flight. Contrast `Ambiguous`.
    """


class IPNotWhitelisted(DhanError):
    """Order placement requires a whitelisted static IP.

    Degrades the CLIENT, not the order: every later request fails the same
    way, so continuing to send them is pure noise.
    """


class RateLimited(DhanError):
    """Dhan's order APIs are 10/sec, 250/min, 1000/hour, 7000/day.

    The only retryable outcome. Measured 2026-09-08: the 429 carries no
    Retry-After header, so a caller has to pace itself.
    """


# DH-904 is the only code worth keying on, and it is corroborated by its own
# errorType. Every other code observed was DH-905, for three unrelated causes.
_RATE_CODES = frozenset({"DH-904"})
_RATE_TYPES = frozenset({"Rate_Limit"})

# Matched against the venue's MESSAGE, because the code cannot carry this:
# "Invalid IP" and "quantity is required" both arrived as DH-905.
_IP_PHRASES = ("invalid ip", "ip not whitelisted", "not whitelisted")

_Failure = tuple[str, str, str]  # reason, code, error_type
_UNSPECIFIED = "unspecified failure"


def classify(body: Any) -> Any:
    """Return the payload; raise the right error otherwise.

    Never raises the no-answer case -- see the module docstring.
    """
    if isinstance(body, list):
        failure = _failure_in_array(body)
        if failure is not None:
            _raise(failure)
        return body

    if not isinstance(body, dict):
        raise TransportError(
            f"Dhan answered with JSON that is neither an object nor an array: {body!r}"
        )

    failure = _failure_in_object(body)
    if failure is not None:
        _raise(failure)

    # Only the option-chain and market-feed endpoints wrap their payload, and
    # only when they say so. Unwrapping unconditionally is what made a
    # successful order read as a rejection; unwrapping never would hand those
    # two endpoints' callers an envelope instead of their data.
    if body.get("status") == "success" and "data" in body:
        return body["data"]
    return body


def _failure_in_object(body: dict[str, Any]) -> _Failure | None:
    """Read a failure out of an object, or return None if it is a payload."""
    if any(k in body for k in ("errorMessage", "errorCode", "errorType")):
        return (
            str(body.get("errorMessage") or _UNSPECIFIED),
            str(body.get("errorCode") or ""),
            str(body.get("errorType") or ""),
        )

    status = body.get("status")
    if status is None or str(status).lower() == "success":
        return None

    # The envelope family announces failure with `status` alone and hides the
    # message under `data` as a {code: message} map. No error field appears at
    # all, so a rule keyed on one would return that map to the caller as data.
    reason, code = _from_code_message_map(body.get("data"))
    return (reason or f"Dhan answered status={status!r}", code, str(status))


def _failure_in_array(body: list[Any]) -> _Failure | None:
    """An error can arrive INSIDE a bare array -- `/v2/ip/getIP` at HTTP 200.

    So the container says nothing and every element has to be looked at. An
    element counts as an error only if it carries `errorMessage` or a `status`
    that is not a success; Dhan names an order's state `orderStatus`, never
    `status`, which is what keeps a real order list from reading as an error.
    That distinction is unverified against a NON-EMPTY list: this account has
    never traded, so every array in the corpus is empty. Re-check it against
    the first real order list.
    """
    for element in body:
        if not isinstance(element, dict):
            continue
        if "errorMessage" in element:
            return (
                str(element.get("errorMessage") or _UNSPECIFIED),
                str(element.get("errorCode") or ""),
                str(element.get("errorType") or ""),
            )
        status = element.get("status")
        if status is not None and str(status).lower() != "success":
            return (
                str(element.get("message") or _UNSPECIFIED),
                str(element.get("code") or ""),
                str(status),
            )
    return None


def _from_code_message_map(data: Any) -> tuple[str, str]:
    """`{"813": "Invalid SecurityId"}` -> the message, and the code."""
    if not isinstance(data, dict) or not data:
        return "", ""
    codes = [str(k) for k in data]
    messages = [str(v) for v in data.values()]
    return "; ".join(messages), ",".join(codes)


def _raise(failure: _Failure) -> None:
    """Turn a failure into the outcome its handling needs. Never returns."""
    reason, code, error_type = failure

    if code in _RATE_CODES or error_type in _RATE_TYPES:
        raise RateLimited(reason, code, error_type)

    if any(phrase in reason.lower() for phrase in _IP_PHRASES):
        raise IPNotWhitelisted(
            f"{reason}. Order placement requires a whitelisted static IP; every "
            "request will fail the same way until one is configured.",
            code,
            error_type,
        )

    raise DhanApiError(reason, code, error_type)
