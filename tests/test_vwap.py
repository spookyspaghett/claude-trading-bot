from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from config_loader import VwapConfig
from strategy import Direction, VWAPRevertStrategy

ET = ZoneInfo("America/New_York")
_T0 = datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc)

# 15 bars of quiet chop around 100 — enough history for VWAP/σ (min_bars=10).
_BASE = [100.0, 100.2, 99.9, 100.1, 100.0, 99.8, 100.2, 100.1,
         99.9, 100.0, 100.1, 99.9, 100.0, 100.2, 99.8]


def _bar(close: float, i: int, symbol: str = "BTC/USD") -> Any:
    b = MagicMock()
    b.symbol = symbol
    b.open = close
    b.high = close + 0.05
    b.low = close - 0.05
    b.close = close
    b.volume = 1000
    b.timestamp = _T0 + timedelta(minutes=i)
    return b


def _strat(**overrides: Any) -> VWAPRevertStrategy:
    cfg = VwapConfig(min_bars=10, dev_window=30, **overrides)
    return VWAPRevertStrategy(config=cfg, symbols=["BTC/USD"], trade_24_7=True)


def _feed(s: VWAPRevertStrategy, prices: list[float]) -> list[Any]:
    return [s.on_bar(_bar(p, i)) for i, p in enumerate(prices)]


# ── Warmup gate ───────────────────────────────────────────────────────────────


def test_no_entry_before_min_bars() -> None:
    s = _strat()
    # A huge dip on bar 6 would trigger an entry, but σ needs min_bars history.
    sigs = _feed(s, _BASE[:5] + [90.0])
    assert all(x is None for x in sigs)


# ── Entry / exits ─────────────────────────────────────────────────────────────


def test_long_entry_below_band_with_stop_below_entry() -> None:
    s = _strat()
    sigs = _feed(s, _BASE + [97.5])
    sig = sigs[-1]
    assert sig is not None
    assert sig.direction == Direction.BUY
    assert sig.entry_price == Decimal("97.5")
    assert sig.stop_price < sig.entry_price


def test_target_exit_when_back_at_vwap() -> None:
    s = _strat()
    sigs = _feed(s, _BASE + [97.5, 97.2, 100.1])
    assert sigs[-1] is not None
    assert sigs[-1].direction == Direction.FLAT
    assert "target" in sigs[-1].reason


def test_stop_exit_when_low_breaches_stop() -> None:
    s = _strat()
    sigs = _feed(s, _BASE + [97.5, 95.0])
    assert sigs[-1] is not None
    assert sigs[-1].direction == Direction.FLAT
    assert "stop" in sigs[-1].reason


def test_no_duplicate_entry_while_in_position() -> None:
    s = _strat()
    sigs = _feed(s, _BASE + [97.5, 97.4])
    assert sigs[-2] is not None and sigs[-2].direction == Direction.BUY
    assert sigs[-1] is None  # still long, dip continues — manage, don't re-enter


# ── Long-only / short side ────────────────────────────────────────────────────


def test_long_only_blocks_short() -> None:
    s = _strat(long_only=True)
    sigs = _feed(s, _BASE + [102.5])
    assert sigs[-1] is None


def test_short_entry_when_long_only_off() -> None:
    s = _strat(long_only=False)
    sigs = _feed(s, _BASE + [102.5])
    sig = sigs[-1]
    assert sig is not None
    assert sig.direction == Direction.SELL
    assert sig.stop_price > sig.entry_price


# ── Per-day trade cap ─────────────────────────────────────────────────────────


def test_max_trades_per_day_caps_entries() -> None:
    s = _strat(max_trades_per_day=1)
    # Round trip (entry + target exit), then a second qualifying dip.
    sigs = _feed(s, _BASE + [97.5, 100.1, 100.0, 97.0])
    assert sigs[-1] is None
    assert s._state["BTC/USD"].trades_today == 1


# ── Session rollover ──────────────────────────────────────────────────────────


def test_new_session_reanchors_vwap_and_resets_counters() -> None:
    s = _strat(max_trades_per_day=1)
    _feed(s, _BASE + [97.5, 100.1])  # one entry+exit, cap reached
    st = s._state["BTC/USD"]
    assert st.trades_today == 1
    # First bar of the next UTC day re-anchors everything.
    s.on_bar(_bar(100.0, 24 * 60))
    assert st.trades_today == 0
    assert len(st.devs) == 1
