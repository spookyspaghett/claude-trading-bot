from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api import bot_manager
from api.routers import account, bot, config_router, kill, logs, positions
from api.routers.backtest_router import router as backtest_router
from api.routers.bars_router import router as bars_router
from api.routers.donchian_router import router as donchian_router
from api.routers.profiles_router import router as profiles_router
from api.routers.ws import router as ws_router

app = FastAPI(title="Claude Trading Dashboard", version="1.0.0")


@app.on_event("startup")
async def _relaunch_bots() -> None:
    """Relaunch any bots that were running before this API process restarted
    (their subprocesses die with the parent on a systemd restart)."""
    bot_manager.relaunch_persisted()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:4173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routes ────────────────────────────────────────────────────────────────
app.include_router(positions.router, prefix="/api")
app.include_router(account.router, prefix="/api")
app.include_router(logs.router, prefix="/api")
app.include_router(bot.router, prefix="/api")
app.include_router(config_router.router, prefix="/api")
app.include_router(profiles_router, prefix="/api")
app.include_router(kill.router, prefix="/api")
app.include_router(ws_router, prefix="/api")
app.include_router(backtest_router, prefix="/api")
app.include_router(bars_router, prefix="/api")
app.include_router(donchian_router, prefix="/api")

# ── Serve built React UI (production) ────────────────────────────────────────
_dist = Path(__file__).parent.parent / "ui" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="ui")
