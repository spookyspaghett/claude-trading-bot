from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _today_log() -> Path:
    return PROJECT_ROOT / "logs" / f"{date.today().isoformat()}.jsonl"


@router.get("/orders")
async def get_today_events() -> list[dict[str, object]]:
    """Return all structured log events from today's JSONL file."""
    log_file = _today_log()
    if not log_file.exists():
        return []
    events: list[dict[str, object]] = []
    for line in log_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events
