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
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.messages import (
    CancelAllOrders,
    CancelOrder,
    GenerateFillReports,
    GenerateOrderStatusReport,
    GenerateOrderStatusReports,
    GeneratePositionStatusReports,
    ModifyOrder,
    SubmitOrder,
    SubmitOrderList,
)
from nautilus_trader.execution.reports import (
    ExecutionMassStatus,
    FillReport,
    OrderStatusReport,
    PositionStatusReport,
)
from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.model.currencies import INR
from nautilus_trader.model.enums import (
    AccountType,
    OmsType,
    OrderSide,
    OrderType,
    TimeInForce,
)
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientId,
    ClientOrderId,
    InstrumentId,
    VenueOrderId,
)
from nautilus_trader.model.orders import Order

from nautilus_india.dhan import forever_orders, super_orders
from nautilus_india.dhan.auth import from_env
from nautilus_india.dhan.config import DhanExecClientConfig, submission_refusal
from nautilus_india.dhan.constants import (
    FOREVER_ORDERS_PATH,
    LEG_ENTRY,
    LEG_STOP_LOSS,
    LEG_TARGET,
    ORDER_SLICING_PATH,
    ORDER_TYPE_LIMIT,
    ORDERS_EXTERNAL_PATH,
    ORDERS_PATH,
    POSITIONS_PATH,
    PRODUCT_CNC,
    SEGMENT_CODES,
    SUPER_ORDERS_PATH,
    TRADES_PATH,
)
from nautilus_india.dhan.errors import Ambiguous, DhanError, IPNotWhitelisted
from nautilus_india.dhan.http import DhanHttpClient
from nautilus_india.dhan.orders import (
    MARKET_ORDER_NOTE,
    Unsendable,
    fill_report,
    modify_payload,
    order_status_report,
    order_type_for,
    place_payload,
    position_status_report,
    units_for,
    validity_for,
)
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
            after_market_order=self._config.after_market_order,
            amo_time=self._config.amo_time,
        )

        if order.order_type is OrderType.MARKET:
            # Disclosure, not a refusal: the order goes as asked, and the
            # caller is told what the venue will do with it.
            self._log.warning(f"{order.client_order_id}: {MARKET_ORDER_NOTE}")

        self.generate_order_submitted(
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            ts_event=self._clock.timestamp_ns(),
        )

        path = ORDER_SLICING_PATH if self._config.slice_over_freeze_limit else ORDERS_PATH
        try:
            body = await self._http.post(path, payload)
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
                after_market_order=self._config.after_market_order,
                amo_time=self._config.amo_time,
            )
        except (LookupError, Unsendable) as exc:
            return str(exc)
        return None

    # -- cancelling and modifying ----------------------------------------

    async def _cancel_order(self, command: CancelOrder) -> None:
        venue_order_id = command.venue_order_id
        if venue_order_id is None:
            # Dhan addresses a cancel by its own order id and offers no other
            # handle. Guessing one cancels somebody else's order.
            self.generate_order_cancel_rejected(
                strategy_id=command.strategy_id,
                instrument_id=command.instrument_id,
                client_order_id=command.client_order_id,
                venue_order_id=None,
                reason=(
                    "no venue order id: Dhan cancels by its own order id only, and "
                    "there is nothing else to address the order by."
                ),
                ts_event=self._clock.timestamp_ns(),
            )
            return
        try:
            await self._http.delete(f"{ORDERS_PATH}/{venue_order_id.value}")
        except Ambiguous as exc:
            # The order may or may not still be working. Saying it is
            # cancelled is how a live order gets forgotten.
            self._log.error(
                f"cancel of {venue_order_id} did not complete and may have reached "
                f"Dhan ({exc}). NO EVENT EMITTED -- the order may still be working. "
                "Resolve by reconciliation."
            )
            return
        except DhanError as exc:
            self.generate_order_cancel_rejected(
                strategy_id=command.strategy_id,
                instrument_id=command.instrument_id,
                client_order_id=command.client_order_id,
                venue_order_id=venue_order_id,
                reason=str(exc),
                ts_event=self._clock.timestamp_ns(),
            )
            return
        self.generate_order_canceled(
            strategy_id=command.strategy_id,
            instrument_id=command.instrument_id,
            client_order_id=command.client_order_id,
            venue_order_id=venue_order_id,
            ts_event=self._clock.timestamp_ns(),
        )

    async def _cancel_all_orders(self, command: CancelAllOrders) -> None:
        """One DELETE per working order.

        Dhan has no cancel-all on the order endpoint. `DELETE /v2/positions`
        exits POSITIONS, which is a different and much larger action -- it
        would close holdings this command never mentioned.
        """
        for venue_order_id in self._open_venue_order_ids(
            command.instrument_id, command.order_side
        ):
            await self._cancel_order(
                CancelOrder(
                    trader_id=command.trader_id,
                    strategy_id=command.strategy_id,
                    instrument_id=command.instrument_id,
                    client_order_id=self._client_order_id_for(venue_order_id),
                    venue_order_id=venue_order_id,
                    command_id=UUID4(),
                    ts_init=self._clock.timestamp_ns(),
                )
            )

    def _open_venue_order_ids(
        self, instrument_id: InstrumentId, order_side: OrderSide
    ) -> list[VenueOrderId]:
        """The working orders this client knows about, from the cache."""
        found = []
        for order in self._cache.orders_open(instrument_id=instrument_id):
            if order_side != OrderSide.NO_ORDER_SIDE and order.side != order_side:
                continue
            if order.venue_order_id is not None:
                found.append(order.venue_order_id)
        return found

    def _client_order_id_for(self, venue_order_id: VenueOrderId) -> ClientOrderId:
        """Our own id for a venue id, falling back to the venue's own.

        The fallback is for an order this session did not place -- one from
        Dhan's app, or from a session that has since restarted. Refusing to
        cancel it would leave a working order nobody here can reach.
        """
        return self._cache.client_order_id(venue_order_id) or ClientOrderId(
            venue_order_id.value
        )

    async def _modify_order(self, command: ModifyOrder) -> None:
        if command.venue_order_id is None:
            self._reject_modify(command, "no venue order id: Dhan modifies by its "
                                         "own order id only.")
            return
        instrument = self._cache.instrument(command.instrument_id)
        order = self._cache.order(command.client_order_id)
        quantity = command.quantity or (order.quantity if order else None)
        price = command.price or (order.price if order else None)
        if instrument is None or quantity is None or price is None:
            # Dhan's modify replaces the terms rather than patching them, so a
            # missing one would be sent as absent and the venue would decide
            # what it meant.
            self._reject_modify(
                command, "a Dhan modify replaces the order's terms, so it needs "
                         "both a quantity and a price."
            )
            return
        trigger = command.trigger_price or (
            order.trigger_price if order and order.has_trigger_price else None
        )
        payload = modify_payload(
            order_id=command.venue_order_id.value,
            client_id=self._dhan_client_id,
            quantity_units=units_for(instrument, quantity),
            price=str(price),
            trigger_price=str(trigger) if trigger is not None else "",
            validity=validity_for(order.time_in_force if order else TimeInForce.DAY),
            order_type=order_type_for(order.order_type) if order else ORDER_TYPE_LIMIT,
        )
        try:
            await self._http.put(f"{ORDERS_PATH}/{command.venue_order_id.value}", payload)
        except Ambiguous as exc:
            self._log.error(
                f"modify of {command.venue_order_id} did not complete and may have "
                f"reached Dhan ({exc}). NO EVENT EMITTED -- the order's terms are "
                "unknown until reconciliation says."
            )
            return
        except DhanError as exc:
            self._reject_modify(command, str(exc))
            return
        self.generate_order_updated(
            strategy_id=command.strategy_id,
            instrument_id=command.instrument_id,
            client_order_id=command.client_order_id,
            venue_order_id=command.venue_order_id,
            quantity=quantity,
            price=price,
            trigger_price=None,
            ts_event=self._clock.timestamp_ns(),
        )

    def _reject_modify(self, command: ModifyOrder, reason: str) -> None:
        self.generate_order_modify_rejected(
            strategy_id=command.strategy_id,
            instrument_id=command.instrument_id,
            client_order_id=command.client_order_id,
            venue_order_id=command.venue_order_id,
            reason=reason,
            ts_event=self._clock.timestamp_ns(),
        )

    # -- reports ---------------------------------------------------------
    #
    # A FAILED READ IS NOT AN EMPTY BOOK. Nothing here catches a transport
    # error to answer `[]`. An empty list means "nothing is open", and
    # returning one for a read that FAILED tells the engine every order is
    # gone -- after which it reopens them all.

    def _instrument_for(self, row: dict):
        """The instrument a Dhan row refers to, or None if we cannot say."""
        code = SEGMENT_CODES.get(str(row.get("exchangeSegment") or ""))
        if code is None:
            return None
        instrument_id = self._provider.instrument_id_for(str(row.get("securityId")), code)
        if instrument_id is None:
            return None
        return self._cache.instrument(instrument_id) or self._provider.find(instrument_id)

    def _reports_from(self, rows, build) -> list:
        """Map rows to reports, skipping -- loudly -- the ones we cannot map.

        A row this adapter cannot resolve is a holding it cannot name.
        Dropping it silently is exactly the risk nobody is sizing against, so
        it is logged as an error and the rest of the book still reports.
        """
        found = []
        ts_init = self._clock.timestamp_ns()
        for row in rows or []:
            instrument = self._instrument_for(row)
            if instrument is None:
                self._log.error(
                    f"Dhan reported securityId {row.get('securityId')!r} on segment "
                    f"{row.get('exchangeSegment')!r}, which is not in the loaded "
                    "scrip master. The row is NOT reported: it is a holding this "
                    "adapter cannot name."
                )
                continue
            found.append(build(row, instrument, self.account_id, UUID4(), ts_init))
        return found

    async def generate_order_status_report(
        self, command: GenerateOrderStatusReport
    ) -> OrderStatusReport | None:
        """One order, addressed by whichever id the caller has.

        Dhan's own id is the direct address. When we do not have it there is
        `GET /v2/orders/external/{correlation-id}`, which exists for exactly
        this -- Dhan's words are "in case the user has missed order id due to
        unforeseen reason". Giving up because we lack a venue id would
        abandon an order whose id Dhan is holding for us.
        """
        if command.venue_order_id is not None:
            path = f"{ORDERS_PATH}/{command.venue_order_id.value}"
        elif command.client_order_id is not None:
            path = f"{ORDERS_EXTERNAL_PATH}/{command.client_order_id.value}"
        else:
            # Answering with the day's whole order book would be a different
            # question from the one asked.
            return None
        body = await self._http.get(path)
        # Dhan documents this endpoint as returning an OBJECT and the one live
        # call this repository has made returned an ARRAY. Both are read.
        rows = body if isinstance(body, list) else [body]
        # Measured: an order id that cannot exist answers 200 with `[]`, so
        # "no such order" and "nothing to say" are the same answer and None is
        # the only one this can honestly give.
        reports = self._reports_from(rows, order_status_report)
        return reports[0] if reports else None

    async def generate_order_status_reports(
        self, command: GenerateOrderStatusReports
    ) -> list[OrderStatusReport]:
        return self._reports_from(await self._http.get(ORDERS_PATH), order_status_report)

    async def generate_fill_reports(
        self, command: GenerateFillReports
    ) -> list[FillReport]:
        """The day's fills, or one order's.

        `GET /v2/trades/{order-id}` exists because, in Dhan's words, "during
        partial trades or Bracket/Cover Orders traders get confused in reading
        trade from tradebook". Filtering the whole book client-side would pull
        every trade of the day to answer a question about one order.
        """
        if command.venue_order_id is not None:
            body = await self._http.get(f"{TRADES_PATH}/{command.venue_order_id.value}")
        else:
            body = await self._http.get(TRADES_PATH)
        # Documented as a bare object for one order and an array for the book.
        rows = body if isinstance(body, list) else [body]
        return self._reports_from(rows, fill_report)

    async def generate_position_status_reports(
        self, command: GeneratePositionStatusReports
    ) -> list[PositionStatusReport]:
        return self._reports_from(
            await self._http.get(POSITIONS_PATH), position_status_report
        )

    async def generate_mass_status(
        self, lookback_mins: int | None = None
    ) -> ExecutionMassStatus | None:
        """All three books at once.

        `lookback_mins` is ignored, and deliberately: Dhan's order, trade and
        position endpoints return the DAY's, with no window parameter.
        Pretending to honour it would report a filter that was never applied.
        """
        status = ExecutionMassStatus(
            client_id=self.id,
            account_id=self.account_id,
            venue=self.venue,
            report_id=UUID4(),
            ts_init=self._clock.timestamp_ns(),
        )
        status.add_order_reports(
            self._reports_from(await self._http.get(ORDERS_PATH), order_status_report)
        )
        status.add_fill_reports(
            self._reports_from(await self._http.get(TRADES_PATH), fill_report)
        )
        status.add_position_reports(
            self._reports_from(await self._http.get(POSITIONS_PATH), position_status_report)
        )
        return status

    # -- super orders and forever orders ---------------------------------
    #
    # Two endpoints that hold a relationship the ordinary order path cannot.
    # A super order keeps entry, target and stop together, so a filled target
    # cancels the stop instead of leaving it working. A forever order rests
    # past the close, which /v2/orders cannot do at all -- it takes DAY and
    # IOC and nothing else.

    async def _submit_order_list(self, command: SubmitOrderList) -> None:
        """A bracket, sent as ONE super order.

        Sending the three legs separately would drop the very thing the caller
        asked for: there is no OCO between independent orders, so a filled
        target leaves the stop working and the next move opens a position
        nobody chose.
        """
        orders_in_list = list(command.order_list.orders)
        entry = orders_in_list[0]
        instrument = self._cache.instrument(entry.instrument_id)

        refusal = self._super_order_refusal(command, entry, instrument)
        if refusal is not None:
            for order in orders_in_list:
                self.generate_order_denied(
                    strategy_id=order.strategy_id,
                    instrument_id=order.instrument_id,
                    client_order_id=order.client_order_id,
                    reason=refusal,
                    ts_event=self._clock.timestamp_ns(),
                )
            return

        target, stop_loss = orders_in_list[1], orders_in_list[2]
        payload = super_orders.place_payload(
            entry=entry, target=target, stop_loss=stop_loss, instrument=instrument,
            security_id=self._provider.security_id_for(entry.instrument_id),
            client_id=self._dhan_client_id,
            product_type=self._config.product_type,
        )

        for order in orders_in_list:
            self.generate_order_submitted(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                ts_event=self._clock.timestamp_ns(),
            )

        try:
            body = await self._http.post(SUPER_ORDERS_PATH, payload)
        except Ambiguous as exc:
            self._log.error(
                f"super order {entry.client_order_id} did not complete and may have "
                f"reached Dhan ({exc}). NO EVENT EMITTED for any of its three legs "
                "-- all of them may be working. Resolve by reconciliation."
            )
            return
        except DhanError as exc:
            if isinstance(exc, IPNotWhitelisted):
                self.is_degraded_by_ip = True
            for order in orders_in_list:
                self.generate_order_rejected(
                    strategy_id=order.strategy_id,
                    instrument_id=order.instrument_id,
                    client_order_id=order.client_order_id,
                    reason=str(exc),
                    ts_event=self._clock.timestamp_ns(),
                )
            return

        # ONE acknowledgement covers three orders, and Nautilus is tracking
        # all three. Accepting only the entry leaves two the engine believes
        # are still in flight.
        order_id = str(body["orderId"])
        # strict: a bracket is exactly three orders, and a fourth silently
        # dropped would be an order Nautilus tracks and Dhan never heard of.
        legs = (LEG_ENTRY, LEG_TARGET, LEG_STOP_LOSS)
        for order, leg in zip(orders_in_list, legs, strict=True):
            self.generate_order_accepted(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=super_orders.leg_venue_order_id(order_id, leg),
                ts_event=self._clock.timestamp_ns(),
            )

    def _super_order_refusal(self, command, entry, instrument) -> str | None:
        gate = submission_refusal(self._config, os.environ)
        if gate is not None:
            return gate
        if self.is_degraded_by_ip:
            return (
                "this client is degraded: Dhan refused an earlier order with "
                "'Invalid IP', and every order from this address fails the same way."
            )
        if not command.order_list.is_bracket:
            return (
                "Dhan's only multi-order primitive on this path is the super order, "
                "which is an entry with a target and a stop. This list is not that "
                "shape. Sending its orders one at a time would silently drop the "
                "contingency the caller asked for."
            )
        if instrument is None:
            return f"{entry.instrument_id} is not in the cache"
        try:
            self._provider.security_id_for(entry.instrument_id)
            legs = list(command.order_list.orders)
            super_orders.place_payload(
                entry=legs[0], target=legs[1], stop_loss=legs[2], instrument=instrument,
                security_id="0", client_id=self._dhan_client_id,
                product_type=self._config.product_type,
            )
        except (LookupError, Unsendable) as exc:
            return str(exc)
        return None

    async def cancel_super_order_leg(self, order_id: str, leg_name: str) -> None:
        """Cancel one leg, or ENTRY_LEG to cancel the whole structure.

        Cancelling a target or stop leg on its own CANNOT BE UNDONE -- Dhan
        will not let the same leg be added again, and nothing in the API says
        so at the point of no return. So this does.
        """
        if leg_name != LEG_ENTRY:
            self._log.warning(
                f"{order_id}/{leg_name}: {super_orders.LEG_CANCEL_IS_IRREVERSIBLE}"
            )
        try:
            await self._http.delete(super_orders.cancel_path(order_id, leg_name))
        except Ambiguous as exc:
            self._log.error(
                f"cancel of super order leg {order_id}/{leg_name} did not complete "
                f"and may have reached Dhan ({exc}). The leg may still be working."
            )

    async def modify_super_order_leg(self, order_id: str, leg_name: str, **terms) -> None:
        """Change one leg's terms.

        ENTRY_LEG moves the whole super order, but only while the entry is
        PENDING or PART_TRADED. Once it is TRADED, only the target and stop
        legs move, and only their price and trailing jump.
        """
        payload = super_orders.modify_payload(
            order_id=order_id, client_id=self._dhan_client_id, leg_name=leg_name, **terms
        )
        try:
            await self._http.put(f"{SUPER_ORDERS_PATH}/{order_id}", payload)
        except Ambiguous as exc:
            self._log.error(
                f"modify of super order leg {order_id}/{leg_name} did not complete "
                f"and may have reached Dhan ({exc}). Its terms are unknown."
            )

    async def generate_super_order_reports(self) -> list[OrderStatusReport]:
        """The super order book, one report per LEG.

        A single report per super order would hide the target and the stop,
        which are the orders actually resting at the venue.
        """
        return super_orders.reports_from(
            await self._http.get(SUPER_ORDERS_PATH),
            self._instrument_for,
            self.account_id,
            self._clock.timestamp_ns(),
        )

    async def submit_forever_order(
        self,
        command: SubmitOrder,
        *,
        product_type: str = PRODUCT_CNC,
        second_leg: Order | None = None,
    ) -> None:
        """Rest an order past the close, which /v2/orders cannot do.

        Not reachable from `_submit_order`, and deliberately: Nautilus has no
        Good-Till-Triggered concept, so routing a GTC order here would turn a
        request to rest at the exchange into a request to rest at the broker
        behind a trigger. Those are different orders. A caller who wants one
        asks for it.
        """
        order = command.order
        instrument = self._cache.instrument(order.instrument_id)
        gate = submission_refusal(self._config, os.environ)
        if gate is None and instrument is None:
            gate = f"{order.instrument_id} is not in the cache"
        if gate is None and self.is_degraded_by_ip:
            gate = "this client is degraded: Dhan refused an earlier order with 'Invalid IP'."

        payload = None
        if gate is None:
            try:
                payload = forever_orders.place_payload(
                    order=order, instrument=instrument,
                    security_id=self._provider.security_id_for(order.instrument_id),
                    client_id=self._dhan_client_id, product_type=product_type,
                    second_leg=second_leg,
                )
            except (LookupError, Unsendable) as exc:
                gate = str(exc)

        if gate is not None:
            self.generate_order_denied(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=gate,
                ts_event=self._clock.timestamp_ns(),
            )
            return

        self.generate_order_submitted(
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            ts_event=self._clock.timestamp_ns(),
        )
        try:
            body = await self._http.post(FOREVER_ORDERS_PATH, payload)
        except Ambiguous as exc:
            self._log.error(
                f"forever order {order.client_order_id} did not complete and may "
                f"have reached Dhan ({exc}). NO EVENT EMITTED; it may be resting."
            )
            return
        except DhanError as exc:
            if isinstance(exc, IPNotWhitelisted):
                self.is_degraded_by_ip = True
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

    async def cancel_forever_order(self, order_id: str) -> None:
        """Delete a resting forever order. No leg: the whole order goes."""
        try:
            await self._http.delete(forever_orders.cancel_path(order_id))
        except Ambiguous as exc:
            self._log.error(
                f"cancel of forever order {order_id} did not complete and may have "
                f"reached Dhan ({exc}). It may still be resting."
            )

    async def generate_forever_order_reports(self) -> list[OrderStatusReport]:
        """What is resting, from `/v2/forever/orders`.

        Not `/v2/forever/all`, which Dhan's own cURL sample shows and which
        answers 404 -- probed 2026-09-08.
        """
        return forever_orders.reports_from(
            await self._http.get(FOREVER_ORDERS_PATH),
            self._instrument_for,
            self.account_id,
            self._clock.timestamp_ns(),
        )
