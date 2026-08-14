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
    """Risk config with Decimal fields serialised as plain floats for the UI.

    Defaults mirror ``RiskConfig`` so a profile written before these existed
    still deserialises, and so an older UI build that omits them from a PUT
    doesn't silently zero the sizing model.
    """
    model_config = ConfigDict(json_encoders={Decimal: float})

    max_position_usd: float
    stop_loss_pct: float
    daily_loss_limit_usd: float
    max_open_positions: int

    # Risk-based sizing
    risk_per_trade_pct: float = 0.0
    # Portfolio caps
    max_portfolio_heat_pct: float = 0.0
    max_group_heat_pct: float = 0.0
    correlation_groups: dict[str, list[str]] = {}
    max_gross_exposure_pct: float = 100.0
    # Loss limits & drawdown throttle
    daily_loss_limit_pct: float = 0.0
    derisk_start_dd_pct: float = 0.0
    halt_dd_pct: float = 0.0
    min_risk_scale: float = 0.25


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
                risk_per_trade_pct=float(cfg.risk.risk_per_trade_pct),
                max_portfolio_heat_pct=float(cfg.risk.max_portfolio_heat_pct),
                max_group_heat_pct=float(cfg.risk.max_group_heat_pct),
                correlation_groups=cfg.risk.correlation_groups,
                max_gross_exposure_pct=float(cfg.risk.max_gross_exposure_pct),
                daily_loss_limit_pct=float(cfg.risk.daily_loss_limit_pct),
                derisk_start_dd_pct=float(cfg.risk.derisk_start_dd_pct),
                halt_dd_pct=float(cfg.risk.halt_dd_pct),
                min_risk_scale=float(cfg.risk.min_risk_scale),
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
            # Carry every key the editor doesn't render — sizing, portfolio
            # heat, correlation groups, the drawdown throttle. Listing the
            # survivors by hand meant each new risk knob was one forgotten line
            # away from being wiped by an unrelated save.
            **prev_risk,
            "max_position_usd":     float(body.risk.max_position_usd),
            "stop_loss_pct":        float(body.risk.stop_loss_pct),
            "daily_loss_limit_usd": float(body.risk.daily_loss_limit_usd),
            "max_open_positions":   body.risk.max_open_positions,
            "trailing_stop_pct":    prev_risk.get("trailing_stop_pct", 10.0),
            "loser_cut_pct":        prev_risk.get("loser_cut_pct", 7.0),
            # Sizing model. These decide every position size, so they are
            # written explicitly rather than left to ride along in prev_risk.
            "risk_per_trade_pct":     float(body.risk.risk_per_trade_pct),
            "max_portfolio_heat_pct": float(body.risk.max_portfolio_heat_pct),
            "max_group_heat_pct":     float(body.risk.max_group_heat_pct),
            "correlation_groups":     body.risk.correlation_groups,
            "max_gross_exposure_pct": float(body.risk.max_gross_exposure_pct),
            "daily_loss_limit_pct":   float(body.risk.daily_loss_limit_pct),
            "derisk_start_dd_pct":    float(body.risk.derisk_start_dd_pct),
            "halt_dd_pct":            float(body.risk.halt_dd_pct),
            "min_risk_scale":         float(body.risk.min_risk_scale),
        }
        existing["strategy"] = body.strategy.model_dump()

        # Validate the merged profile before writing it. The drawdown ladder
        # carries a cross-field constraint, so an otherwise well-typed request
        # can still describe a config the bot will refuse to load — and finding
        # that out at the next start, from a profile that saved cleanly, is the
        # worst possible time.
        try:
            Config(**{k: v for k, v in existing.items() if k != "name"})
        except Exception as exc:
            raise ValueError(f"Rejected — {exc}") from exc

        save_profile(slug, existing)

        from api.deps import reset_client
        reset_client(slug)
        return {"status": "saved"}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
