from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from config_loader import TrendSRConfig
from strategy import Direction, TrendSRStrategy

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bar(symbol: str, high: float, low: float, close: float, i: int) -> Any:
    b = MagicMock()
    b.symbol = symbol
    b.open = close
    b.high = high
    b.low = low
    b.close = close
    b.volume = 1_000
    b.timestamp = _T0 + timedelta(days=i)
    return b


# Filters off + no aggregation so the core entry/exit logic is deterministic.
def _cfg(long_only: bool = True) -> TrendSRConfig:
    return TrendSRConfig(
        bar_minutes=1, ma_fast=3, ma_slow=5, regime_ma=0, pivot_lookback=5,
        pivot_strength=1, atr_period=3, atr_mult=1.5, breakout_buffer_atr=0.0,
        cooldown_bars=0, trailing_activation_pct=2.0, trailing_pct=5.0,
        long_only=long_only,
    )


@pytest.fixture()
def strat() -> TrendSRStrategy:
    return TrendSRStrategy(_cfg(), ["BTC/USD"], trade_24_7=True)


def _feed(strat: TrendSRStrategy, closes: list[float]) -> list[Any]:
    sigs = []
    for i, c in enumerate(closes):
        sig = strat.on_bar(_bar("BTC/USD", c + 1, c - 1, c, i))
        if sig is not None:
            sigs.append(sig)
    return sigs


def test_no_signal_during_warmup(strat: TrendSRStrategy) -> None:
    # Fewer bars than warmup → never a signal.
    assert _feed(strat, [100, 101, 102, 103]) == []


def test_long_breakout_emits_buy(strat: TrendSRStrategy) -> None:
    # Establish a resistance pivot, then break above it in an uptrend.
    sigs = _feed(strat, [100, 101, 99, 102, 98, 103, 101, 104, 102, 108, 112, 115])
    buys = [s for s in sigs if s.direction == Direction.BUY]
    assert buys, "expected a long breakout entry"
    assert buys[0].symbol == "BTC/USD"
    assert buys[0].stop_price < buys[0].entry_price


def test_long_only_never_shorts(strat: TrendSRStrategy) -> None:
    # A steady downtrend must not produce a SELL when long_only is True.
    sigs = _feed(strat, [120, 118, 119, 115, 116, 110, 108, 104, 100, 96, 92, 88])
    assert all(s.direction != Direction.SELL for s in sigs)


def test_exit_after_entry(strat: TrendSRStrategy) -> None:
    # Break out, then collapse — expect a BUY followed by a FLAT exit.
    sigs = _feed(
        strat,
        [100, 101, 99, 102, 98, 103, 101, 104, 102, 108, 112, 115, 118, 120,
         116, 108, 100, 92],
    )
    dirs = [s.direction for s in sigs]
    assert Direction.BUY in dirs
    assert Direction.FLAT in dirs
    # FLAT must come after the BUY.
    assert dirs.index(Direction.FLAT) > dirs.index(Direction.BUY)


def test_unknown_symbol_ignored(strat: TrendSRStrategy) -> None:
    assert strat.on_bar(_bar("ETH/USD", 10, 9, 9.5, 0)) is None


def test_shorts_allowed_when_not_long_only() -> None:
    s = TrendSRStrategy(_cfg(long_only=False), ["BTC/USD"], trade_24_7=True)
    # Downtrend with a bounce that forms a support pivot (~119), then a break below it.
    sigs = _feed(s, [130, 128, 126, 124, 122, 120, 123, 121, 119, 115, 110, 105, 101])
    # In a downtrend with shorts enabled we expect at least one SELL entry.
    assert any(sig.direction == Direction.SELL for sig in sigs)


def test_regime_filter_blocks_buys_in_downtrend() -> None:
    # regime_ma on → a long breakout below the regime MA must be suppressed.
    cfg = TrendSRConfig(
        bar_minutes=1, ma_fast=3, ma_slow=5, regime_ma=8, pivot_lookback=5,
        pivot_strength=1, atr_period=3, atr_mult=1.5, breakout_buffer_atr=0.0,
        cooldown_bars=0, trailing_activation_pct=2.0, trailing_pct=5.0, long_only=True,
    )
    s = TrendSRStrategy(cfg, ["BTC/USD"], trade_24_7=True)
    # Overall downtrend: price stays below the regime EMA, so no BUYs even on pops.
    sigs = _feed(s, [120, 118, 119, 116, 117, 114, 115, 112, 113, 110, 111, 108, 109, 106])
    assert all(sig.direction != Direction.BUY for sig in sigs)


def test_aggregation_groups_minute_bars() -> None:
    # bar_minutes=15 → 15 one-minute bars collapse into a single evaluated candle.
    cfg = TrendSRConfig(
        bar_minutes=15, ma_fast=3, ma_slow=5, regime_ma=0, pivot_lookback=3,
        pivot_strength=1, atr_period=3, atr_mult=1.5, breakout_buffer_atr=0.0,
        cooldown_bars=0, trailing_activation_pct=2.0, trailing_pct=5.0, long_only=True,
    )
    s = TrendSRStrategy(cfg, ["BTC/USD"], trade_24_7=True)
    st = s._state["BTC/USD"]

    def minute_bar(i: int) -> Any:
        b = MagicMock()
        b.symbol = "BTC/USD"; b.open = 100; b.high = 100 + i; b.low = 100; b.close = 100 + i
        b.volume = 1_000
        b.timestamp = _T0 + timedelta(minutes=i)
        return b

    # Feed 30 one-minute bars = buckets [0..14] and [15..29]. The first 15m candle
    # finalizes when minute 15 arrives; the second is still open.
    for i in range(30):
        s.on_bar(minute_bar(i))
    assert len(st.closes) == 1  # exactly one finalized 15m candle evaluated
