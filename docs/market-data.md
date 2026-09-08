# Market data

> ## The streaming client is not wired
>
> `DhanDataClient` defines `__init__` and nothing else. `_connect` and every
> `_subscribe_*` fall through to `LiveMarketDataClient`, which raises
> `NotImplementedError`. **A node that registers the data factory builds, and
> then fails the moment it starts.**
>
> ```python
> >>> client = DhanLiveDataClientFactory.create(...)   # succeeds
> >>> await client._connect()
> NotImplementedError: implement the `_connect` coroutine
> ```
>
> `examples/dhan_market_data.py` therefore does not run to completion, and
> neither does the node in [Getting started](getting-started.md).
>
> Everything *underneath* the client is built and tested — the binary decoder,
> the subscription protocol, the tick parsers, the instrument master. What is
> missing is the code that joins them. Wiring it is
> [milestone 0.2](../ROADMAP.md), and it is the most self-contained piece of
> work on the roadmap.

This page documents what does exist, because it is usable directly and because
whoever wires the client will want to know what they already have.

---

## The instrument master — works

`DhanInstrumentProvider` downloads and parses Dhan's detailed scrip master:
around 200,000 contracts across NSE, BSE and MCX.

```python
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_india.dhan.providers import DhanInstrumentProvider

provider = DhanInstrumentProvider(
    config=InstrumentProviderConfig(
        load_all=True,
        # ~200,000 contracts. Narrowing this is the difference between a
        # two-second start and holding every Indian contract in the cache.
        filters={"underlyings": {"NIFTY"}},
    ),
)
await provider.load_all_async(filters={"underlyings": {"NIFTY"}})
```

Two filters are understood: `underlyings` (a set of underlying names) and
`instrument_classes` (a set of `InstrumentClass`). The download is a ~32 MB
CSV, so filtering matters.

It also maps both ways between Dhan's ids and Nautilus's:

```python
provider.security_id_for(instrument_id)       # '49081'
provider.instrument_id_for("49081", segment)  # InstrumentId(...) or None
```

The reverse direction is keyed on the **segment code**, not the segment name,
because that is what arrives in the binary feed header.

### Known gap: NSE commodity options are listed but not subscribable

Dhan publishes no market-feed segment code for the NSE commodity segment, so
its 23,870 `OPTFUT` contracts appear in the master and cannot be subscribed.
Dhan's own SDK cannot address them either — it enumerates segments 0–5, 7 and
8, leaving 6 unassigned.

Guessing would be worse than refusing. A wrong segment names a *different*
instrument, and Dhan answers HTTP 200 with empty data for one that does not
exist — so a guess would look like a quiet market rather than an error.

---

## The binary decoder — works

Dhan's feed sends **several packets per frame**. This is where the vendor SDK
fails: `dhanhq` 2.2.0's `marketfeed.process_data` unpacks `data[0:1]`,
dispatches, and returns a single `process_*` call, with no offset loop over
the header's `message_length`. Every packet after the first in a frame is
silently discarded.

```python
from nautilus_india.dhan.binary import decode_frame, Decoder

packets = decode_frame(frame_bytes)   # every packet, not just the first
```

`Decoder` is the stateful version, and its counters are evidence rather than
diagnostics for their own sake:

```python
decoder = Decoder()
packets = decoder.frame(frame_bytes)

decoder.frames               # frames seen
decoder.packets              # packets recovered
decoder.multi_packet_frames  # frames a naive reader would have truncated
decoder.length_mismatches    # settles whether message_length includes the header
decoder.truncated            # frames that ended mid-packet
decoder.unknown_codes        # Counter of packet codes not understood
```

`multi_packet_frames` measures exactly what the vendor SDK loses.

Packet codes: `2` ticker, `4` quote, `5` open interest, `6` previous close,
`7` status, `8` full (with 5-level depth), `50` disconnect. 20-level depth
arrives on a separate socket and through `decode_depth20_frame`.

Prices come off the wire as floats and are converted with `to_price(raw,
precision)`, which rounds at the instrument's precision rather than
stringifying — `dhanhq` renders every price as `"{:.2f}".format(...)`, a
string, and renders `LTT` with `strftime('%H:%M:%S')`, losing the date, the
timezone and the sub-second. Neither can timestamp a Nautilus tick.

---

## The subscription protocol — works

```python
from nautilus_india.dhan.ws import subscription_packet, chunked, feed_url, redacted_feed_url
from nautilus_india.dhan.constants import REQUEST_QUOTE, SEGMENT_NSE_FNO

packet = subscription_packet("CLIENT123", [(SEGMENT_NSE_FNO, "49081")], REQUEST_QUOTE)
len(packet)   # 2187 -- padded to Dhan's fixed group size of 100 regardless

for group in chunked(instruments):   # Dhan accepts at most 100 per packet
    await socket.send(subscription_packet(client_id, group, REQUEST_QUOTE))
```

Request codes: `REQUEST_TICKER` 15, `REQUEST_QUOTE` 17, `REQUEST_DEPTH` 19,
`REQUEST_FULL` 21, `REQUEST_DISCONNECT` 12.

Neither helper truncates. More than 100 instruments raises rather than
subscribing to a subset and reporting success; a security id too long for its
field raises rather than being cut, because a truncated id names a different
contract and Dhan answers 200 for an unknown one.

**The feed URL is a credential.** The v2 handshake carries the access token as
a query parameter, so `feed_url` must never reach a log. `redacted_feed_url`
is the one to print:

```python
redacted_feed_url(token, "CLIENT123")
# 'wss://api-feed.dhan.co?version=2&token=<redacted>&clientId=CLIENT123&authType=2'
```

---

## The tick parsers — work

```python
from nautilus_india.dhan.data import trade_from_ticker, quote_from_full

trade_from_ticker(packet, instrument, ts_init)   # TradeTick, or None
quote_from_full(packet, instrument, ts_init)     # QuoteTick, or None
```

Both return `None` rather than inventing data — a packet with no last price
yields no trade; a full packet with an empty or one-sided book yields no
quote. `ts_event` comes from the exchange timestamp, not from arrival.

**No aggressor side is claimed.** Dhan's ticker does not say who crossed the
spread, and guessing would put a fabricated field into the message bus.

---

## What wiring the client requires

For anyone picking up milestone 0.2, the shape is:

| Hook | Needs |
| --- | --- |
| `_connect` / `_disconnect` | Open `MARKET_FEED_WSS` with `feed_url`, load the provider, start the read loop |
| `_subscribe_quote_ticks` / `_subscribe_trade_ticks` | `security_id_for` → `chunked` → `subscription_packet` |
| the read loop | `Decoder.frame` → parser → `self._handle_data` |
| reconnect | Resubscribe everything; a stream that dies quietly leaves an engine that looks healthy and learns nothing |
| `_subscribe_order_book_deltas` | The separate `DEPTH_FEED_WSS` socket and `decode_depth20_frame` |
| `_request_bars` | `GET /v2/charts/*`, which is a REST path rather than the socket |

The execution client's `_run_order_update_stream` is a working example of the
socket lifecycle this needs, including how it reports a dropped stream loudly
rather than silently.
