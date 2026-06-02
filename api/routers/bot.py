from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api import bot_manager

router = APIRouter()


@router.get("/bot/status")
async def bot_status(profile: str | None = None) -> dict[str, object]:
    """Without a profile: a map of {slug: {running, pid}} for every launched bot.
    With a profile: that single bot's status."""
    if profile:
        return {
            "running": bot_manager.is_running(profile),
            "pid": bot_manager.get_pid(profile),
        }
    return {"bots": bot_manager.status_map()}


@router.post("/bot/start")
async def bot_start(profile: str) -> dict[str, object]:
    return bot_manager.start(profile)


@router.post("/bot/stop")
async def bot_stop(profile: str) -> dict[str, object]:
    return bot_manager.stop(profile)


@router.post("/bot/restart")
async def bot_restart(profile: str) -> dict[str, object]:
    if bot_manager.is_running(profile):
        result = bot_manager.stop(profile)
        if not result.get("ok"):
            return result
    return bot_manager.start(profile)


@router.get("/bot/stderr")
async def bot_stderr(profile: str) -> dict[str, object]:
    """Return the last stdout/stderr output from the bot process."""
    if not profile:
        raise HTTPException(status_code=422, detail="profile is required")
    return {"log": bot_manager.get_stderr_log(profile)}
