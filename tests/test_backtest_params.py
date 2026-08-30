"""Loading backtest settings from a profile or CSV, and judging the sample.

The point of these is that what you backtest and what you run are the same
thing. A mapping that quietly substitutes a default for a profile's real value
produces a result about a strategy the bot is not running.
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

from backtest_params import (
    DEFAULTS,
    free_parameters,
    implied_equity,
    params_from_config,
    params_from_csv,
    sample_verdict,
)
from config_loader import Config

_CREDS = {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}


@pytest.fixture(autouse=True)
def _keys() -> Any:
    with patch.dict(os.environ, _CREDS):
        yield


def _profile(strategy: str = "donchian", **over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "T", "asset_class": "stock", "live": False,
        "symbols": ["QQQ", "SPY"],
        "risk": {
            "max_position_usd": 25000, "stop_loss_pct": 2.0,
            "daily_loss_limit_usd": 4000, "max_open_positions": 8,
        },
        "strategy": {
            "name": strategy,
            "donchian": {
                "lookback_days": 55, "exit_lookback": 20, "trend_ma": 200,
                "long_only": True, "trailing_activation_pct": 15.0,
                "trailing_pct": 12.0,
            },
            "trend_sr": {
                "ma_fast": 34, "ma_slow": 89, "regime_ma": 150,
                "pivot_lookback": 25, "pivot_strength": 4,
                "atr_period": 20, "atr_mult": 2.5,
                "min_adx": 22.0, "adx_period": 14,
                "volume_mult": 1.3, "volume_ma": 30,
                "long_only": True,
                "trailing_activation_pct": 5.0, "trailing_pct": 12.0,
            },
            "ema": {"fast_period": 12, "slow_period": 26},
        },
        "ai": {}, "alpaca_api_key": "k", "alpaca_secret_key": "s",
    }
    data.update(over)
    return data


def _cfg(data: dict[str, Any]) -> Config:
    return Config(**{k: v for k, v in data.items() if k != "name"})


# ── Profile -> backtest form ──────────────────────────────────────────────────

def test_donchian_profile_maps_its_own_block() -> None:
    p = params_from_config(_cfg(_profile("donchian")))
    assert p["strategy"] == "auto"
    assert p["symbol"] == "QQQ"          # first symbol
    assert p["lookback_days"] == 55
    assert p["exit_lookback"] == 20
    assert p["trend_ma"] == 200
    assert p["trailing_pct"] == 12.0
    assert p["long_only"] is True


def test_donchian_atr_matches_what_the_live_runner_hardcodes() -> None:
    """donchian_strategy.py fixes the stop at ATR(14) x 1.5 and exposes no
    setting. Backtesting any other multiplier tests a strategy that can't run."""
    p = params_from_config(_cfg(_profile("donchian")))
    assert (p["atr_period"], p["atr_multiplier"]) == (14, 1.5)


def test_trend_sr_profile_maps_its_own_block() -> None:
    p = params_from_config(_cfg(_profile("trend_sr")))
    assert p["strategy"] == "trend_sr"
    assert (p["ma_fast"], p["ma_slow"]) == (34, 89)
    assert p["trend_ma"] == 150          # regime_ma is the backtest's trend_ma
    assert p["atr_multiplier"] == 2.5
    assert p["min_adx"] == 22.0
    assert p["volume_mult"] == 1.3
    assert p["pivot_strength"] == 4


def test_ema_profile_maps_its_periods() -> None:
    p = params_from_config(_cfg(_profile("ema")))
    assert p["strategy"] == "ema"
    assert (p["ma_fast"], p["ma_slow"]) == (12, 26)


def test_orb_seeds_the_channel_because_auto_can_dispatch_to_donchian() -> None:
    p = params_from_config(_cfg(_profile("orb")))
    assert p["strategy"] == "auto"
    assert p["lookback_days"] == 55


def test_loading_one_strategy_does_not_rewrite_anothers_knobs() -> None:
    """A Trend/SR profile must not drag Donchian's channel along with it."""
    p = params_from_config(_cfg(_profile("trend_sr")))
    assert p["lookback_days"] == DEFAULTS["lookback_days"]
    assert p["exit_lookback"] == DEFAULTS["exit_lookback"]


def test_risk_settings_come_across() -> None:
    p = params_from_config(_cfg(_profile("donchian")))
    assert p["max_position_usd"] == 25000.0
    assert p["stop_loss_pct"] == 2.0


# ── CSV -> backtest form ──────────────────────────────────────────────────────

def test_csv_settings_reach_the_backtest_form() -> None:
    csv = (
        "key,value\n"
        "strategy.name,trend_sr\n"
        "strategy.trend_sr.ma_fast,21\n"
        "strategy.trend_sr.min_adx,25\n"
        "strategy.trend_sr.atr_mult,3.0\n"
    )
    params, warnings = params_from_csv(csv, _profile("donchian"))
    assert params["strategy"] == "trend_sr"
    assert params["ma_fast"] == 21
    assert params["min_adx"] == 25.0
    assert params["atr_multiplier"] == 3.0
    assert any("trend_sr" in w for w in warnings)


def test_csv_is_held_to_the_same_validation_as_a_profile_import() -> None:
    """The backtest must not be pointed at settings the bot would refuse."""
    from settings_csv import CsvImportError
    with pytest.raises(CsvImportError, match="unknown setting"):
        params_from_csv("key,value\nstrategy.trend_sr.ma_fastt,21\n", _profile())


