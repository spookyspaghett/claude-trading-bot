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


@pytest.fixture()
def strat() -> TrendSRStrategy:
    cfg = TrendSRConfig(
        ma_fast=3, ma_slow=5, pivot_lookback=5, pivot_strength=1,
        atr_period=3, atr_mult=1.5, trailing_activation_pct=2.0,
        trailing_pct=5.0, long_only=True,
    )
    return TrendSRStrategy(cfg, ["BTC/USD"], trade_24_7=True)


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
    cfg = TrendSRConfig(
        ma_fast=3, ma_slow=5, pivot_lookback=5, pivot_strength=1,
        atr_period=3, atr_mult=1.5, trailing_activation_pct=2.0,
        trailing_pct=5.0, long_only=False,
    )
    s = TrendSRStrategy(cfg, ["BTC/USD"], trade_24_7=True)
    # Downtrend with a bounce that forms a support pivot (~119), then a break below it.
    sigs = _feed(s, [130, 128, 126, 124, 122, 120, 123, 121, 119, 115, 110, 105, 101])
    # In a downtrend with shorts enabled we expect at least one SELL entry.
    assert any(sig.direction == Direction.SELL for sig in sigs)
