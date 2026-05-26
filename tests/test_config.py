from __future__ import annotations

import copy
import os
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from config_loader import OrbConfig, load_config

_VALID: dict[str, object] = {
    "live": False,
    "symbols": ["SPY", "AAPL"],
    "risk": {
        "max_position_usd": 5000,
        "stop_loss_pct": 1.0,
        "daily_loss_limit_usd": 500,
        "max_open_positions": 4,
    },
    "strategy": {
        "name": "orb",
        "orb": {
            "opening_range_minutes": 15,
            "entry_order_type": "limit",
            "eod_exit_time": "15:50",
        },
    },
}

_CREDS = {"ALPACA_API_KEY": "test_key", "ALPACA_SECRET_KEY": "test_secret"}


@pytest.fixture()
def cfg_file(tmp_path: Path) -> Path:
    f = tmp_path / "config.yaml"
    f.write_text(yaml.dump(_VALID), encoding="utf-8")
    return f


def test_valid_config_loads(cfg_file: Path) -> None:
    with patch.dict(os.environ, _CREDS):
        cfg = load_config(cfg_file)
    assert cfg.symbols == ["SPY", "AAPL"]
    assert cfg.live is False
    assert cfg.alpaca_api_key == "test_key"


def test_risk_fields_are_decimal(cfg_file: Path) -> None:
    with patch.dict(os.environ, _CREDS):
        cfg = load_config(cfg_file)
    assert isinstance(cfg.risk.max_position_usd, Decimal)
    assert isinstance(cfg.risk.stop_loss_pct, Decimal)
    assert isinstance(cfg.risk.daily_loss_limit_usd, Decimal)
    assert cfg.risk.max_position_usd == Decimal("5000")


def test_missing_api_key_raises(cfg_file: Path) -> None:
    with patch.dict(os.environ, {"ALPACA_SECRET_KEY": "s"}, clear=True):
        with pytest.raises(Exception, match="ALPACA_API_KEY"):
            load_config(cfg_file)


def test_missing_secret_key_raises(cfg_file: Path) -> None:
    with patch.dict(os.environ, {"ALPACA_API_KEY": "k"}, clear=True):
        with pytest.raises(Exception, match="ALPACA_SECRET_KEY"):
            load_config(cfg_file)


def test_empty_symbols_raises(tmp_path: Path) -> None:
    data = copy.deepcopy(_VALID)
    data["symbols"] = []  # type: ignore[index]
    f = tmp_path / "config.yaml"
    f.write_text(yaml.dump(data), encoding="utf-8")
    with patch.dict(os.environ, _CREDS):
        with pytest.raises(Exception):
            load_config(f)


def test_invalid_eod_time_raises(tmp_path: Path) -> None:
    data = copy.deepcopy(_VALID)
    data["strategy"]["orb"]["eod_exit_time"] = "25:00"  # type: ignore[index]
    f = tmp_path / "config.yaml"
    f.write_text(yaml.dump(data), encoding="utf-8")
    with patch.dict(os.environ, _CREDS):
        with pytest.raises(Exception):
            load_config(f)


def test_live_defaults_to_false(cfg_file: Path) -> None:
    with patch.dict(os.environ, _CREDS):
        cfg = load_config(cfg_file)
    assert cfg.live is False


def test_orb_config_defaults() -> None:
    orb = OrbConfig(opening_range_minutes=15)
    assert orb.entry_order_type == "limit"
    assert orb.eod_exit_time == "15:50"


def test_orb_config_range_minutes_bounds() -> None:
    with pytest.raises(Exception):
        OrbConfig(opening_range_minutes=0)
    with pytest.raises(Exception):
        OrbConfig(opening_range_minutes=61)
