from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config_loader import DonchianConfig, EmaConfig, OrbConfig, StrategyConfig  # noqa: E402

router = APIRouter()


class RiskPublic(BaseModel):
    """Risk config with Decimal fields serialised as plain floats for the UI."""
    model_config = ConfigDict(json_encoders={Decimal: float})

    max_position_usd: float
    stop_loss_pct: float
    daily_loss_limit_usd: float
    max_open_positions: int


class ConfigPublic(BaseModel):
    live: bool
    symbols: list[str]
    risk: RiskPublic
    strategy: StrategyConfig


@router.get("/config")
async def get_config() -> ConfigPublic:
    try:
        raw: dict[str, Any] = yaml.safe_load(
            (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")
        )
        return ConfigPublic(**raw)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.put("/config")
async def put_config(body: ConfigPublic) -> dict[str, str]:
    try:
        orb = body.strategy.orb
        ema = body.strategy.ema
        don = body.strategy.donchian
        data: dict[str, Any] = {
            "live": body.live,
            "symbols": body.symbols,
            "risk": {
                "max_position_usd":    float(body.risk.max_position_usd),
                "stop_loss_pct":       float(body.risk.stop_loss_pct),
                "daily_loss_limit_usd": float(body.risk.daily_loss_limit_usd),
                "max_open_positions":  body.risk.max_open_positions,
                "trailing_stop_pct":   10.0,
                "loser_cut_pct":       7.0,
            },
            "ai": {"enable_research": False, "enable_claude_filter": False},
            "strategy": {
                "name": body.strategy.name,
                "orb": {
                    "opening_range_minutes": orb.opening_range_minutes,
                    "entry_order_type":      orb.entry_order_type,
                    "eod_exit_time":         orb.eod_exit_time,
                },
                "ema": {
                    "fast_period":      ema.fast_period,
                    "slow_period":      ema.slow_period,
                    "entry_order_type": ema.entry_order_type,
                    "eod_exit_time":    ema.eod_exit_time,
                },
                "donchian": {
                    "lookback_days":            don.lookback_days,
                    "trend_ma":                 don.trend_ma,
                    "trailing_activation_pct":  don.trailing_activation_pct,
                    "trailing_pct":             don.trailing_pct,
                    "long_only":                don.long_only,
                },
            },
        }
        (PROJECT_ROOT / "config.yaml").write_text(
            yaml.dump(data, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )
        from api.deps import reset_client
        reset_client()
        return {"status": "saved"}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
