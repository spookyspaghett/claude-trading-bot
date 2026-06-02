from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _log_dir(profile: str | None) -> Path:
    """Per-profile log directory; falls back to the active profile, then legacy."""
    if profile:
        return PROJECT_ROOT / "logs" / profile
    try:
        import sys
        sys.path.insert(0, str(PROJECT_ROOT))
        from profiles import get_active_slug
        slug = get_active_slug()
        if slug:
            return PROJECT_ROOT / "logs" / slug
    except Exception:
        pass
    return PROJECT_ROOT / "logs"


def _log_path(profile: str | None) -> Path:
    return _log_dir(profile) / f"{date.today().isoformat()}.jsonl"


@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket, profile: str | None = None) -> None:
    await websocket.accept()
    log_file = _log_path(profile)

    # Send existing log lines on connect.
    if log_file.exists():
        for line in log_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                await websocket.send_text(line)

    position = log_file.stat().st_size if log_file.exists() else 0

    try:
        while True:
            await asyncio.sleep(0.4)
            log_file = _log_path(profile)  # re-resolve in case day rolled over
            if not log_file.exists():
                continue
            size = log_file.stat().st_size
            if size <= position:
                continue
            with log_file.open(encoding="utf-8") as fh:
                fh.seek(position)
                new_text = fh.read()
                position = fh.tell()
            for line in new_text.splitlines():
                line = line.strip()
                if line:
                    await websocket.send_text(line)
    except WebSocketDisconnect:
        pass
