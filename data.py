from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from alpaca.data.live import CryptoDataStream, StockDataStream
from alpaca.data.models import Bar

from logger import log_error, log_info

if TYPE_CHECKING:
    from config_loader import Config


@dataclass(frozen=True)
class AggregatedBar:
    """N-minute bar built by aggregating 1-minute bars."""

    symbol: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    timestamp: datetime   # timestamp of the first constituent 1m bar
    period_minutes: int


class BarAggregator:
    """Accumulates 1-minute bars and emits an AggregatedBar every N bars."""

    def __init__(self, symbol: str, period_minutes: int) -> None:
        self._symbol = symbol
        self._period = period_minutes
        self._bars: list[Bar] = []

    def add(self, bar: Bar) -> AggregatedBar | None:
        self._bars.append(bar)
        if len(self._bars) < self._period:
            return None
        result = self._aggregate()
        self._bars.clear()
        return result

    def _aggregate(self) -> AggregatedBar:
        bars = self._bars
        ts = bars[0].timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return AggregatedBar(
            symbol=self._symbol,
            open=Decimal(str(bars[0].open)),
            high=max(Decimal(str(b.high)) for b in bars),
            low=min(Decimal(str(b.low)) for b in bars),
            close=Decimal(str(bars[-1].close)),
            volume=sum(int(b.volume) for b in bars),
            timestamp=ts,
            period_minutes=self._period,
        )


class DataFeed:
    """Wraps StockDataStream; delivers 1m Bars and 5m AggregatedBars via a queue."""

    def __init__(self, config: Config) -> None:
        self._api_key = config.alpaca_api_key
        self._secret_key = config.alpaca_secret_key
        self._is_crypto = config.asset_class == "crypto"
        self._symbols = list(config.symbols)
        self._queue: asyncio.Queue[Bar | AggregatedBar] = asyncio.Queue(maxsize=1000)
        self._aggregators_5m: dict[str, BarAggregator] = {
            s: BarAggregator(s, 5) for s in config.symbols
        }
        self._connected: bool = False
        self._last_bar_at: datetime | None = None

    @property
    def queue(self) -> asyncio.Queue[Bar | AggregatedBar]:
        return self._queue

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_bar_at(self) -> datetime | None:
        """When the last bar arrived. A connected-but-silent stream is the
        failure mode that used to be invisible, so surface it for the heartbeat."""
        return self._last_bar_at

    async def _on_bar(self, bar: Bar) -> None:
        self._last_bar_at = datetime.now(tz=UTC)
        try:
            self._queue.put_nowait(bar)
        except asyncio.QueueFull:
            pass  # drop oldest rather than block — strategy catches up on next bar

        symbol = bar.symbol
        if symbol in self._aggregators_5m:
            agg = self._aggregators_5m[symbol].add(bar)
            if agg is not None:
                try:
                    self._queue.put_nowait(agg)
                except asyncio.QueueFull:
                    pass

    @staticmethod
    def _close(stream: Any) -> None:
        """Best-effort: close the websocket so we don't leak a connection."""
        if stream is None:
            return
        try:
            stream.stop()
        except Exception:
            pass

    async def run(self) -> None:
        """Connect and stream bars; reconnect with exponential backoff on failure."""
        delay = 1.0
        max_delay = 60.0
        while True:
            stream: Any = None
            try:
                if self._is_crypto:
                    stream = CryptoDataStream(self._api_key, self._secret_key)
                else:
                    stream = StockDataStream(self._api_key, self._secret_key)
                stream.subscribe_bars(self._on_bar, *self._symbols)
                self._connected = True
                log_info("data_feed_connected", symbols=self._symbols)
                delay = 1.0
                # stream.run() is a *synchronous* wrapper that calls
                # asyncio.run() / loop.run_until_complete() internally — it
                # cannot be awaited from inside a running event loop.
                # _run_forever() is the actual async coroutine underneath.
                await stream._run_forever()
                # Returning normally means the server closed the socket (session
                # end, server-side disconnect). This used to fall straight through
                # to a zero-delay reconnect while `connected` stayed True — so a
                # dead feed reported itself healthy and hammered Alpaca until it
                # hit the connection limit, and the bot went silent for good.
                log_error("data_feed_disconnected", reason="stream_closed")
            except asyncio.CancelledError:
                self._connected = False
                self._close(stream)
                raise
            except Exception as exc:
                # Was swallowed silently: an auth failure or connection-limit
                # rejection left no trace anywhere the operator could see it.
                log_error("data_feed_error", error=str(exc))
            self._connected = False
            self._close(stream)
            log_info("data_feed_reconnecting", retry_in_s=delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)
