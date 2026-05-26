from __future__ import annotations

from fastapi import APIRouter

from api import bot_manager

router = APIRouter()


@router.get("/bot/status")
async def bot_status() -> dict[str, object]:
    return {
        "running": bot_manager.is_running(),
        "pid": bot_manager.get_pid(),
    }


@router.post("/bot/start")
async def bot_start() -> dict[str, object]:
    return bot_manager.start()


@router.post("/bot/stop")
async def bot_stop() -> dict[str, object]:
    return bot_manager.stop()


@router.post("/bot/restart")
async def bot_restart() -> dict[str, object]:
    if bot_manager.is_running():
        result = bot_manager.stop()
        if not result.get("ok"):
            return result
    return bot_manager.start()


@router.get("/bot/stderr")
async def bot_stderr() -> dict[str, object]:
    """Return the last stdout/stderr output from the bot process."""
    return {"log": bot_manager.get_stderr_log()}
