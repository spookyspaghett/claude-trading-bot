from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from risk import RiskManager


@pytest.fixture()
def rm() -> RiskManager:
    return RiskManager(
        max_position_usd=Decimal("5000"),
        stop_loss_pct=Decimal("1"),
        daily_loss_limit_usd=Decimal("500"),
        max_open_positions=4,
    )


# ── compute_qty ───────────────────────────────────────────────────────────────


def test_compute_qty_exact(rm: RiskManager) -> None:
    assert rm.compute_qty(Decimal("100")) == Decimal("50")


def test_compute_qty_floors_fractional(rm: RiskManager) -> None:
    # 5000 / 333 = 15.015…  → floor = 15
    assert rm.compute_qty(Decimal("333")) == Decimal("15")


def test_compute_qty_zero_price(rm: RiskManager) -> None:
    assert rm.compute_qty(Decimal("0")) == Decimal("0")


def test_compute_qty_negative_price(rm: RiskManager) -> None:
    assert rm.compute_qty(Decimal("-1")) == Decimal("0")


# ── compute_stop_price ────────────────────────────────────────────────────────


def test_stop_price_long(rm: RiskManager) -> None:
    # 1 % below $100 = $99.00
    assert rm.compute_stop_price(Decimal("100"), "buy") == Decimal("99.00")


def test_stop_price_short(rm: RiskManager) -> None:
    # 1 % above $100 = $101.00
    assert rm.compute_stop_price(Decimal("100"), "sell") == Decimal("101.00")


def test_stop_price_rounds_to_cents(rm: RiskManager) -> None:
    stop = rm.compute_stop_price(Decimal("123.456"), "buy")
    assert stop == stop.quantize(Decimal("0.01"))


# ── check_new_order ───────────────────────────────────────────────────────────


def test_check_order_allowed_by_default(rm: RiskManager) -> None:
    ok, reason = rm.check_new_order("SPY")
    assert ok is True
    assert reason == ""


def test_max_positions_blocks_fifth_symbol(rm: RiskManager) -> None:
    for sym in ["A", "B", "C", "D"]:
        rm.record_fill(sym, Decimal("1"), Decimal("0"))
    ok, reason = rm.check_new_order("E")
    assert ok is False
    assert "max open positions" in reason


def test_existing_symbol_not_blocked_by_max_positions(rm: RiskManager) -> None:
    for sym in ["A", "B", "C", "D"]:
        rm.record_fill(sym, Decimal("1"), Decimal("0"))
    # Adding more to an already-open symbol is not blocked by position count
    ok, _ = rm.check_new_order("A")
    assert ok is True


def test_daily_limit_exact_hit(rm: RiskManager) -> None:
    rm.record_fill("SPY", Decimal("1"), Decimal("-500"))
    ok, reason = rm.check_new_order("SPY")
    assert ok is False
    assert "daily loss" in reason.lower()


def test_daily_limit_not_hit_one_cent_below(rm: RiskManager) -> None:
    rm.record_fill("SPY", Decimal("1"), Decimal("-499.99"))
    ok, _ = rm.check_new_order("AAPL")
    assert ok is True


# ── kill switch ───────────────────────────────────────────────────────────────


def test_kill_switch_no_file(tmp_path: Path) -> None:
    rm = RiskManager(
        max_position_usd=Decimal("5000"),
        stop_loss_pct=Decimal("1"),
        daily_loss_limit_usd=Decimal("500"),
        max_open_positions=4,
        kill_switch_path=tmp_path / "KILL",
    )
    assert rm.poll_kill_switch() is False
    ok, _ = rm.check_new_order("SPY")
    assert ok is True


def test_kill_switch_with_file(tmp_path: Path) -> None:
    kill = tmp_path / "KILL"
    kill.touch()
    rm = RiskManager(
        max_position_usd=Decimal("5000"),
        stop_loss_pct=Decimal("1"),
        daily_loss_limit_usd=Decimal("500"),
        max_open_positions=4,
        kill_switch_path=kill,
    )
    assert rm.poll_kill_switch() is True
    ok, reason = rm.check_new_order("SPY")
    assert ok is False
    assert "kill switch" in reason.lower()


