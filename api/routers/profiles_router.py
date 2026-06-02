from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config_loader import Config, StrategyConfig  # noqa: E402
from profiles import (  # noqa: E402
    delete_profile,
    get_active_slug,
    list_profiles,
    load_profile,
    save_profile,
    set_active_slug,
    slugify,
)

router = APIRouter()


class RiskBody(BaseModel):
    max_position_usd: float
    stop_loss_pct: float
    daily_loss_limit_usd: float
    max_open_positions: int = Field(ge=1)
    trailing_stop_pct: float = 10.0
    loser_cut_pct: float = 7.0


class AiBody(BaseModel):
    enable_research: bool = False
    enable_claude_filter: bool = False


class ProfileBody(BaseModel):
    name: str = Field(min_length=1)
    asset_class: Literal["stock", "crypto"] = "stock"
    live: bool = False
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    symbols: list[str] = Field(min_length=1)
    risk: RiskBody
    strategy: StrategyConfig
    ai: AiBody = AiBody()


def _mask(secret: str) -> str:
    if not secret:
        return ""
    return f"••••{secret[-4:]}" if len(secret) > 4 else "••••"


def _validate_loadable(data: dict[str, Any]) -> None:
    """Ensure the profile would build a valid Config (raises on bad input)."""
    Config(**{k: v for k, v in data.items() if k != "name"})


@router.get("/profiles")
async def get_profiles() -> list[dict[str, Any]]:
    return list_profiles()


@router.get("/profiles/{slug}")
async def get_profile(slug: str) -> dict[str, Any]:
    try:
        data = load_profile(slug)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Mask secrets — the UI shows placeholders and only sends new keys on change.
    data = dict(data)
    data["alpaca_api_key"] = _mask(data.get("alpaca_api_key", ""))
    data["alpaca_secret_key"] = _mask(data.get("alpaca_secret_key", ""))
    data["slug"] = slug
    data["active"] = slug == get_active_slug()
    return data


def _build_payload(body: ProfileBody, existing: dict[str, Any] | None) -> dict[str, Any]:
    # Blank key fields mean "keep the existing secret" (UI sends masked placeholders).
    api_key = body.alpaca_api_key.strip()
    secret_key = body.alpaca_secret_key.strip()
    if (not api_key or api_key.startswith("••••")) and existing:
        api_key = existing.get("alpaca_api_key", "")
    if (not secret_key or secret_key.startswith("••••")) and existing:
        secret_key = existing.get("alpaca_secret_key", "")
    return {
        "name": body.name,
        "asset_class": body.asset_class,
        "live": body.live,
        "symbols": body.symbols,
        "risk": body.risk.model_dump(),
        "strategy": body.strategy.model_dump(),
        "ai": body.ai.model_dump(),
        "alpaca_api_key": api_key,
        "alpaca_secret_key": secret_key,
    }


@router.post("/profiles")
async def create_profile(body: ProfileBody) -> dict[str, Any]:
    try:
        slug = slugify(body.name)
        if (PROJECT_ROOT / "profiles" / f"{slug}.yaml").exists():
            raise ValueError(f"A profile named {body.name!r} already exists.")
        data = _build_payload(body, existing=None)
        _validate_loadable(data)
        save_profile(slug, data)
        return {"slug": slug, "status": "created"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/profiles/{slug}")
async def update_profile(slug: str, body: ProfileBody) -> dict[str, Any]:
    try:
        try:
            existing = load_profile(slug)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        data = _build_payload(body, existing=existing)
        _validate_loadable(data)
        save_profile(slug, data)
        from api.deps import reset_client
        reset_client()
        return {"slug": slug, "status": "saved"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/profiles/{slug}")
async def remove_profile(slug: str) -> dict[str, str]:
    from api import bot_manager
    if bot_manager.is_running() and slug == get_active_slug():
        raise HTTPException(status_code=409, detail="Stop the bot before deleting the active profile.")
    delete_profile(slug)
    return {"status": "deleted"}


@router.post("/profiles/{slug}/activate")
async def activate_profile(slug: str) -> dict[str, str]:
    from api import bot_manager
    if bot_manager.is_running():
        raise HTTPException(status_code=409, detail="Stop the bot before switching profiles.")
    try:
        set_active_slug(slug)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    from api.deps import reset_client
    reset_client()
    return {"status": "activated", "slug": slug}
