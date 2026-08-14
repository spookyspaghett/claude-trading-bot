"""Risk analytics: R-multiples, drawdown geometry and the bootstrap."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backtest import (
    Trade,
    _compute_stats,
    _drawdown_profile,
    _monte_carlo,
    _r_multiples,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _trade(pnl: str, entry: str = "100", stop: str = "95", qty: str = "10",
           day: int = 0, held: int = 1) -> Trade:
    return Trade(
        symbol="TEST", direction="BUY",
        entry_time=T0 + timedelta(days=day),
        entry_price=Decimal(entry), stop_price=Decimal(stop),
        qty=Decimal(qty),
        exit_time=T0 + timedelta(days=day + held),
        exit_price=Decimal(entry), exit_reason="test",
        pnl=Decimal(pnl),
    )


def _curve(values: list[float], start_day: int = 0) -> list[dict[str, object]]:
    return [
        {"timestamp": int((T0 + timedelta(days=start_day + i)).timestamp()),
         "equity": v}
        for i, v in enumerate(values)
    ]


# ── R-multiples ───────────────────────────────────────────────────────────────

def test_r_multiple_is_pnl_over_risk_taken() -> None:
    # risk = |100 − 95| × 10 = 50; a $100 win is +2R, a $50 loss is −1R.
    assert _r_multiples([_trade("100")]) == [2.0]
    assert _r_multiples([_trade("-50")]) == [-1.0]


def test_r_is_independent_of_position_size() -> None:
    """The whole point of R: doubling size must not change the edge measure."""
    small = _r_multiples([_trade("100", qty="10")])
    big = _r_multiples([_trade("200", qty="20")])
    assert small == big == [2.0]


def test_trades_without_a_stop_are_excluded_not_zeroed() -> None:
    trades = [_trade("100"), _trade("100", stop="100"), _trade("50", stop="0")]
    rs = _r_multiples(trades)
    assert rs == [2.0]          # only the one with a real stop


# ── drawdown geometry ─────────────────────────────────────────────────────────

def test_drawdown_reports_dollars_percent_and_duration() -> None:
    # Peak 120 on day 1, trough 90 on day 3, back to 120 on day 5.
    dd, pct, days = _drawdown_profile(_curve([100, 120, 100, 90, 110, 120]))
    assert dd == Decimal("30")
    assert pct == pytest.approx(25.0)
    assert days == pytest.approx(3.0)


def test_unrecovered_drawdown_counts_to_the_end_of_the_run() -> None:
    _dd, _pct, days = _drawdown_profile(_curve([100, 150, 120, 120, 120]))
    assert days == pytest.approx(3.0)


def test_flat_curve_has_no_drawdown() -> None:
    dd, pct, days = _drawdown_profile(_curve([100, 100, 100]))
    assert (dd, pct, days) == (Decimal("0"), 0.0, 0.0)


# ── Monte Carlo ───────────────────────────────────────────────────────────────

def test_monte_carlo_is_deterministic() -> None:
    pnls = [100.0, -50.0, 200.0, -50.0, -50.0, 300.0]
    assert _monte_carlo(pnls, 10000.0) == _monte_carlo(pnls, 10000.0)


def test_p95_drawdown_is_at_least_the_median() -> None:
    med, p95, _ = _monte_carlo([100.0, -80.0, 150.0, -80.0, -80.0], 10000.0)
    assert p95 >= med > 0


def test_a_losing_strategy_almost_always_loses() -> None:
    _med, _p95, prob = _monte_carlo([-10.0] * 20, 10000.0)
    assert prob == 1.0


def test_a_strongly_winning_strategy_rarely_loses() -> None:
    _med, _p95, prob = _monte_carlo([100.0] * 20, 10000.0)
    assert prob == 0.0


def test_monte_carlo_handles_no_trades() -> None:
    assert _monte_carlo([], 10000.0) == (0.0, 0.0, 0.0)


# ── end-to-end stats ──────────────────────────────────────────────────────────

def test_stats_report_return_and_expectancy() -> None:
    trades = [_trade("100", day=0), _trade("-50", day=2), _trade("100", day=4)]
    stats = _compute_stats(trades, _curve([10000, 10100, 10050, 10150]),
                           Decimal("10000"))
    assert stats.total_trades == 3
    assert stats.total_pnl == Decimal("150")
    assert stats.total_return_pct == pytest.approx(1.5)
    assert stats.r_trades == 3
    assert stats.expectancy_r == pytest.approx((2.0 - 1.0 + 2.0) / 3)
    assert stats.avg_win_r == pytest.approx(2.0)
    assert stats.avg_loss_r == pytest.approx(1.0)


def test_stats_count_the_worst_losing_streak() -> None:
    trades = [_trade("100"), _trade("-50"), _trade("-50"), _trade("-50"),
              _trade("100"), _trade("-50")]
    stats = _compute_stats(trades, _curve([10000, 10050]), Decimal("10000"))
    assert stats.max_consecutive_losses == 3


def test_empty_backtest_produces_zeroed_stats() -> None:
    stats = _compute_stats([], [], Decimal("10000"))
    assert stats.total_trades == 0
    assert stats.expectancy_r == 0.0
    assert stats.mc_p95_max_dd_pct == 0.0


def test_sortino_ignores_upside_volatility() -> None:
    """A curve that only ever rises has no downside deviation, so Sortino must
    not be dragged down by the size of its up-days the way Sharpe is."""
    trades = [_trade("100")]
    steady = _compute_stats(trades, _curve([100, 101, 102, 103, 104]), Decimal("100"))
    spiky = _compute_stats(trades, _curve([100, 101, 130, 131, 180]), Decimal("100"))
    assert spiky.sharpe_ratio < steady.sharpe_ratio
    assert spiky.sortino_ratio == steady.sortino_ratio == 0.0  # no down days at all
