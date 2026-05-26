from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from alpaca.data.models import Bar

from data import AggregatedBar

if TYPE_CHECKING:
    from config_loader import EmaConfig, OrbConfig

ET = ZoneInfo("America/New_York")
MARKET_OPEN: time = time(9, 30)
MARKET_CLOSE: time = time(16, 0)


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    FLAT = "FLAT"


@dataclass(frozen=True)
class Signal:
    symbol: str
    direction: Direction
    entry_price: Decimal
    stop_price: Decimal
    reason: str


class Strategy(abc.ABC):
    @abc.abstractmethod
    def on_bar(self, bar: Bar | AggregatedBar) -> Signal | None: ...

    @abc.abstractmethod
    def reset_day(self) -> None: ...


@dataclass
class _ORBState:
    range_high: Decimal = field(default_factory=lambda: Decimal("0"))
    range_low: Decimal = field(default_factory=lambda: Decimal("Inf"))
    range_complete: bool = False
    long_triggered: bool = False
    short_triggered: bool = False
    flat_sent: bool = False


class ORBStrategy(Strategy):
    """Opening-Range Breakout strategy.

    Observes the first `opening_range_minutes` minutes of the session
    to establish a high/low range, then fires BUY on a close above the
    high or SELL on a close below the low.  Each direction fires at most
    once per day per symbol.  All positions are signalled FLAT at EOD.
    """

    def __init__(
        self,
        config: OrbConfig,
        symbols: list[str],
        stop_loss_pct: Decimal,
    ) -> None:
        self._orb_minutes = config.opening_range_minutes
        self._stop_loss_pct = stop_loss_pct
        self._state: dict[str, _ORBState] = {s: _ORBState() for s in symbols}
        self._range_end: time = self._build_range_end_time()

        eod_h, eod_m = config.eod_exit_time.split(":")
        self._eod_time: time = time(int(eod_h), int(eod_m))

    def _build_range_end_time(self) -> time:
        base = datetime(2000, 1, 1, 9, 30, 0)
        end = base + timedelta(minutes=self._orb_minutes)
        return end.time()

    @staticmethod
    def _to_et(bar: Bar | AggregatedBar) -> datetime:
        ts = bar.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ZoneInfo("UTC"))
        return ts.astimezone(ET)

    def on_bar(self, bar: Bar | AggregatedBar) -> Signal | None:
        # Strategy only processes raw 1-minute bars.
        if isinstance(bar, AggregatedBar):
            return None

        symbol = bar.symbol
        state = self._state.get(symbol)
        if state is None:
            return None

        bar_et = self._to_et(bar)
        bar_time = bar_et.time()

        if not (MARKET_OPEN <= bar_time < MARKET_CLOSE):
            return None

        # ── EOD exit ─────────────────────────────────────────────────────────
        if bar_time >= self._eod_time:
            if state.flat_sent:
                return None
            if not state.long_triggered and not state.short_triggered:
                return None  # no position to flatten
            state.flat_sent = True
            return Signal(
                symbol=symbol,
                direction=Direction.FLAT,
                entry_price=Decimal(str(bar.close)),
                stop_price=Decimal("0"),
                reason="EOD exit",
            )

        # ── Opening range accumulation ────────────────────────────────────────
        if MARKET_OPEN <= bar_time < self._range_end:
            high = Decimal(str(bar.high))
            low = Decimal(str(bar.low))
            state.range_high = max(state.range_high, high)
            state.range_low = min(state.range_low, low)
            return None

        # ── Mark range complete on the first post-range bar ──────────────────
        if not state.range_complete:
            if state.range_high == Decimal("0") or state.range_low == Decimal("Inf"):
                return None  # no bars arrived during the opening range; skip
            state.range_complete = True

        close = Decimal(str(bar.close))

        # ── Long breakout ─────────────────────────────────────────────────────
        if close > state.range_high and not state.long_triggered:
            state.long_triggered = True
            stop = self._stop_price(close, Direction.BUY)
            return Signal(
                symbol=symbol,
                direction=Direction.BUY,
                entry_price=close,
                stop_price=stop,
                reason=f"ORB long breakout above {state.range_high}",
            )

        # ── Short breakdown ───────────────────────────────────────────────────
        if close < state.range_low and not state.short_triggered:
            state.short_triggered = True
            stop = self._stop_price(close, Direction.SELL)
            return Signal(
                symbol=symbol,
                direction=Direction.SELL,
                entry_price=close,
                stop_price=stop,
                reason=f"ORB short breakdown below {state.range_low}",
            )

        return None

    def _stop_price(self, price: Decimal, direction: Direction) -> Decimal:
        factor = self._stop_loss_pct / Decimal("100")
        if direction == Direction.BUY:
            return (price * (Decimal("1") - factor)).quantize(Decimal("0.01"))
        return (price * (Decimal("1") + factor)).quantize(Decimal("0.01"))

    def reset_day(self) -> None:
        self._state = {s: _ORBState() for s in self._state}


# ── EMA Crossover strategy ────────────────────────────────────────────────────

@dataclass
class _EMAState:
    fast_ema: Decimal | None = None
    slow_ema: Decimal | None = None
    fast_was_above: bool | None = None   # cross state on the previous bar
    position: str = ""                   # "" | "BUY" | "SELL"
    pending_entry: str = ""              # entry to fire next bar after a reversal flat
    eod_sent: bool = False


