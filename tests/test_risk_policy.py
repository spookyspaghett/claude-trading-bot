"""Risk-based sizing, portfolio heat, correlation groups and the DD throttle.

The legacy behaviours these sit alongside are covered in test_risk.py; every
test here sets an explicit RiskPolicy, because with no policy the manager is
required to behave exactly as it did before.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from risk import RiskManager, RiskPolicy

ENTRY = Decimal("100")


def _rm(equity: str | None = "100000", **policy: object) -> RiskManager:
    rm = RiskManager(
        max_position_usd=Decimal("50000"),
        stop_loss_pct=Decimal("1"),
        daily_loss_limit_usd=Decimal("100000"),   # out of the way unless tested
        max_open_positions=10,
        policy=RiskPolicy(**policy),  # type: ignore[arg-type]
    )
    if equity is not None:
        rm.set_account_equity(Decimal(equity))
    return rm


# ── sizing solves quantity from the stop ──────────────────────────────────────

def test_qty_is_solved_from_the_stop_distance() -> None:
    rm = _rm(risk_per_trade_pct=Decimal("1"))
    # 1% of 100_000 = 1_000 risked; stop 5 away ⇒ 200 shares.
    assert rm.compute_qty(ENTRY, stop_price=Decimal("95")) == Decimal("200")


def test_wider_stop_takes_a_smaller_position() -> None:
    rm = _rm(risk_per_trade_pct=Decimal("1"))
    tight = rm.compute_qty(ENTRY, stop_price=Decimal("95"))
    wide = rm.compute_qty(ENTRY, stop_price=Decimal("90"))
    assert wide < tight
    # ...and both put the same money at risk, which is the entire point.
    assert tight * Decimal("5") == wide * Decimal("10") == Decimal("1000")


def test_notional_cap_still_binds() -> None:
    """A very tight stop must not translate into an unbounded position."""
    rm = RiskManager(
        max_position_usd=Decimal("5000"), stop_loss_pct=Decimal("1"),
        daily_loss_limit_usd=Decimal("100000"), max_open_positions=10,
        policy=RiskPolicy(risk_per_trade_pct=Decimal("1")),
    )
    rm.set_account_equity(Decimal("100000"))
    # Risk alone would allow 10_000 shares; max_position_usd allows 50.
    assert rm.compute_qty(ENTRY, stop_price=Decimal("99.90")) == Decimal("50")


def test_missing_stop_falls_back_to_notional_sizing() -> None:
    rm = _rm(risk_per_trade_pct=Decimal("1"))
    assert rm.compute_qty(ENTRY) == Decimal("500")            # 50_000 / 100
    assert rm.compute_qty(ENTRY, stop_price=Decimal("0")) == Decimal("500")


def test_stop_at_entry_refuses_to_size() -> None:
    """Zero stop distance implies infinite size; refuse rather than max out."""
    rm = _rm(risk_per_trade_pct=Decimal("1"))
    assert rm.compute_qty(ENTRY, stop_price=ENTRY) == Decimal("0")


def test_unknown_equity_disables_risk_sizing() -> None:
    rm = _rm(equity=None, risk_per_trade_pct=Decimal("1"))
    assert rm.compute_qty(ENTRY, stop_price=Decimal("95")) == Decimal("500")


# ── portfolio heat ────────────────────────────────────────────────────────────

def test_portfolio_heat_caps_the_next_position() -> None:
    rm = _rm(risk_per_trade_pct=Decimal("1"),
             max_portfolio_heat_pct=Decimal("1.5"))
    first = rm.compute_qty(ENTRY, symbol="AAA", stop_price=Decimal("95"))
    rm.record_fill("AAA", first, Decimal("0"),
                   entry_price=ENTRY, stop_price=Decimal("95"))
    assert rm.open_risk_total == Decimal("1000")
    # Only 0.5% of equity of heat remains, so the next trade is half-sized.
    second = rm.compute_qty(ENTRY, symbol="BBB", stop_price=Decimal("95"))
    assert second == Decimal("100")


def test_heat_exhausted_blocks_new_symbols() -> None:
    rm = _rm(risk_per_trade_pct=Decimal("1"),
             max_portfolio_heat_pct=Decimal("1"))
    qty = rm.compute_qty(ENTRY, symbol="AAA", stop_price=Decimal("95"))
    rm.record_fill("AAA", qty, Decimal("0"),
                   entry_price=ENTRY, stop_price=Decimal("95"))
    allowed, reason = rm.check_new_order("BBB")
    assert not allowed
    assert "heat" in reason


def test_closing_a_position_releases_its_heat() -> None:
    rm = _rm(risk_per_trade_pct=Decimal("1"),
             max_portfolio_heat_pct=Decimal("1"))
    qty = rm.compute_qty(ENTRY, symbol="AAA", stop_price=Decimal("95"))
    rm.record_fill("AAA", qty, Decimal("0"),
                   entry_price=ENTRY, stop_price=Decimal("95"))
    rm.record_fill("AAA", -qty, Decimal("50"))
    assert rm.open_risk_total == Decimal("0")
    assert rm.check_new_order("BBB")[0]


# ── correlation groups ────────────────────────────────────────────────────────

def _grouped() -> RiskManager:
    return _rm(
        risk_per_trade_pct=Decimal("1"),
        max_portfolio_heat_pct=Decimal("10"),      # deliberately not binding
        max_group_heat_pct=Decimal("1.5"),
        correlation_groups={"AAPL": "tech", "MSFT": "tech"},
    )


def test_group_heat_caps_a_correlated_second_name() -> None:
    rm = _grouped()
    qty = rm.compute_qty(ENTRY, symbol="AAPL", stop_price=Decimal("95"))
    rm.record_fill("AAPL", qty, Decimal("0"),
                   entry_price=ENTRY, stop_price=Decimal("95"))
    # MSFT shares AAPL's group, so only the group's remaining 0.5% is available.
    got = rm.compute_qty(ENTRY, symbol="MSFT", stop_price=Decimal("95"))
    assert got == Decimal("100")


def test_ungrouped_symbol_is_unaffected_by_group_heat() -> None:
    rm = _grouped()
    qty = rm.compute_qty(ENTRY, symbol="AAPL", stop_price=Decimal("95"))
    rm.record_fill("AAPL", qty, Decimal("0"),
                   entry_price=ENTRY, stop_price=Decimal("95"))
    # GLD is in no group, so it is bounded only by the portfolio-wide cap.
    got = rm.compute_qty(ENTRY, symbol="GLD", stop_price=Decimal("95"))
    assert got == Decimal("200")


# ── drawdown throttle ─────────────────────────────────────────────────────────

def _throttled() -> RiskManager:
    return _rm(
        risk_per_trade_pct=Decimal("1"),
        derisk_start_dd_pct=Decimal("10"),
        halt_dd_pct=Decimal("20"),
        min_risk_scale=Decimal("0.5"),
    )


def test_full_size_at_the_high_water_mark() -> None:
    rm = _throttled()
    assert rm.risk_scale == Decimal("1")
    assert rm.compute_qty(ENTRY, stop_price=Decimal("95")) == Decimal("200")


def test_risk_shrinks_midway_through_a_drawdown() -> None:
    rm = _throttled()
    rm.set_account_equity(Decimal("85000"))    # 15% down: halfway to the halt
    assert rm.current_drawdown_pct == Decimal("15")
    assert rm.risk_scale == Decimal("0.75")
    # 1% of 85_000 × 0.75 = 637.50 risked, over a 5-wide stop.
    assert rm.compute_qty(ENTRY, stop_price=Decimal("95")) == Decimal("127")


def test_new_entries_halt_at_the_drawdown_limit() -> None:
    rm = _throttled()
    rm.set_account_equity(Decimal("79000"))    # 21% down
    allowed, reason = rm.check_new_order("AAA")
    assert not allowed
    assert "drawdown" in reason


def test_halt_does_not_force_liquidation() -> None:
    """Throttling stops new risk; it must not dump the book at the bottom."""
    rm = _throttled()
    rm.set_account_equity(Decimal("70000"))
    assert not rm.should_flatten_all


def test_high_water_mark_ratchets_up_only() -> None:
    rm = _throttled()
    rm.set_account_equity(Decimal("120000"))
    rm.set_account_equity(Decimal("108000"))
    assert rm.current_drawdown_pct == Decimal("10")


# ── loss limits & gross exposure ──────────────────────────────────────────────

def test_percentage_daily_limit_applies_when_tighter() -> None:
    rm = RiskManager(
        max_position_usd=Decimal("50000"), stop_loss_pct=Decimal("1"),
        daily_loss_limit_usd=Decimal("100000"), max_open_positions=10,
        policy=RiskPolicy(daily_loss_limit_pct=Decimal("1")),
    )
    rm.set_account_equity(Decimal("100000"))
    rm.record_fill("AAA", Decimal("1"), Decimal("-1000"))   # exactly 1%
    assert rm.should_flatten_all
    assert rm.check_new_order("BBB") == (False, "daily loss limit reached")


def test_percentage_daily_limit_never_loosens_the_absolute_one() -> None:
    rm = RiskManager(
        max_position_usd=Decimal("50000"), stop_loss_pct=Decimal("1"),
        daily_loss_limit_usd=Decimal("500"), max_open_positions=10,
        policy=RiskPolicy(daily_loss_limit_pct=Decimal("50")),
    )
    rm.set_account_equity(Decimal("100000"))
    rm.record_fill("AAA", Decimal("1"), Decimal("-500"))
    assert rm.should_flatten_all


def test_gross_exposure_cap_limits_size() -> None:
    rm = _rm(risk_per_trade_pct=Decimal("1"),
             max_gross_exposure_pct=Decimal("100"))
    rm.record_fill("AAA", Decimal("980"), Decimal("0"),
                   entry_price=ENTRY, stop_price=Decimal("95"))
    assert rm.open_notional_total == Decimal("98000")
    # Only $2_000 of buying power is left, so 20 shares at $100.
    got = rm.compute_qty(ENTRY, symbol="BBB", stop_price=Decimal("95"))
    assert got == Decimal("20")


def test_small_account_can_hold_more_than_one_position() -> None:
    """The lockout this replaces: any account below max_position_usd could only
    ever open a single position, because the pre-trade projection assumed every
    position would be the configured maximum."""
    rm = RiskManager(
        max_position_usd=Decimal("50000"), stop_loss_pct=Decimal("1"),
        daily_loss_limit_usd=Decimal("100000"), max_open_positions=5,
        policy=RiskPolicy(risk_per_trade_pct=Decimal("1")),
    )
    rm.set_account_equity(Decimal("2000"))
    for sym in ("AAA", "BBB", "CCC", "DDD"):
        allowed, reason = rm.check_new_order(sym)
        assert allowed, f"{sym} rejected: {reason}"
        qty = rm.compute_qty(ENTRY, symbol=sym, stop_price=Decimal("95"))
        assert qty > 0
        rm.record_fill(sym, qty, Decimal("0"),
                       entry_price=ENTRY, stop_price=Decimal("95"))
    assert len(rm.open_symbols) == 4


# ── reporting ─────────────────────────────────────────────────────────────────

def test_snapshot_reports_live_risk() -> None:
    rm = _rm(risk_per_trade_pct=Decimal("1"),
             max_portfolio_heat_pct=Decimal("5"))
    qty = rm.compute_qty(ENTRY, symbol="AAA", stop_price=Decimal("95"))
    rm.record_fill("AAA", qty, Decimal("0"),
                   entry_price=ENTRY, stop_price=Decimal("95"))
    snap = rm.snapshot()
    assert snap["open_risk_usd"] == pytest.approx(1000.0)
    assert snap["portfolio_heat_pct"] == pytest.approx(1.0)
    assert snap["open_notional_usd"] == pytest.approx(20000.0)
    assert snap["open_positions"] == 1
