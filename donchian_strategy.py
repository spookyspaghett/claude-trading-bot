from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
STATE_PATH = Path("memory/donchian_state.json")


@dataclass
class DonchianPosition:
    symbol: str
    direction: str        # "BUY" or "SELL"
    entry_price: float
    entry_date: str
    stop_price: float
    channel_low: float    # exit long when close < channel_low
    channel_high: float   # exit short when close > channel_high
    peak_price: float     # tracks best price seen since entry (for trailing stop)
    trailing_active: bool = False
    qty: float = 0.0


@dataclass
class ScanResult:
    action: str           # "enter_long" | "enter_short" | "exit" | "hold" | "none"
    symbol: str
    stop_price: float = 0.0
    channel_low: float = 0.0
    channel_high: float = 0.0
    close_price: float = 0.0


class DonchianLiveStrategy:
    """N-day Donchian channel breakout for live daily trading.

    Call scan() once per day after market close (16:05 ET).
    The returned ScanResult tells the runner what to do at tomorrow's open.
    State (open positions + stop prices) is persisted to disk so restarts
    don't lose track of open trades.
    """

    def __init__(
        self,
        lookback_days: int = 40,
        trend_ma: int = 200,
        trailing_activation_pct: float = 1.0,
        trailing_pct: float = 8.0,
        long_only: bool = True,
    ) -> None:
        self.lookback = lookback_days
        self.trend_ma = trend_ma
        self._trailing_act = trailing_activation_pct / 100.0
        self._trailing_pct = trailing_pct / 100.0
        self.long_only = long_only
        self._positions: dict[str, DonchianPosition] = {}
        self._load_state()

    # ── persistence ───────────────────────────────────────────────────────────

    def _load_state(self) -> None:
        if STATE_PATH.exists():
            try:
                raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                for sym, d in raw.get("positions", {}).items():
                    self._positions[sym] = DonchianPosition(**d)
            except Exception:
                pass

    def _save_state(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps(
                {"positions": {s: asdict(p) for s, p in self._positions.items()}},
                indent=2,
            ),
            encoding="utf-8",
        )

    # ── EOD scan (call after market close) ───────────────────────────────────

    def scan(self, symbol: str, bars: list) -> ScanResult:
        """Analyse today's daily bar vs the Donchian channel.

        bars: list of Alpaca Bar objects (or any obj with .open/.high/.low/.close),
              sorted oldest→newest, must contain at least lookback+trend_ma+20 bars.
        Returns a ScanResult describing what action to take at tomorrow's open.
        """
        needed = self.lookback + max(self.trend_ma, 1) + 5
        if len(bars) < needed:
            return ScanResult(action="none", symbol=symbol)

        closes = [float(b.close) for b in bars]
        highs  = [float(b.high)  for b in bars]
        lows   = [float(b.low)   for b in bars]

        today_close = closes[-1]
        today_high  = highs[-1]
        today_low   = lows[-1]

        # Channel uses bars BEFORE today (no look-ahead)
        window_highs = highs[-self.lookback - 1: -1]
        window_lows  = lows[-self.lookback - 1: -1]
        ch_high = max(window_highs)
        ch_low  = min(window_lows)

        # Slow trend MA filter (e.g. 200-day)
        if self.trend_ma > 0 and len(closes) >= self.trend_ma + 1:
            ma = sum(closes[-self.trend_ma - 1: -1]) / self.trend_ma
            trend_up   = today_close > ma
            trend_down = today_close < ma
        else:
            trend_up = trend_down = True

        # ATR-based stop distance (14-period)
        trs = []
        for j in range(1, min(15, len(bars))):
            b, prev = bars[-j], bars[-j - 1]
            trs.append(max(
                float(b.high) - float(b.low),
                abs(float(b.high) - float(prev.close)),
                abs(float(b.low)  - float(prev.close)),
            ))
        atr = sum(trs) / len(trs) if trs else today_close * 0.01
        stop_dist = round(atr * 1.5, 2)

        # ── Check existing position ───────────────────────────────────────────
        pos = self._positions.get(symbol)
        if pos is not None:
            # Update trailing stop
            if pos.direction == "BUY":
                pos.peak_price = max(pos.peak_price, today_high)
                if (today_close - pos.entry_price) / pos.entry_price >= self._trailing_act:
                    pos.trailing_active = True
                if pos.trailing_active:
                    new_stop = round(pos.peak_price * (1.0 - self._trailing_pct), 2)
                    pos.stop_price = max(pos.stop_price, new_stop)
            else:
                pos.peak_price = min(pos.peak_price, today_low)
                if (pos.entry_price - today_close) / pos.entry_price >= self._trailing_act:
                    pos.trailing_active = True
                if pos.trailing_active:
                    new_stop = round(pos.peak_price * (1.0 + self._trailing_pct), 2)
                    pos.stop_price = min(pos.stop_price, new_stop)

            # Channel reverse exit
            channel_exit = (
                pos.direction == "BUY"  and today_close < ch_low
                or pos.direction == "SELL" and today_close > ch_high
            )
            if channel_exit:
                del self._positions[symbol]
                self._save_state()
                return ScanResult(action="exit", symbol=symbol,
                                  channel_low=ch_low, channel_high=ch_high,
                                  close_price=today_close)

            self._save_state()
            return ScanResult(action="hold", symbol=symbol,
                              stop_price=pos.stop_price,
                              channel_low=ch_low, channel_high=ch_high,
                              close_price=today_close)

        # ── Entry signals ─────────────────────────────────────────────────────
        if today_close > ch_high and trend_up:
            stop = round(today_close - stop_dist, 2)
            self._positions[symbol] = DonchianPosition(
                symbol=symbol, direction="BUY",
                entry_price=today_close,
                entry_date=str(datetime.now(tz=ET).date()),
                stop_price=stop,
                channel_low=ch_low, channel_high=ch_high,
                peak_price=today_close,
            )
            self._save_state()
            return ScanResult(action="enter_long", symbol=symbol,
                              stop_price=stop, channel_low=ch_low,
                              channel_high=ch_high, close_price=today_close)

        if not self.long_only and today_close < ch_low and trend_down:
            stop = round(today_close + stop_dist, 2)
            self._positions[symbol] = DonchianPosition(
                symbol=symbol, direction="SELL",
                entry_price=today_close,
                entry_date=str(datetime.now(tz=ET).date()),
                stop_price=stop,
                channel_low=ch_low, channel_high=ch_high,
                peak_price=today_close,
            )
            self._save_state()
            return ScanResult(action="enter_short", symbol=symbol,
                              stop_price=stop, channel_low=ch_low,
                              channel_high=ch_high, close_price=today_close)

        return ScanResult(action="none", symbol=symbol,
                          channel_low=ch_low, channel_high=ch_high,
                          close_price=today_close)

    # ── position management ───────────────────────────────────────────────────

    def record_fill(self, symbol: str, qty: float) -> None:
        if symbol in self._positions:
            self._positions[symbol].qty = qty
            self._save_state()

    def remove_position(self, symbol: str) -> None:
        self._positions.pop(symbol, None)
        self._save_state()

    @property
    def open_positions(self) -> dict[str, DonchianPosition]:
        return self._positions
