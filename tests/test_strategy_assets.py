"""A strategy may only be paired with an asset class it can actually trade.

The failure this prevents is silent rather than loud: ORB pointed at a 24/7
crypto feed doesn't raise, it just stops trading outside US market hours and
flattens every afternoon.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from config_loader import STRATEGY_ASSETS, load_config, strategies_for


def _cfg(asset_class: str, strategy: str) -> dict[str, Any]:
    return {
        "live": False,
        "asset_class": asset_class,
        "symbols": ["BTC/USD"] if asset_class == "crypto" else ["SPY"],
        "risk": {
            "max_position_usd": 1000,
            "stop_loss_pct": 1.0,
            "daily_loss_limit_usd": 100,
            "max_open_positions": 2,
        },
        "strategy": {"name": strategy},
    }


def _write(tmp_path: Path, data: dict[str, Any]) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _keys() -> Any:
    """Config requires Alpaca credentials; every test here is about strategy
    pairing, not auth."""
    with patch.dict(os.environ, {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}):
        yield


# ── the mapping itself ────────────────────────────────────────────────────────

def test_orb_is_stock_only() -> None:
    """ORB is defined by the 09:30 ET opening range; it has no 24/7 mode."""
    assert STRATEGY_ASSETS["orb"] == frozenset({"stock"})
    assert "orb" not in strategies_for("crypto")
    assert "orb" in strategies_for("stock")


def test_every_other_strategy_trades_both() -> None:
    for name in ("ema", "donchian", "trend_sr", "vwap_revert"):
        assert STRATEGY_ASSETS[name] == frozenset({"stock", "crypto"}), name


def test_both_asset_classes_have_something_to_trade() -> None:
    assert strategies_for("stock")
    assert strategies_for("crypto")


# ── config validation ─────────────────────────────────────────────────────────

def test_crypto_rejects_orb(tmp_path: Path) -> None:
    path = _write(tmp_path, _cfg("crypto", "orb"))
    with pytest.raises(Exception, match="orb"):
        load_config(path)


def test_rejection_names_the_usable_strategies(tmp_path: Path) -> None:
    """The error has to say what to do, not just what's wrong."""
    path = _write(tmp_path, _cfg("crypto", "orb"))
    with pytest.raises(Exception, match="supports") as excinfo:
        load_config(path)
    message = str(excinfo.value)
    assert "trend_sr" in message
    assert "crypto" in message


@pytest.mark.parametrize("strategy", ["ema", "donchian", "trend_sr", "vwap_revert"])
def test_crypto_accepts_the_session_agnostic_strategies(
    tmp_path: Path, strategy: str,
) -> None:
    cfg = load_config(_write(tmp_path, _cfg("crypto", strategy)))
    assert cfg.strategy.name == strategy


@pytest.mark.parametrize(
    "strategy", ["orb", "ema", "donchian", "trend_sr", "vwap_revert"])
def test_stock_accepts_every_strategy(
    tmp_path: Path, strategy: str,
) -> None:
    cfg = load_config(_write(tmp_path, _cfg("stock", strategy)))
    assert cfg.strategy.name == strategy


# ── live dispatch ─────────────────────────────────────────────────────────────

def test_dispatch_refuses_orb_for_crypto(tmp_path: Path) -> None:
    """Defence in depth: even handed a config that skipped validation, the
    strategy builder must not hand a crypto bot a market-hours strategy."""
    from main import _build_strategy

    cfg = load_config(_write(tmp_path, _cfg("crypto", "trend_sr")))
    # Bypass validation the way a hand-edited profile would.
    object.__setattr__(cfg.strategy, "name", "orb")
    with pytest.raises(ValueError, match="ORB cannot trade crypto"):
        _build_strategy(cfg)


def test_dispatch_rejects_an_unknown_strategy(tmp_path: Path) -> None:
    """This used to fall through to ORB, quietly swapping the configured
    strategy for a market-hours one with nothing in the logs."""
    from main import _build_strategy

    cfg = load_config(_write(tmp_path, _cfg("crypto", "trend_sr")))
    object.__setattr__(cfg.strategy, "name", "nonsense")
    with pytest.raises(ValueError, match="Unknown strategy"):
        _build_strategy(cfg)


def test_dispatch_builds_orb_for_stock(tmp_path: Path) -> None:
    from main import _build_strategy

    cfg = load_config(_write(tmp_path, _cfg("stock", "orb")))
    strat, order_type = _build_strategy(cfg)
    assert type(strat).__name__ == "ORBStrategy"
    assert order_type == "limit"


@pytest.mark.parametrize(
    ("strategy", "expected"),
    [("trend_sr", "TrendSRStrategy"), ("ema", "EMAStrategy"),
     ("vwap_revert", "VWAPRevertStrategy")],
)
def test_crypto_strategies_are_built_for_24_7(
    tmp_path: Path, strategy: str, expected: str,
) -> None:
    """Each crypto-capable strategy must actually receive trade_24_7, or it
    would anchor its session to the ET trading day."""
    from main import _build_strategy

    cfg = load_config(_write(tmp_path, _cfg("crypto", strategy)))
    strat, _ = _build_strategy(cfg)
    assert type(strat).__name__ == expected
    assert getattr(strat, "_trade_24_7", None) is True
