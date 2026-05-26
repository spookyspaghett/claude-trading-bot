from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config_loader import OrbConfig, RiskConfig, StrategyConfig  # noqa: E402

router = APIRouter()


class ConfigPublic(BaseModel):
    live: bool
    symbols: list[str]
    risk: RiskConfig
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
        # Rebuild a clean dict to write — exclude credentials (they live in .env).
        data: dict[str, Any] = {
            "live": body.live,
            "symbols": body.symbols,
            "risk": {
                "max_position_usd": float(body.risk.max_position_usd),
                "stop_loss_pct": float(body.risk.stop_loss_pct),
                "daily_loss_limit_usd": float(body.risk.daily_loss_limit_usd),
                "max_open_positions": body.risk.max_open_positions,
            },
            "strategy": {
                "name": body.strategy.name,
                "orb": {
                    "opening_range_minutes": body.strategy.orb.opening_range_minutes,
                    "entry_order_type": body.strategy.orb.entry_order_type,
                    "eod_exit_time": body.strategy.orb.eod_exit_time,
                },
            },
        }
        (PROJECT_ROOT / "config.yaml").write_text(
            yaml.dump(data, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )
        # Reset the cached trading client so it picks up any credential changes on next call.
        from api.deps import reset_client
        reset_client()
        return {"status": "saved"}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
