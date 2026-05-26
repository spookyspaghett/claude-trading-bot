from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

PROJECT_ROOT = Path(__file__).parent.parent.parent


@router.post("/kill")
async def trigger_kill_switch() -> dict[str, str]:
    """Create the KILL file. The running bot will detect it within one tick,
    flatten all positions, and exit."""
    kill_path = PROJECT_ROOT / "KILL"
    kill_path.touch()
    return {"status": "kill switch activated"}
