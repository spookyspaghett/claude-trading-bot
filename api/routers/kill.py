from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from api import bot_manager

router = APIRouter()

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _kill_path(slug: str) -> Path:
    return PROJECT_ROOT / "logs" / slug / "KILL"


@router.post("/kill")
async def trigger_kill_switch(profile: str) -> dict[str, str]:
    """Create the profile's KILL file. That bot detects it within one tick,
    flattens its positions, and exits — other accounts are unaffected."""
    if not profile:
        raise HTTPException(status_code=422, detail="profile is required")
    path = _kill_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return {"status": f"kill switch activated for {profile}"}


@router.post("/kill-all")
async def trigger_kill_all() -> dict[str, object]:
    """Master kill: trip the KILL switch for every running bot."""
    slugs = bot_manager.running_slugs()
    for slug in slugs:
        path = _kill_path(slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    return {"status": "kill-all activated", "killed": slugs}
