from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config_loader import Config, StrategyConfig  # noqa: E402
from profiles import (  # noqa: E402
    get_active_slug,
    load_active_config,
    load_profile,
    save_profile,
)

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
    asset_class: Literal["stock", "crypto"] = "stock"
    symbols: list[str]
    risk: RiskPublic
    strategy: StrategyConfig


@router.get("/config")
async def get_config(profile: str | None = None) -> ConfigPublic:
    """Return the editable settings of a profile (active when none given)."""
    try:
        if profile:
            data = load_profile(profile)
            cfg = Config(**{k: v for k, v in data.items() if k != "name"})
        else:
            cfg = load_active_config()
        return ConfigPublic(
            live=cfg.live,
            asset_class=cfg.asset_class,
            symbols=cfg.symbols,
            risk=RiskPublic(
                max_position_usd=float(cfg.risk.max_position_usd),
                stop_loss_pct=float(cfg.risk.stop_loss_pct),
                daily_loss_limit_usd=float(cfg.risk.daily_loss_limit_usd),
                max_open_positions=cfg.risk.max_open_positions,
            ),
            strategy=cfg.strategy,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.put("/config")
async def put_config(body: ConfigPublic, profile: str | None = None) -> dict[str, str]:
    """Merge edits into a profile (active when none given); keep keys, name and AI."""
    try:
        slug = profile or get_active_slug()
        if slug is None:
            raise ValueError("No profile to save into.")

        existing: dict[str, Any] = load_profile(slug)
        prev_risk = existing.get("risk", {}) or {}

        existing["live"] = body.live
        existing["asset_class"] = body.asset_class
        existing["symbols"] = body.symbols
        existing["risk"] = {
            "max_position_usd":     float(body.risk.max_position_usd),
            "stop_loss_pct":        float(body.risk.stop_loss_pct),
            "daily_loss_limit_usd": float(body.risk.daily_loss_limit_usd),
            "max_open_positions":   body.risk.max_open_positions,
            # Preserve advanced risk knobs not exposed in the basic editor.
            "trailing_stop_pct":    prev_risk.get("trailing_stop_pct", 10.0),
            "loser_cut_pct":        prev_risk.get("loser_cut_pct", 7.0),
        }
        existing["strategy"] = body.strategy.model_dump()

        save_profile(slug, existing)

        from api.deps import reset_client
        reset_client(slug)
        return {"status": "saved"}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