class EMAStrategy(Strategy):
    """Exponential Moving Average crossover strategy.

    Fires BUY when the fast EMA crosses above the slow EMA (golden cross)
    and SELL when it crosses below (death cross).  On a reversal while
    already in a position, it flattens first and re-enters one bar later
    (if the cross still holds) to avoid same-bar entry/exit conflicts.

    EMA values persist across days (correct behaviour for a running average).
    Position flags are reset at the start of each new trading day via
    reset_day().
    """

    def __init__(
        self,
        config: EmaConfig,
        symbols: list[str],
        stop_loss_pct: Decimal,
    ) -> None:
        self._fast_k = Decimal("2") / (Decimal(str(config.fast_period)) + 1)
        self._slow_k = Decimal("2") / (Decimal(str(config.slow_period)) + 1)
        self._fast_period = config.fast_period
        self._slow_period = config.slow_period
        self._stop_loss_pct = stop_loss_pct
        self._state: dict[str, _EMAState] = {s: _EMAState() for s in symbols}
        eod_h, eod_m = config.eod_exit_time.split(":")
        self._eod_time: time = time(int(eod_h), int(eod_m))

    @staticmethod
    def _to_et(bar: Bar | AggregatedBar) -> datetime:
        ts = bar.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ZoneInfo("UTC"))
        return ts.astimezone(ET)

    def on_bar(self, bar: Bar | AggregatedBar) -> Signal | None:
        if isinstance(bar, AggregatedBar):
            return None

        symbol = bar.symbol
        state  = self._state.get(symbol)
        if state is None:
            return None

        bar_et   = self._to_et(bar)
        bar_time = bar_et.time()

        if not (MARKET_OPEN <= bar_time < MARKET_CLOSE):
            return None

        close = Decimal(str(bar.close))

        # ── EOD exit ──────────────────────────────────────────────────────────
        if bar_time >= self._eod_time:
            if not state.eod_sent and state.position != "":
                state.eod_sent      = True
                state.position      = ""
                state.pending_entry = ""
                return Signal(
                    symbol=symbol, direction=Direction.FLAT,
                    entry_price=close, stop_price=Decimal("0"),
                    reason="EOD exit",
                )
            return None

        # ── Update EMAs ───────────────────────────────────────────────────────
        if state.fast_ema is None:
            state.fast_ema      = close
            state.slow_ema      = close
            state.fast_was_above = (state.fast_ema > state.slow_ema)
            return None

        state.fast_ema = close * self._fast_k + state.fast_ema * (Decimal("1") - self._fast_k)
        state.slow_ema = close * self._slow_k + state.slow_ema * (Decimal("1") - self._slow_k)

        fast_above = state.fast_ema > state.slow_ema

        if state.fast_was_above is None:
            state.fast_was_above = fast_above
            return None

        # ── Deferred entry (1 bar after a reversal flat) ──────────────────────
        if state.pending_entry:
            entry              = state.pending_entry
            state.pending_entry = ""
            wants_long = entry == "BUY"
            if (wants_long and fast_above) or (not wants_long and not fast_above):
                state.position = entry
                direction      = Direction.BUY if wants_long else Direction.SELL
                stop           = self._stop_price(close, direction)
                label          = "above" if wants_long else "below"
                return Signal(
                    symbol=symbol, direction=direction,
                    entry_price=close, stop_price=stop,
                    reason=(
                        f"EMA({self._fast_period}) {label} EMA({self._slow_period})"
                        " — re-entry after reversal"
                    ),
                )
            return None

        # ── Detect cross ──────────────────────────────────────────────────────
        prev_above           = state.fast_was_above
        state.fast_was_above = fast_above

        if fast_above == prev_above:
            return None  # no cross this bar

        if fast_above:
            # Golden cross — fast crossed above slow
            if state.position == "SELL":
                state.position      = ""
                state.pending_entry = "BUY"
                return Signal(
                    symbol=symbol, direction=Direction.FLAT,
                    entry_price=close, stop_price=Decimal("0"),
                    reason=(
                        f"EMA({self._fast_period}) crossed above EMA({self._slow_period})"
                        " — exit short"
                    ),
                )
            if state.position == "":
                state.position = "BUY"
                stop = self._stop_price(close, Direction.BUY)
                return Signal(
                    symbol=symbol, direction=Direction.BUY,
                    entry_price=close, stop_price=stop,
                    reason=f"EMA({self._fast_period}) crossed above EMA({self._slow_period})",
                )
        else:
            # Death cross — fast crossed below slow
            if state.position == "BUY":
                state.position      = ""
                state.pending_entry = "SELL"
                return Signal(
                    symbol=symbol, direction=Direction.FLAT,
                    entry_price=close, stop_price=Decimal("0"),
                    reason=(
                        f"EMA({self._fast_period}) crossed below EMA({self._slow_period})"
                        " — exit long"
                    ),
                )
            if state.position == "":
                state.position = "SELL"
                stop = self._stop_price(close, Direction.SELL)
                return Signal(
                    symbol=symbol, direction=Direction.SELL,
                    entry_price=close, stop_price=stop,
                    reason=f"EMA({self._fast_period}) crossed below EMA({self._slow_period})",
                )

        return None

    def _stop_price(self, price: Decimal, direction: Direction) -> Decimal:
        factor = self._stop_loss_pct / Decimal("100")
        if direction == Direction.BUY:
            return (price * (Decimal("1") - factor)).quantize(Decimal("0.01"))
        return (price * (Decimal("1") + factor)).quantize(Decimal("0.01"))

    def reset_day(self) -> None:
        """Reset intraday position flags. EMA values intentionally persist."""
        for state in self._state.values():
            state.position      = ""
            state.pending_entry = ""
            state.eod_sent      = False
