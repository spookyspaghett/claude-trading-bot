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
    from config_loader import EmaConfig, OrbConfig, TrendSRConfig

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

    # Number of bars of history the strategy needs before it can trade.
    warmup_bars: int = 0

    def warm_up(self, symbol: str, bars: list[Bar | AggregatedBar]) -> None:
        """Prime indicators from historical bars without trading. No-op by default."""
        return


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
    count: int = 0                       # bars processed (for the warmup gate)


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
        trade_24_7: bool = False,
    ) -> None:
        self._fast_k = Decimal("2") / (Decimal(str(config.fast_period)) + 1)
        self._slow_k = Decimal("2") / (Decimal(str(config.slow_period)) + 1)
        self._fast_period = config.fast_period
        self._slow_period = config.slow_period
        self._stop_loss_pct = stop_loss_pct
        self._trade_24_7 = trade_24_7
        # Need ~slow_period bars before the EMAs are meaningful (#8): don't trade
        # until warmed, so a cross can't fire on noise on the 2nd bar.
        self._warmup = config.slow_period
        self._state: dict[str, _EMAState] = {s: _EMAState() for s in symbols}
        eod_h, eod_m = config.eod_exit_time.split(":")
        self._eod_time: time = time(int(eod_h), int(eod_m))

    @staticmethod
    def _to_et(bar: Bar | AggregatedBar) -> datetime:
        ts = bar.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ZoneInfo("UTC"))
        return ts.astimezone(ET)

    @property
    def warmup_bars(self) -> int:
        return self._warmup

    def _step_ema(self, state: _EMAState, close: Decimal) -> bool | None:
        """Update the EMAs and bar count; return fast>slow, or None when seeding."""
        state.count += 1
        if state.fast_ema is None or state.slow_ema is None:
            state.fast_ema = close
            state.slow_ema = close
            state.fast_was_above = state.fast_ema > state.slow_ema
            return None
        one = Decimal("1")
        state.fast_ema = close * self._fast_k + state.fast_ema * (one - self._fast_k)
        state.slow_ema = close * self._slow_k + state.slow_ema * (one - self._slow_k)
        return state.fast_ema > state.slow_ema

    def warm_up(self, symbol: str, bars: list[Bar | AggregatedBar]) -> None:
        """Prime the EMAs from history without trading (#8)."""
        state = self._state.get(symbol)
        if state is None:
            return
        for b in bars:
            self._step_ema(state, Decimal(str(b.close)))

    def on_bar(self, bar: Bar | AggregatedBar) -> Signal | None:
        if isinstance(bar, AggregatedBar):
            return None

        symbol = bar.symbol
        state  = self._state.get(symbol)
        if state is None:
            return None

        bar_et   = self._to_et(bar)
        bar_time = bar_et.time()

        if not self._trade_24_7 and not (MARKET_OPEN <= bar_time < MARKET_CLOSE):
            return None

        close = Decimal(str(bar.close))

        # ── EOD exit (stocks only; crypto trades 24/7) ────────────────────────
        if not self._trade_24_7 and bar_time >= self._eod_time:
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
        fast_above = self._step_ema(state, close)
        if fast_above is None:
            return None   # just seeded

        # Warmup gate: don't trade until the EMAs have enough history (#8).
        if state.count < self._warmup:
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


# ── Trend + Support/Resistance strategy (crypto-oriented) ─────────────────────

@dataclass
class _TrendSRState:
    highs: list[Decimal] = field(default_factory=list)
    lows: list[Decimal] = field(default_factory=list)
    closes: list[Decimal] = field(default_factory=list)
    vols: list[Decimal] = field(default_factory=list)
    fast_ema: Decimal | None = None
    slow_ema: Decimal | None = None
    regime_ema: Decimal | None = None
    resistance: Decimal | None = None
    support: Decimal | None = None
    position: str = ""                  # "" | "BUY" | "SELL"
    entry_price: Decimal = field(default_factory=lambda: Decimal("0"))
    stop_price: Decimal = field(default_factory=lambda: Decimal("0"))
    best_px: Decimal = field(default_factory=lambda: Decimal("0"))
    cooldown: int = 0                   # bars left to wait after an exit
    # Fresh-breakout tracking (used only when an entry filter is active) so a
    # filtered-out breakout is skipped, not merely deferred to a later bar.
    was_above_res: bool = False
    was_below_sup: bool = False
    # Live aggregation of the raw feed into `bar_minutes` candles.
    bucket: int | None = None
    agg_high: Decimal = field(default_factory=lambda: Decimal("0"))
    agg_low: Decimal = field(default_factory=lambda: Decimal("0"))
    agg_close: Decimal = field(default_factory=lambda: Decimal("0"))
    agg_volume: Decimal = field(default_factory=lambda: Decimal("0"))


