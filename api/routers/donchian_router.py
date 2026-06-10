from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter()

PROJECT_ROOT = Path(__file__).parent.parent.parent
MEMORY_DIR = PROJECT_ROOT / "memory"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@router.get("/donchian")
async def get_donchian_state(profile: str | None = None) -> dict[str, Any]:
    """Tracked Donchian positions + overnight handoff queue for the dashboard.

    Pure file read of the bot's persisted state — works whether or not the bot
    is currently running (it's what a restarted bot would resume from).
    """
    suffix = f"_{profile}" if profile else ""
    state = _read_json(MEMORY_DIR / f"donchian_state{suffix}.json")
    handoff = _read_json(MEMORY_DIR / f"donchian_handoff{suffix}.json")

    positions = [
        {
            "symbol": sym,
            "direction": str(p.get("direction", "")),
            "entry_price": p.get("entry_price", 0.0),
            "entry_date": str(p.get("entry_date", "")),
            "stop_price": p.get("stop_price", 0.0),
            "channel_low": p.get("channel_low", 0.0),
            "channel_high": p.get("channel_high", 0.0),
            "trailing_active": bool(p.get("trailing_active", False)),
            "qty": p.get("qty", 0.0),
            "pending_exit": bool(p.get("pending_exit", False)),
        }
        for sym, p in (state.get("positions") or {}).items()
        if isinstance(p, dict)
    ]
    return {
        "positions": positions,
        "queued_entries": dict(handoff.get("queued_entries") or {}),
        "queued_exits": list(handoff.get("queued_exits") or []),
        "queued_date": str(handoff.get("queued_date") or ""),
        "pending_reanchor": list(handoff.get("pending_reanchor") or []),
        "ran_eod_date": str(handoff.get("ran_eod_date") or ""),
        "ran_open_date": str(handoff.get("ran_open_date") or ""),
    }
