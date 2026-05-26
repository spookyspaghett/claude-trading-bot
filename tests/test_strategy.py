from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from config_loader import OrbConfig
from strategy import Direction, ORBStrategy

ET = ZoneInfo("America/New_York")


def _bar(
    symbol: str,
    open_: float,
    high: float,
    low: float,
    close: float,
    hour: int,
    minute: int,
) -> Any:
    """Build a mock Bar at a fixed ET time on an arbitrary trading day."""
    ts_et = datetime(2024, 6, 3, hour, minute, 0, tzinfo=ET)
    b = MagicMock()
    b.symbol = symbol
    b.open = open_
    b.high = high
    b.low = low
    b.close = close
    b.volume = 100_000
    b.timestamp = ts_et.astimezone(timezone.utc)
    # AggregatedBar check in strategy uses isinstance — MagicMock passes as Bar.
    return b


@pytest.fixture()
def orb() -> ORBStrategy:
    cfg = OrbConfig(opening_range_minutes=15, entry_order_type="limit", eod_exit_time="15:50")
    return ORBStrategy(config=cfg, symbols=["SPY"], stop_loss_pct=Decimal("1"))


# ── Opening range accumulation ────────────────────────────────────────────────


def test_no_signal_during_opening_range(orb: ORBStrategy) -> None:
    assert orb.on_bar(_bar("SPY", 470, 472, 469, 471, 9, 35)) is None


def test_no_signal_on_multiple_range_bars(orb: ORBStrategy) -> None:
    for m in range(30, 45):
        assert orb.on_bar(_bar("SPY", 470, 475, 468, 471, 9, m)) is None


def test_range_high_low_built_correctly(orb: ORBStrategy) -> None:
    orb.on_bar(_bar("SPY", 470, 476, 469, 471, 9, 31))
    orb.on_bar(_bar("SPY", 471, 473, 467, 472, 9, 40))
    state = orb._state["SPY"]
    assert state.range_high == Decimal("476")
    assert state.range_low == Decimal("467")


# ── No signal outside market hours ───────────────────────────────────────────


def test_no_signal_pre_market(orb: ORBStrategy) -> None:
    assert orb.on_bar(_bar("SPY", 470, 472, 469, 471, 9, 0)) is None


def test_no_signal_post_market(orb: ORBStrategy) -> None:
    assert orb.on_bar(_bar("SPY", 470, 472, 469, 471, 16, 5)) is None


# ── Breakout signals ──────────────────────────────────────────────────────────


def _setup_range(orb: ORBStrategy) -> None:
    """Feed two range bars (high=476, low=467) then leave range."""
    orb.on_bar(_bar("SPY", 470, 476, 469, 471, 9, 31))
    orb.on_bar(_bar("SPY", 471, 473, 467, 472, 9, 40))


def test_long_signal_above_range_high(orb: ORBStrategy) -> None:
    _setup_range(orb)
    sig = orb.on_bar(_bar("SPY", 476, 480, 475, 477, 9, 50))
    assert sig is not None
    assert sig.direction == Direction.BUY
    assert sig.symbol == "SPY"
    assert sig.entry_price == Decimal("477")


def test_short_signal_below_range_low(orb: ORBStrategy) -> None:
    _setup_range(orb)
    sig = orb.on_bar(_bar("SPY", 467, 468, 464, 466, 9, 50))
    assert sig is not None
    assert sig.direction == Direction.SELL
    assert sig.symbol == "SPY"
    assert sig.entry_price == Decimal("466")


def test_no_signal_inside_range_post_range(orb: ORBStrategy) -> None:
    _setup_range(orb)
    sig = orb.on_bar(_bar("SPY", 471, 473, 470, 472, 9, 50))
    assert sig is None


# ── Stop price attached to signal ────────────────────────────────────────────


def test_long_signal_stop_price_is_below_entry(orb: ORBStrategy) -> None:
    _setup_range(orb)
    sig = orb.on_bar(_bar("SPY", 476, 480, 475, 477, 9, 50))
    assert sig is not None
    assert sig.stop_price < sig.entry_price