class TrendSRStrategy(Strategy):
    """Trend-following breakout using moving averages + pivot support/resistance.

    Designed for 24/7 crypto but works on any bar series.

    The raw feed is aggregated into ``bar_minutes`` candles so the strategy runs
    on a sane timeframe (e.g. 15m) instead of noisy 1-minute bars.

    Entry (long): close breaks above the latest resistance by an ATR buffer while
      the fast MA is above the slow MA AND price is above the long-term regime MA
      (so it stays flat in downtrends instead of buying every pop). A cooldown
      after each exit prevents instant re-entry churn.
    Stop: ``max(latest support, entry − ATR × atr_mult)``.
    Exit: trailing stop (after ``trailing_activation_pct`` gain it trails
      ``trailing_pct`` below the peak), or close below the slow MA, or close below
      the latest support. Shorts mirror this and are enabled only when
      ``long_only`` is False (never for crypto, which can't short on Alpaca).

    Optional entry filters (both default OFF — see docs/trend_sr_filters.md):
      • ADX gate (``min_adx`` > 0): require Wilder's ADX ≥ ``min_adx`` so the
        strategy only trades breakouts when a real trend is present, skipping
        chop. ``adx_period`` sets the smoothing window (default 14).
      • Volume confirmation (``volume_mult`` > 0): require the breakout bar's
        volume ≥ ``volume_mult`` × the average volume over ``volume_ma`` bars,
        so low-conviction breakouts are skipped. Silently passes when the data
        feed has no volume.

    Fresh-breakout entries: whenever a filter is active, entries fire ONLY on the
    bar where price first crosses the level (not on every subsequent bar it stays
    beyond it). This makes a rejected filter SKIP the trade instead of merely
    delaying it to a later, worse-priced bar. With both filters off, the legacy
    "enter on any bar beyond the level" behaviour is preserved unchanged.
    """

    def __init__(
        self,
        config: TrendSRConfig,
        symbols: list[str],
        trade_24_7: bool = False,
    ) -> None:
        self._bar_minutes = max(1, config.bar_minutes)
        self._ma_fast = config.ma_fast
        self._ma_slow = config.ma_slow
        self._regime_ma = config.regime_ma
        self._fast_k = Decimal("2") / (Decimal(str(config.ma_fast)) + 1)
        self._slow_k = Decimal("2") / (Decimal(str(config.ma_slow)) + 1)
        self._regime_k = (Decimal("2") / (Decimal(str(config.regime_ma)) + 1)
                          if config.regime_ma > 0 else Decimal("0"))
        self._pivot_lookback = config.pivot_lookback
        self._pivot_strength = config.pivot_strength
        self._atr_period = config.atr_period
        self._atr_mult = Decimal(str(config.atr_mult))
        self._buffer_atr = Decimal(str(config.breakout_buffer_atr))
        self._cooldown_bars = config.cooldown_bars
        self._trail_act = Decimal(str(config.trailing_activation_pct)) / Decimal("100")
        self._trail_pct = Decimal(str(config.trailing_pct)) / Decimal("100")
        self._long_only = config.long_only
        self._trade_24_7 = trade_24_7
        # ── Optional entry filters (0 = disabled) ─────────────────────────────
        self._adx_period = config.adx_period
        self._min_adx = Decimal(str(config.min_adx))
        self._volume_ma = config.volume_ma
        self._volume_mult = Decimal(str(config.volume_mult))
        self._warmup = max(
            self._ma_slow,
            self._regime_ma,
            self._pivot_lookback + 2 * self._pivot_strength + 1,
            self._atr_period + 1,
            (2 * self._adx_period + 1) if self._min_adx > 0 else 0,
            (self._volume_ma + 1) if self._volume_mult > 0 else 0,
        )
        self._maxlen = self._warmup + self._pivot_lookback + 5
        self._state: dict[str, _TrendSRState] = {s: _TrendSRState() for s in symbols}

    @staticmethod
    def _to_et(bar: Bar | AggregatedBar) -> datetime:
        ts = bar.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ZoneInfo("UTC"))
        return ts.astimezone(ET)

    @staticmethod
    def _utc_seconds(bar: Bar | AggregatedBar) -> int:
        ts = bar.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ZoneInfo("UTC"))
        return int(ts.timestamp())

    def _atr(self, st: _TrendSRState) -> Decimal | None:
        n = len(st.closes)
        if n < self._atr_period + 1:
            return None
        trs: list[Decimal] = []
        for i in range(n - self._atr_period, n):
            tr = max(
                st.highs[i] - st.lows[i],
                abs(st.highs[i] - st.closes[i - 1]),
                abs(st.lows[i] - st.closes[i - 1]),
            )
            trs.append(tr)
        return sum(trs, Decimal("0")) / Decimal(str(len(trs)))

    def _adx(self, st: _TrendSRState) -> Decimal | None:
        """Wilder's Average Directional Index over the rolling window.

        Returns None until enough bars exist. Measures trend *strength* (not
        direction): low ADX ⇒ choppy/range-bound, high ADX ⇒ strong trend.
        """
        p = self._adx_period
        n = len(st.closes)
        if n < 2 * p + 1:
            return None

        start = n - (2 * p + 1)
        trs: list[Decimal] = []
        plus_dm: list[Decimal] = []
        minus_dm: list[Decimal] = []
        zero = Decimal("0")
        for i in range(start + 1, n):
            up = st.highs[i] - st.highs[i - 1]
            down = st.lows[i - 1] - st.lows[i]
            plus_dm.append(up if (up > down and up > zero) else zero)
            minus_dm.append(down if (down > up and down > zero) else zero)
            trs.append(max(
                st.highs[i] - st.lows[i],
                abs(st.highs[i] - st.closes[i - 1]),
                abs(st.lows[i] - st.closes[i - 1]),
            ))
        if len(trs) < p:
            return None

        def _wilder(vals: list[Decimal]) -> list[Decimal]:
            acc = sum(vals[:p], zero)
            out = [acc]
            pd = Decimal(str(p))
            for v in vals[p:]:
                acc = acc - (acc / pd) + v
                out.append(acc)
            return out

        s_tr = _wilder(trs)
        s_pdm = _wilder(plus_dm)
        s_mdm = _wilder(minus_dm)
        hundred = Decimal("100")
        dxs: list[Decimal] = []
        for tr_s, pdm_s, mdm_s in zip(s_tr, s_pdm, s_mdm):
            if tr_s == 0:
                continue
            pdi = hundred * pdm_s / tr_s
            mdi = hundred * mdm_s / tr_s
            denom = pdi + mdi
            dxs.append(zero if denom == 0 else hundred * abs(pdi - mdi) / denom)
        if not dxs:
            return None
        recent = dxs[-p:]
        return sum(recent, zero) / Decimal(str(len(recent)))

    def _avg_volume(self, st: _TrendSRState) -> Decimal | None:
        """Average volume of the `volume_ma` bars *before* the current bar.

        Returns None when there isn't enough history or the data has no volume
        (so the volume gate becomes a no-op rather than blocking every trade).
        """
        p = self._volume_ma
        if len(st.vols) < p + 1:
            return None
        window = [v for v in st.vols[-p - 1:-1] if v > 0]
        if not window:
            return None
        return sum(window, Decimal("0")) / Decimal(str(len(window)))

    def _update_pivots(self, st: _TrendSRState) -> None:
        """Confirm a pivot `pivot_strength` bars back and update S/R levels."""
        s = self._pivot_strength
        idx = len(st.highs) - 1 - s          # candidate pivot index
        if idx < s:
            return
        lo = idx - s
        hi = idx + s + 1
        window_highs = st.highs[lo:hi]
        window_lows = st.lows[lo:hi]
        if st.highs[idx] == max(window_highs):
            st.resistance = st.highs[idx]
        if st.lows[idx] == min(window_lows):
            st.support = st.lows[idx]

    def on_bar(self, bar: Bar | AggregatedBar) -> Signal | None:
        # We aggregate raw feed bars ourselves, so ignore the feed's own
        # AggregatedBar to avoid double-counting.
        if isinstance(bar, AggregatedBar):
            return None

        symbol = bar.symbol
        st = self._state.get(symbol)
        if st is None:
            return None

        if not self._trade_24_7:
            bar_time = self._to_et(bar).time()
            if not (MARKET_OPEN <= bar_time < MARKET_CLOSE):
                return None

        high = Decimal(str(bar.high))
        low = Decimal(str(bar.low))
        close = Decimal(str(bar.close))
        vol = Decimal(str(getattr(bar, "volume", 0) or 0))

        # No aggregation: evaluate every bar directly.
        if self._bar_minutes <= 1:
            return self._evaluate(symbol, st, high, low, close, vol)

        # Intrabar stop check (#6): when aggregating (e.g. 15m candles), still
        # check the open position's stop against EVERY raw 1m bar so an adverse
        # spike inside a forming candle exits at the stop instead of waiting for
        # candle close. Fills at the stop price.
        if st.position == "BUY" and low <= st.stop_price:
            st.position = ""
            st.cooldown = self._cooldown_bars
            return Signal(symbol=symbol, direction=Direction.FLAT,
                          entry_price=st.stop_price, stop_price=Decimal("0"),
                          reason="Trend/SR exit (intrabar stop)")
        if st.position == "SELL" and high >= st.stop_price:
            st.position = ""
            st.cooldown = self._cooldown_bars
            return Signal(symbol=symbol, direction=Direction.FLAT,
                          entry_price=st.stop_price, stop_price=Decimal("0"),
                          reason="Trend/SR exit (intrabar stop)")

        # Aggregate into bar_minutes candles by UTC timestamp bucket. Evaluate a
        # candle only once it completes (when a bar from a later bucket arrives).
        bucket = self._utc_seconds(bar) // (self._bar_minutes * 60)
        if st.bucket is None:
            st.bucket = bucket
            st.agg_high, st.agg_low, st.agg_close = high, low, close
            st.agg_volume = vol
            return None
        if bucket == st.bucket:
            st.agg_high = max(st.agg_high, high)
            st.agg_low = min(st.agg_low, low)
            st.agg_close = close
            st.agg_volume += vol
            return None
        # Rollover: finalize the completed candle, then open a new one.
        sig = self._evaluate(symbol, st, st.agg_high, st.agg_low, st.agg_close, st.agg_volume)
        st.bucket = bucket
        st.agg_high, st.agg_low, st.agg_close = high, low, close
        st.agg_volume = vol
        return sig

    @property
    def warmup_bars(self) -> int:
        return self._warmup

    def warm_up(self, symbol: str, bars: list[Bar | AggregatedBar]) -> None:
        """Prime indicators from historical (already-timeframed) bars, no trading."""
        st = self._state.get(symbol)
        if st is None:
            return
        for b in bars:
            self._update_indicators(
                st, Decimal(str(b.high)), Decimal(str(b.low)), Decimal(str(b.close)),
                Decimal(str(getattr(b, "volume", 0) or 0)),
            )

    def _update_indicators(
        self, st: _TrendSRState, high: Decimal, low: Decimal, close: Decimal,
        volume: Decimal = Decimal("0"),
    ) -> None:
        st.highs.append(high)
        st.lows.append(low)
        st.closes.append(close)
        st.vols.append(volume)
        if len(st.highs) > self._maxlen:
            st.highs = st.highs[-self._maxlen:]
            st.lows = st.lows[-self._maxlen:]
            st.closes = st.closes[-self._maxlen:]
            st.vols = st.vols[-self._maxlen:]

        one = Decimal("1")
        if st.fast_ema is None or st.slow_ema is None:
            st.fast_ema = close
            st.slow_ema = close
            st.regime_ema = close
        else:
            fe: Decimal = st.fast_ema
            se: Decimal = st.slow_ema
            st.fast_ema = close * self._fast_k + fe * (one - self._fast_k)
            st.slow_ema = close * self._slow_k + se * (one - self._slow_k)
            if self._regime_k > 0 and st.regime_ema is not None:
                re: Decimal = st.regime_ema
                st.regime_ema = close * self._regime_k + re * (one - self._regime_k)

        # Track pivots every bar (also during warmup) so early S/R isn't missed.
        self._update_pivots(st)

    def _evaluate(
        self, symbol: str, st: _TrendSRState,
        high: Decimal, low: Decimal, close: Decimal, volume: Decimal = Decimal("0"),
    ) -> Signal | None:
        self._update_indicators(st, high, low, close, volume)

        if len(st.closes) < self._warmup:
            return None
        if st.cooldown > 0:
            st.cooldown -= 1

        one = Decimal("1")
        atr = self._atr(st)
        assert st.fast_ema is not None and st.slow_ema is not None

        # ── Fresh-breakout tracking (only matters when a filter is active) ────
        # Update every bar — including while in a position — so that after an
        # exit we don't instantly re-enter a still-extended breakout; we wait
        # for price to drop back and cross the level again.
        filters_active = self._min_adx > 0 or self._volume_mult > 0
        fresh_long = fresh_short = True   # filters off ⇒ legacy behaviour
        if filters_active and atr is not None:
            buf = atr * self._buffer_atr
            above_res = st.resistance is not None and close > st.resistance + buf
            below_sup = st.support is not None and close < st.support - buf
            fresh_long = above_res and not st.was_above_res
            fresh_short = below_sup and not st.was_below_sup
            st.was_above_res = above_res
            st.was_below_sup = below_sup

        # ── Manage an open position ───────────────────────────────────────────
        # Stop hits fill at the stop price; MA/support exits fill at the close —
        # so live (intrabar stop) and backtest price the same exit (#6).
        if st.position == "BUY":
            st.best_px = max(st.best_px, high)
            entry = st.entry_price
            gain = (close - entry) / entry if entry else Decimal("0")
            if gain >= self._trail_act and self._trail_pct > 0:
                trail = st.best_px * (one - self._trail_pct)
                st.stop_price = max(st.stop_price, trail)
            exit_px, reason = None, ""
            if low <= st.stop_price:
                exit_px, reason = st.stop_price, "stop"
            elif close < st.slow_ema or (st.support is not None and close < st.support):
                exit_px, reason = close, "MA/support break"
            if exit_px is not None:
                st.position = ""
                st.cooldown = self._cooldown_bars
                return Signal(symbol=symbol, direction=Direction.FLAT,
                              entry_price=exit_px, stop_price=Decimal("0"),
                              reason=f"Trend/SR exit ({reason})")
            return None

        if st.position == "SELL":
            st.best_px = min(st.best_px, low)
            entry = st.entry_price
            gain = (entry - close) / entry if entry else Decimal("0")
            if gain >= self._trail_act and self._trail_pct > 0:
                trail = st.best_px * (one + self._trail_pct)
                st.stop_price = min(st.stop_price, trail)
            exit_px, reason = None, ""
            if high >= st.stop_price:
                exit_px, reason = st.stop_price, "stop"
            elif close > st.slow_ema or (st.resistance is not None and close > st.resistance):
                exit_px, reason = close, "MA/resistance break"
            if exit_px is not None:
                st.position = ""
                st.cooldown = self._cooldown_bars
                return Signal(symbol=symbol, direction=Direction.FLAT,
                              entry_price=exit_px, stop_price=Decimal("0"),
                              reason=f"Trend/SR exit ({reason})")
            return None

        # ── Look for an entry ─────────────────────────────────────────────────
        if atr is None or st.cooldown > 0:
            return None

        # Optional entry filters (shared by long & short; both default off).
        # ADX gate: only trade breakouts when the trend is strong enough.
        if self._min_adx > 0:
            adx = self._adx(st)
            if adx is None or adx < self._min_adx:
                return None
        # Volume gate: breakout bar volume must beat recent average. When the
        # data has no volume, _avg_volume returns None and the gate is skipped.
        if self._volume_mult > 0:
            avg_vol = self._avg_volume(st)
            if avg_vol is not None and volume < self._volume_mult * avg_vol:
                return None

        stop_dist = atr * self._atr_mult
        buffer = atr * self._buffer_atr
        regime_on = self._regime_ma > 0 and st.regime_ema is not None
        regime = st.regime_ema if regime_on else None

        uptrend = st.fast_ema > st.slow_ema
        regime_up = regime is None or close > regime
        if (st.resistance is not None and close > st.resistance + buffer
                and uptrend and regime_up and fresh_long):
            floor = st.support if st.support is not None else (close - stop_dist)
            st.stop_price = max(floor, close - stop_dist)
            st.entry_price = close
            st.best_px = close
            st.position = "BUY"
            return Signal(symbol=symbol, direction=Direction.BUY,
                          entry_price=close, stop_price=st.stop_price,
                          reason=(f"Trend/SR breakout above {st.resistance} "
                                  f"(MA{self._ma_fast}>{self._ma_slow}, regime up)"))

        regime_dn = regime is None or close < regime
        if (not self._long_only and st.support is not None
                and close < st.support - buffer
                and st.fast_ema < st.slow_ema and regime_dn and fresh_short):
            ceil_ = st.resistance if st.resistance is not None else (close + stop_dist)
            st.stop_price = min(ceil_, close + stop_dist)
            st.entry_price = close
            st.best_px = close
            st.position = "SELL"
            return Signal(symbol=symbol, direction=Direction.SELL,
                          entry_price=close, stop_price=st.stop_price,
                          reason=(f"Trend/SR breakdown below {st.support} "
                                  f"(MA{self._ma_fast}<{self._ma_slow}, regime down)"))

        return None

    def reset_day(self) -> None:
        """No-op: Trend/SR is a multi-day swing strategy; state persists."""
        return