def test_csv_cannot_smuggle_live_mode_in_through_the_backtest() -> None:
    from settings_csv import CsvImportError
    with pytest.raises(CsvImportError, match="live cannot be set"):
        params_from_csv("key,value\nlive,true\n", _profile())


# ── Free parameters ───────────────────────────────────────────────────────────

def test_filters_switched_off_are_not_fitted_parameters() -> None:
    on = dict(DEFAULTS, strategy="trend_sr", min_adx=22.0, volume_mult=1.3)
    off = dict(DEFAULTS, strategy="trend_sr", min_adx=0.0, volume_mult=0.0)
    assert "min_adx" in free_parameters(on)
    assert "min_adx" not in free_parameters(off)
    assert len(free_parameters(on)) == len(free_parameters(off)) + 2


def test_knobs_the_strategy_never_reads_do_not_count() -> None:
    """Charging Trend/SR for Donchian's channel would overstate overfitting
    risk rather than measure it."""
    p = dict(DEFAULTS, strategy="trend_sr", lookback_days=90, exit_lookback=45)
    assert "lookback_days" not in free_parameters(p)


def test_atr_knobs_drop_out_when_the_atr_stop_is_off() -> None:
    p = dict(DEFAULTS, strategy="auto", use_atr_stop=False)
    assert "atr_period" not in free_parameters(p)
    assert "atr_multiplier" not in free_parameters(p)


def test_vwap_has_almost_nothing_to_fit() -> None:
    assert len(free_parameters(dict(DEFAULTS, strategy="vwap_revert"))) <= 1


# ── Sample verdict ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("trades", "params", "level"),
    [
        (12, 3, "bad"),      # under 30 trades: nothing to infer from
        (50, 6, "bad"),      # 8:1 — the ratio the literature calls overfitted
        (90, 3, "warn"),     # 30:1 but under 100 trades
        (200, 20, "bad"),    # plenty of trades, far too many knobs
        (300, 6, "ok"),      # 50:1 and a large sample
    ],
)
def test_sample_verdict_levels(trades: int, params: int, level: str) -> None:
    assert sample_verdict(trades, params)["level"] == level


def test_zero_parameters_does_not_divide_by_zero() -> None:
    v = sample_verdict(40, 0)
    assert v["ratio"] == 40.0
    assert v["level"] in ("ok", "warn", "bad")


# ── The equity a settings file implies ────────────────────────────────────────
# max_position_usd and daily_loss_limit_usd are absolute dollars, and the file
# carries no equity to read them against. The two daily limits pin it exactly.

def test_equity_is_recovered_from_the_two_daily_limits() -> None:
    assert implied_equity(2000, 2.0) == 100_000
    assert implied_equity(20_000, 4.0) == 500_000
    assert implied_equity(20, 4.0) == 500


@pytest.mark.parametrize(("usd", "pct"), [(0, 2.0), (2000, 0), (0, 0), (-1, 2.0)])
def test_equity_is_not_guessed_when_only_one_limit_is_set(
    usd: float, pct: float
) -> None:
    """With nothing to divide, the form's own value must stand."""
    assert implied_equity(usd, pct) is None


def test_starting_equity_comes_across_with_the_dollar_limits() -> None:
    """The bug this prevents: a $150 position cap loaded onto the form's default
    $500,000 book caps every trade at 0.03% of equity, so the run reports
    roughly nothing and reads as a bad strategy rather than a mis-scaled test."""
    p = params_from_config(_cfg(_profile("trend_sr", risk={
        "max_position_usd": 150, "stop_loss_pct": 2.0,
        "daily_loss_limit_usd": 20, "daily_loss_limit_pct": 4.0,
        "max_open_positions": 3, "risk_per_trade_pct": 1.0,
    })))
    assert p["starting_equity"] == 500
    assert p["max_position_usd"] == 150.0


def test_starting_equity_is_left_alone_when_it_cannot_be_derived() -> None:
    p = params_from_config(_cfg(_profile("donchian", risk={
        "max_position_usd": 25000, "stop_loss_pct": 2.0,
        "daily_loss_limit_usd": 4000, "max_open_positions": 8,
    })))
    assert p["starting_equity"] == DEFAULTS["starting_equity"]


def test_the_sizing_model_reaches_the_backtest() -> None:
    """risk_per_trade_pct decides every position size, and used to be read from
    the root config.yaml whatever profile the form was loaded from."""
    p = params_from_config(_cfg(_profile("donchian", risk={
        "max_position_usd": 60000, "stop_loss_pct": 2.0,
        "daily_loss_limit_usd": 20000, "daily_loss_limit_pct": 4.0,
        "max_open_positions": 8, "risk_per_trade_pct": 0.5,
    })))
    assert p["risk_per_trade_pct"] == 0.5
    assert p["daily_loss_limit_usd"] == 20000.0
    assert p["daily_loss_limit_pct"] == 4.0
    assert p["starting_equity"] == 500_000


def test_sizing_is_not_counted_as_a_fitted_parameter() -> None:
    """Position size changes returns, not entries and exits. Counting it would
    inflate the overfitting ratio with a knob that fits nothing."""
    p = dict(DEFAULTS, strategy="trend_sr", risk_per_trade_pct=0.5)
    assert "risk_per_trade_pct" not in free_parameters(p)