def test_short_signal_stop_price_is_above_entry(orb: ORBStrategy) -> None:
    _setup_range(orb)
    sig = orb.on_bar(_bar("SPY", 467, 468, 464, 466, 9, 50))
    assert sig is not None
    assert sig.stop_price > sig.entry_price


# ── No duplicate signals ──────────────────────────────────────────────────────


def test_no_duplicate_long_signal(orb: ORBStrategy) -> None:
    _setup_range(orb)
    sig1 = orb.on_bar(_bar("SPY", 476, 480, 475, 477, 9, 50))
    sig2 = orb.on_bar(_bar("SPY", 477, 482, 476, 480, 9, 51))
    assert sig1 is not None and sig1.direction == Direction.BUY
    assert sig2 is None


def test_no_duplicate_short_signal(orb: ORBStrategy) -> None:
    _setup_range(orb)
    sig1 = orb.on_bar(_bar("SPY", 467, 468, 464, 466, 9, 50))
    sig2 = orb.on_bar(_bar("SPY", 465, 466, 462, 463, 9, 51))
    assert sig1 is not None and sig1.direction == Direction.SELL
    assert sig2 is None


def test_both_long_and_short_can_fire_on_different_days(orb: ORBStrategy) -> None:
    _setup_range(orb)
    # Long fires
    orb.on_bar(_bar("SPY", 476, 480, 475, 477, 9, 50))
    # Reset and fire short on a new day
    orb.reset_day()
    orb.on_bar(_bar("SPY", 470, 476, 469, 471, 9, 31))
    orb.on_bar(_bar("SPY", 471, 473, 467, 472, 9, 40))
    sig = orb.on_bar(_bar("SPY", 467, 468, 464, 466, 9, 50))
    assert sig is not None and sig.direction == Direction.SELL


# ── EOD exit ──────────────────────────────────────────────────────────────────


def test_eod_flat_signal_after_long(orb: ORBStrategy) -> None:
    _setup_range(orb)
    orb.on_bar(_bar("SPY", 476, 480, 475, 477, 9, 50))  # trigger long
    sig = orb.on_bar(_bar("SPY", 478, 479, 477, 478, 15, 51))
    assert sig is not None
    assert sig.direction == Direction.FLAT


def test_eod_flat_signal_after_short(orb: ORBStrategy) -> None:
    _setup_range(orb)
    orb.on_bar(_bar("SPY", 467, 468, 464, 466, 9, 50))  # trigger short
    sig = orb.on_bar(_bar("SPY", 465, 466, 464, 465, 15, 51))
    assert sig is not None
    assert sig.direction == Direction.FLAT


def test_no_eod_flat_if_no_position(orb: ORBStrategy) -> None:
    _setup_range(orb)
    # No breakout fired
    sig = orb.on_bar(_bar("SPY", 472, 473, 471, 472, 15, 51))
    assert sig is None


def test_eod_flat_fires_only_once(orb: ORBStrategy) -> None:
    _setup_range(orb)
    orb.on_bar(_bar("SPY", 476, 480, 475, 477, 9, 50))
    sig1 = orb.on_bar(_bar("SPY", 478, 479, 477, 478, 15, 51))
    sig2 = orb.on_bar(_bar("SPY", 478, 479, 477, 478, 15, 52))
    assert sig1 is not None and sig1.direction == Direction.FLAT
    assert sig2 is None


# ── reset_day ────────────────────────────────────────────────────────────────


def test_reset_day_clears_all_state(orb: ORBStrategy) -> None:
    _setup_range(orb)
    orb.on_bar(_bar("SPY", 476, 480, 475, 477, 9, 50))
    orb.reset_day()
    state = orb._state["SPY"]
    assert state.range_complete is False
    assert state.long_triggered is False
    assert state.short_triggered is False
    assert state.flat_sent is False


# ── Unknown symbol ignored ────────────────────────────────────────────────────


def test_unknown_symbol_returns_none(orb: ORBStrategy) -> None:
    sig = orb.on_bar(_bar("TSLA", 200, 205, 198, 202, 10, 0))
    assert sig is None
