from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from config_loader import EmaConfig
from strategy import EMAStrategy

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bar(c: float, i: int):
    b = MagicMock()
    b.symbol = "BTC/USD"; b.open = c; b.high = c; b.low = c; b.close = c
    b.volume = 1000; b.timestamp = _T0 + timedelta(minutes=i)
    return b


def _strat() -> EMAStrategy:
    cfg = EmaConfig(fast_period=3, slow_period=8, entry_order_type="market", eod_exit_time="15:50")
    return EMAStrategy(cfg, ["BTC/USD"], stop_loss_pct=Decimal("1"), trade_24_7=True)


def test_ema_has_warmup_bars() -> None:
    assert _strat().warmup_bars == 8


def test_ema_no_signal_before_warmup() -> None:
    s = _strat()
    # Oscillating prices would cross repeatedly, but no signal until warmed.
    sigs = [s.on_bar(_bar(100 + (5 if i % 2 else -5), i)) for i in range(7)]
    assert all(x is None for x in sigs)


def test_ema_warm_up_primes_without_trading() -> None:
    s = _strat()
    s.warm_up("BTC/USD", [_bar(100 + i, i) for i in range(20)])
    st = s._state["BTC/USD"]
    assert st.count >= s.warmup_bars       # warmed
    assert st.fast_ema is not None and st.slow_ema is not None
    assert st.position == ""               # no position opened during warmup