def test_kill_switch_stays_triggered_after_file_deleted(tmp_path: Path) -> None:
    kill = tmp_path / "KILL"
    kill.touch()
    rm = RiskManager(
        max_position_usd=Decimal("5000"),
        stop_loss_pct=Decimal("1"),
        daily_loss_limit_usd=Decimal("500"),
        max_open_positions=4,
        kill_switch_path=kill,
    )
    rm.poll_kill_switch()
    kill.unlink()
    assert rm.poll_kill_switch() is True  # cached — stays triggered


# ── should_flatten_all ────────────────────────────────────────────────────────


def test_should_flatten_after_loss_limit(rm: RiskManager) -> None:
    rm.record_fill("SPY", Decimal("1"), Decimal("-600"))
    assert rm.should_flatten_all is True


def test_should_flatten_after_kill_switch(tmp_path: Path) -> None:
    kill = tmp_path / "KILL"
    kill.touch()
    rm = RiskManager(
        max_position_usd=Decimal("5000"),
        stop_loss_pct=Decimal("1"),
        daily_loss_limit_usd=Decimal("500"),
        max_open_positions=4,
        kill_switch_path=kill,
    )
    rm.poll_kill_switch()
    assert rm.should_flatten_all is True


def test_should_not_flatten_normally(rm: RiskManager) -> None:
    assert rm.should_flatten_all is False


# ── record_fill / position tracking ──────────────────────────────────────────


def test_record_fill_opens_position(rm: RiskManager) -> None:
    rm.record_fill("SPY", Decimal("10"), Decimal("0"))
    assert "SPY" in rm.open_symbols


def test_record_fill_closes_position(rm: RiskManager) -> None:
    rm.record_fill("SPY", Decimal("10"), Decimal("0"))
    rm.record_fill("SPY", Decimal("-10"), Decimal("50"))
    assert "SPY" not in rm.open_symbols


def test_daily_pnl_accumulates(rm: RiskManager) -> None:
    rm.record_fill("SPY", Decimal("10"), Decimal("-100"))
    rm.record_fill("AAPL", Decimal("5"), Decimal("200"))
    assert rm.daily_pnl == Decimal("100")


# ── reset_day ─────────────────────────────────────────────────────────────────


def test_reset_day_clears_pnl_and_positions(rm: RiskManager) -> None:
    rm.record_fill("SPY", Decimal("10"), Decimal("-400"))
    rm.reset_day()
    assert rm.daily_pnl == Decimal("0")
    assert rm.open_symbols == []
    ok, _ = rm.check_new_order("SPY")
    assert ok is True


def test_reset_day_does_not_clear_kill_switch(tmp_path: Path) -> None:
    kill = tmp_path / "KILL"
    kill.touch()
    rm = RiskManager(
        max_position_usd=Decimal("5000"),
        stop_loss_pct=Decimal("1"),
        daily_loss_limit_usd=Decimal("500"),
        max_open_positions=4,
        kill_switch_path=kill,
    )
    rm.poll_kill_switch()
    rm.reset_day()
    assert rm.should_flatten_all is True


# ── #9: risk-manager gaps ───────────────────────────────────────────────────────

def test_pending_entries_count_toward_open_limit() -> None:
    rm = RiskManager(max_position_usd=Decimal("5000"), stop_loss_pct=Decimal("1"),
                     daily_loss_limit_usd=Decimal("500"), max_open_positions=2)
    rm.register_pending("AAPL")
    rm.register_pending("MSFT")
    # Two pending (unfilled) entries already fill the limit of 2.
    ok, reason = rm.check_new_order("NVDA")
    assert not ok and "max open positions" in reason


def test_aggregate_exposure_capped_to_equity() -> None:
    rm = RiskManager(max_position_usd=Decimal("5000"), stop_loss_pct=Decimal("1"),
                     daily_loss_limit_usd=Decimal("500"), max_open_positions=10)
    rm.set_account_equity(Decimal("8000"))   # only room for one 5k position
    rm.register_pending("AAPL")
    ok, reason = rm.check_new_order("MSFT")   # 2 × 5k = 10k > 8k
    assert not ok and "exposure" in reason


def test_unrealized_drawdown_trips_daily_limit() -> None:
    rm = RiskManager(max_position_usd=Decimal("5000"), stop_loss_pct=Decimal("1"),
                     daily_loss_limit_usd=Decimal("500"), max_open_positions=4)
    assert rm.should_flatten_all is False
    rm.set_unrealized(Decimal("-600"))        # deep unrealized loss, no realized yet
    assert rm.should_flatten_all is True
    ok, _ = rm.check_new_order("AAPL")
    assert not ok
