"""The Dhan error taxonomy, and the one place a response body is interpreted.

DHAN ANSWERS 200 FOR FAILURES. An unknown `securityId` returns 200 with empty
arrays, byte-identical to a holiday, and a 4xx sometimes carries the same
shaped body. So nothing here reads a status code. The body is the only
evidence.

FIVE OUTCOMES, because they need five different responses:

  OrderRejected     the exchange said no to THIS request. The session goes on.
  IPNotWhitelisted  this account cannot place orders from here AT ALL. Every
                    subsequent order fails identically, so retrying is 250
                    useless requests a minute and the client must degrade.
  RateLimited       retryable, and the only one that is.
  Ambiguous         NO body arrived. The request may or may not have reached
                    the venue. See below -- this is the important one.
  TransportError    something we did not enumerate. Never guessed at.

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
    """Base for everything this adapter raises about Dhan."""


class TransportError(DhanError):
    """Unclassified. The body did not match anything we know."""


class OrderRejected(TransportError):
    """A definitive refusal of this request, by Dhan or the exchange."""

    def __init__(self, reason: str, code: str = "") -> None:
        super().__init__(reason)
        self.reason, self.code = reason, code


class IPNotWhitelisted(TransportError):
    """Order placement requires a whitelisted static IP.

    Degrades the CLIENT, not the order: every later request fails the same
    way, so continuing to send them is pure noise.
    """


class RateLimited(TransportError):
    """Dhan's order APIs are 10/sec, 250/min, 1000/hour, 7000/day."""

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


class Ambiguous(TransportError):
    """No answer arrived, so we do not know whether the venue acted.

    Raised by the transport on a write that timed out, was reset, or came
    back unreadable. A caller must NOT translate this into a rejection: the
    order may be working at the exchange. It is resolved by asking Dhan what
    it holds, never by assuming.
    """


# Order APIs: 10/sec, 250/min, 1000/hour, 7000/day.
_RATE_CODES = frozenset({"DH-904"})
_IP_CODES = frozenset({"DH-905", "DH-808"})
_IP_PHRASES = ("invalid ip", "ip not whitelisted", "not whitelisted")


def classify(body: Any) -> dict[str, Any]:
    """Return `data` on success; raise the right error otherwise.

    Never raises the no-answer case -- see the module docstring.
    """
    if not isinstance(body, dict):
        raise TransportError(f"Dhan answered with JSON that is not an object: {body!r}")

    if body.get("status") == "success":
        return body.get("data") or {}

    remarks = body.get("remarks") or {}
    if isinstance(remarks, str):
        # Some endpoints return a bare string. A dict-only reader reports
        # every one of those as "unspecified failure" and throws away the
        # only description of what went wrong.
        remarks = {"error_message": remarks}
    code = str(remarks.get("error_code") or "")
    message = str(remarks.get("error_message") or "unspecified failure")

    if code in _IP_CODES or any(p in message.lower() for p in _IP_PHRASES):
        raise IPNotWhitelisted(
            f"{code or 'no code'}: {message}. Order placement requires a "
            "whitelisted static IP; every request will fail the same way "
            "until one is configured."
        )
    if code in _RATE_CODES:
        raise RateLimited()
    raise OrderRejected(message, code)
