"""The Dhan execution client.

THREE OUTCOMES, AND THE THIRD IS WHY THIS FILE IS CAREFUL.

    Proof it was never sent      -> OrderDenied
    Definitive venue rejection   -> OrderRejected
    May have reached the venue   -> NO EVENT AT ALL; reconcile

An `Ambiguous` from the transport means a write timed out, was reset, or came
back unreadable. The order may be working at the exchange. Reporting it
rejected tells the engine an order is dead while it is live, and the position
that follows is one nobody chose. So those handlers do nothing on purpose,
and the report generators are what resolve it.

EVERY CHECK THAT CAN DENY RUNS BEFORE `generate_order_submitted`. A denial is
a statement that nothing was sent, which stops being true the moment anything
is -- and Nautilus will not accept DENIED after SUBMITTED either.

AN IP FAILURE DEGRADES THIS CLIENT, NOT THE ORDER. Order placement needs a
whitelisted static IP. Without one every order fails identically, so the
second one is refused here rather than at Dhan: 250 useless requests a minute
is how a rate limit gets spent on a failure that was already known. The flag
is never cleared, because nothing in this process can whitelist an address.

TWO SWITCHES, NEVER ONE. See `config.submission_refusal`.

A FAILED READ IS NOT AN EMPTY BOOK. The report generators let a transport
error propagate rather than answering `[]`. An empty list means "nothing is
open", and returning one for a read that FAILED tells the engine every order
is gone -- after which it reopens them all.
"""

from __future__ import annotations

import asyncio
import os

from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.execution.messages import SubmitOrder
from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.model.currencies import INR
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import AccountId, ClientId, VenueOrderId

from nautilus_india.dhan.auth import from_env
from nautilus_india.dhan.config import DhanExecClientConfig, submission_refusal
from nautilus_india.dhan.constants import ORDERS_PATH
from nautilus_india.dhan.errors import Ambiguous, DhanError, IPNotWhitelisted
from nautilus_india.dhan.http import DhanHttpClient
from nautilus_india.dhan.orders import Unsendable, place_payload
from nautilus_india.dhan.providers import DhanInstrumentProvider


class DhanExecutionClient(LiveExecutionClient):
    """Places, cancels and reports Dhan orders on behalf of a Nautilus node."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: DhanExecClientConfig,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
    ) -> None:
        provider = DhanInstrumentProvider(
            config=config.instrument_provider, url=config.scrip_master_url
        )
        super().__init__(
            loop=loop,
            client_id=ClientId(name),
            # This client serves NSE, BSE and MCX at once, so it is not bound
            # to one venue -- the instruments carry theirs.
            venue=None,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=INR,
            instrument_provider=provider,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            config=config,
        )
        self._config = config
        self._provider = provider
        self._dhan_client_id = config.client_id or ""
        self._http: DhanHttpClient | None = None
        # Set once an IP failure is seen, and never cleared: nothing in this
        # process can whitelist an address, so a retry can only fail again.
        self.is_degraded_by_ip = False
        self._set_account_id(AccountId(f"{name}-{self._dhan_client_id or 'UNSET'}"))

    # -- lifecycle -------------------------------------------------------

    async def _connect(self) -> None:
        client_id, token = self._config.client_id, self._config.access_token
        if not (client_id and token):
            # Resolved at connect time rather than at construction: a token
            # rotates every 24 hours, so one captured when the config was
            # built is one that may already be dead.
            credentials = from_env()
            client_id = client_id or credentials.client_id
            token = token or credentials.access_token
        self._dhan_client_id = client_id
        self._set_account_id(AccountId(f"{self.id.value}-{client_id}"))
        self._http = DhanHttpClient(client_id, token, base_url=self._config.base_url)
        await self._instrument_provider.initialize()

    async def _disconnect(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # -- submitting ------------------------------------------------------

    async def _submit_order(self, command: SubmitOrder) -> None:
        order = command.order
        instrument = self._cache.instrument(order.instrument_id)

        refusal = self._refusal_for(order, instrument)
        if refusal is not None:
            # Denied, not rejected: nothing has been sent, and nothing will be.
            self.generate_order_denied(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=refusal,
                ts_event=self._clock.timestamp_ns(),
            )
            return

        payload = place_payload(
            order=order,
            instrument=instrument,
            security_id=self._provider.security_id_for(order.instrument_id),
            client_id=self._dhan_client_id,
            product_type=self._config.product_type,
        )

        self.generate_order_submitted(
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            ts_event=self._clock.timestamp_ns(),
        )

        try:
            body = await self._http.post(ORDERS_PATH, payload)
        except Ambiguous as exc:
            # THE IMPORTANT CASE. The order may be at the exchange. Emitting
            # anything here is a claim we cannot support; the order status
            # reports resolve it.
            self._log.error(
                f"submission of {order.client_order_id} did not complete and may "
                f"have reached Dhan ({exc}). NO EVENT EMITTED -- the order may be "
                "working at the exchange. Resolve by reconciliation."
            )
            return
        except IPNotWhitelisted as exc:
            self.is_degraded_by_ip = True
            self._log.error(
                f"client degraded: {exc} Every later order fails identically, so "
                "no further order will be sent from this client."
            )
            self.generate_order_rejected(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=str(exc),
                ts_event=self._clock.timestamp_ns(),
            )
            return
        except DhanError as exc:
            # Definitive: Dhan understood the request and declined it, so
            # nothing is working at the exchange.
            self.generate_order_rejected(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=str(exc),
                ts_event=self._clock.timestamp_ns(),
            )
            return

        self.generate_order_accepted(
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=VenueOrderId(str(body["orderId"])),
            ts_event=self._clock.timestamp_ns(),
        )

    def _refusal_for(self, order, instrument) -> str | None:
        """Why this order will not be sent, or None. Runs before any I/O."""
        gate = submission_refusal(self._config, os.environ)
        if gate is not None:
            return gate
        if self.is_degraded_by_ip:
            return (
                "this client is degraded: Dhan refused an earlier order with "
                "'Invalid IP', and every order from this address fails the same "
                "way until a static IP is whitelisted."
            )
        if instrument is None:
            return (
                f"{order.instrument_id} is not in the cache, so there is no lot "
                "size to convert the quantity with and no segment to route it to."
            )
        try:
            # Building the payload IS the validation: it resolves the segment,
            # the validity and the correlation id, and raises on each. Built
            # with a placeholder id because the real one is looked up first
            # and raises on its own.
            self._provider.security_id_for(order.instrument_id)
            place_payload(
                order=order,
                instrument=instrument,
                security_id="0",
                client_id=self._dhan_client_id,
                product_type=self._config.product_type,
            )
        except (LookupError, Unsendable) as exc:
            return str(exc)
        return None
