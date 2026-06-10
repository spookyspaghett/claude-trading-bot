from __future__ import annotations

from types import SimpleNamespace

import pytest

import donchian_strategy as ds
from donchian_strategy import DonchianLiveStrategy


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(ds, "STATE_PATH", tmp_path / "donchian_state.json")


def _bar(o: float, h: float, low: float, c: float) -> SimpleNamespace:
    return SimpleNamespace(open=o, high=h, low=low, close=c)


def _rising(n: int = 11) -> list[SimpleNamespace]:
    # Gentle uptrend; each bar ~+1.
    bars = []
    for i in range(n):
        base = 100 + i
        bars.append(_bar(base, base + 0.5, base - 1, base))
    return bars


def _strat() -> DonchianLiveStrategy:
    return DonchianLiveStrategy(lookback_days=3, trend_ma=0, long_only=True)


def test_entry_creates_pending_unfilled_position() -> None:
    s = _strat()
    bars = _rising(11) + [_bar(120, 121, 119, 120)]  # breakout above prior 3-day high
    res = s.scan("AAPL", bars)
    assert res.action == "enter_long"
    pos = s.open_positions["AAPL"]
    assert pos.qty == 0.0                       # not filled yet
    assert pos.stop_price < pos.entry_price
    assert s.positions_pending_entry() == ["AAPL"]
    assert s.positions_pending_exit() == []


def test_reanchor_preserves_stop_distance_to_fill() -> None:
    s = _strat()
    bars = _rising(11) + [_bar(120, 121, 119, 120)]
    s.scan("AAPL", bars)
    pos = s.open_positions["AAPL"]
    dist = pos.entry_price - pos.stop_price     # set off the scan close (120)
    s.record_fill("AAPL", 10.0)
    s.reanchor("AAPL", 118.0)                   # actual fill gapped down to 118
    pos = s.open_positions["AAPL"]
    assert pos.entry_price == 118.0
    assert pos.peak_price == 118.0
    assert abs((pos.entry_price - pos.stop_price) - dist) < 1e-6   # distance preserved


def test_channel_exit_flags_not_deletes_and_rederives() -> None:
    s = _strat()
    bars = _rising(11) + [_bar(120, 121, 119, 120)]
    s.scan("AAPL", bars)
    s.record_fill("AAPL", 10.0)

    # A crash below the channel low → exit signalled.
    crash = bars + [_bar(95, 96, 88, 90)]
    res = s.scan("AAPL", crash)
    assert res.action == "exit"
    assert "AAPL" in s.open_positions           # NOT deleted (survives restart)
    assert s.open_positions["AAPL"].pending_exit is True
    assert s.positions_pending_exit() == ["AAPL"]

    # A fresh strategy (simulated restart) reloads state and still sees the exit.
    s2 = _strat()
    assert s2.positions_pending_exit() == ["AAPL"]

    # Re-scanning keeps re-queueing the exit until the close fills.
    assert s2.scan("AAPL", crash).action == "exit"

    # Only an actual fill removes it.
    s2.remove_position("AAPL")
    assert "AAPL" not in s2.open_positions


def test_exit_lookback_exits_on_shorter_channel() -> None:
    # Turtle-style: enter on the 3-day channel, exit on the 2-day channel.
    s = DonchianLiveStrategy(lookback_days=3, exit_lookback=2, trend_ma=0, long_only=True)
    bars = _rising(11) + [_bar(120, 121, 119, 120)]
    assert s.scan("AAPL", bars).action == "enter_long"
    s.record_fill("AAPL", 10.0)

    # Two strong up days, then a pullback below the 2-day low (129) but well
    # above the 3-day low (119) and above the ATR stop.
    pullback = bars + [_bar(130, 130.5, 129, 130), _bar(140, 140.5, 139, 140),
                       _bar(126, 126.5, 124.5, 125)]
    assert s.scan("AAPL", pullback).action == "exit"


def test_exit_lookback_zero_keeps_entry_channel_exit() -> None:
    # Same pullback with exit_lookback disabled: 125 > 3-day low (119) → hold.
    s = DonchianLiveStrategy(lookback_days=3, trend_ma=0, long_only=True)
    bars = _rising(11) + [_bar(120, 121, 119, 120)]
    assert s.scan("AAPL", bars).action == "enter_long"
    s.record_fill("AAPL", 10.0)

    pullback = bars + [_bar(130, 130.5, 129, 130), _bar(140, 140.5, 139, 140),
                       _bar(126, 126.5, 124.5, 125)]
    assert s.scan("AAPL", pullback).action == "hold"
