"""The one module that can reach Dhan over HTTP.

STATUS CODES ARE NOT READ AS THE ANSWER. Dhan answers 200 for failures -- an
unknown securityId returns 200 with empty arrays, byte-identical to a holiday
-- and sometimes answers 4xx with the same shaped body. So the body is parsed
either way and handed to `classify`. No `raise_for_status`: a 4xx still
carries the reason, and raising on the code throws it away.

WHAT COMES BACK IS NOT ALWAYS AN OBJECT. `/v2/orders`, `/v2/positions`,
`/v2/trades` and `/v2/super/orders` answer with a bare ARRAY, so these methods
return `Any` and not `dict`. An earlier annotation said `dict` and was wrong
for four of the endpoints this client exists to call -- a claim no runtime
test can catch, which is why it is stated here.

READS AND WRITES FAIL DIFFERENTLY, and this is the point of the module. A GET
changes nothing, so a GET that times out is just a failed read. A POST may
have reached the venue, so a POST that times out is ambiguous and the caller
must not conclude anything about the order. Marking reads ambiguous too would
flood the operator with false alarms and train them to ignore the one signal
that matters.

THE CREDENTIAL TRAVELS IN A HEADER. Dhan takes it there, and a bearer token
in a JSON body is a bearer token in every request log that ever captures one.
It is redacted from `__repr__`.
"""

from __future__ import annotations

from typing import Any

import httpx

from nautilus_india.dhan.constants import BASE_URL
from nautilus_india.dhan.errors import Ambiguous, TransportError, classify

# Order APIs are 10/sec, 250/min, 1000/hour, 7000/day. The per-second ceiling
# is rarely binding; a timeout is, because a hung order request is an order
# whose disposition is unknown.
TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class DhanHttpClient:
    def __init__(
        self,
        client_id: str,
        access_token: str,
        *,
        base_url: str = BASE_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client_id = client_id
        self._base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT)
        self._headers = {
            "access-token": access_token,
            "client-id": client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def __repr__(self) -> str:
        # The token is deliberately absent. See the module docstring.
        return f"{type(self).__name__}(client_id={self._client_id!r}, base_url={self._base!r})"

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(self, path: str, params: dict | None = None) -> Any:
        """A read. Failure is a failed read, never an ambiguity."""
        try:
            response = await self._client.get(
                f"{self._base}{path}", params=params, headers=self._headers
            )
        except httpx.HTTPError as exc:
            raise TransportError(f"GET {path} failed: {exc}") from exc
        return self._answer(response, path, ambiguous_on_failure=False)

    async def post(self, path: str, payload: dict) -> Any:
        """A write. Failure without a body is ambiguous, never a rejection."""
        try:
            response = await self._client.post(
                f"{self._base}{path}", json=payload, headers=self._headers
            )
        except httpx.HTTPError as exc:
            raise Ambiguous(
                f"POST {path} did not complete ({exc}). The request may have "
                "reached Dhan; do not conclude the order was rejected. Ask "
                "Dhan what it holds."
            ) from exc
        return self._answer(response, path, ambiguous_on_failure=True)

    def _answer(
        self, response: httpx.Response, path: str, *, ambiguous_on_failure: bool
    ) -> Any:
        try:
            body = response.json()
        except ValueError as exc:
            detail = (
                f"{response.request.method} {path} answered {response.status_code} "
                f"with a body that is not JSON: {response.text[:200]!r}"
            )
            if ambiguous_on_failure:
                raise Ambiguous(
                    f"{detail}. We cannot tell whether Dhan acted; ask it what it holds."
                ) from exc
            raise TransportError(detail) from exc
        return classify(body)
