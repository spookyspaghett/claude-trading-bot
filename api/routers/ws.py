from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

PROJECT_ROOT = Path(__file__).parent.parent.parent

# How far back to look for history when the feed first connects.
_BACKFILL_DAYS = 7
_BACKFILL_LINES = 300


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


def _backfill_lines(
    profile: str | None,
    days: int = _BACKFILL_DAYS,
    limit: int = _BACKFILL_LINES,
) -> list[str]:
    """Most recent log lines, walking back through previous days' files.

    A bot that has been up for days legitimately writes nothing "today" on a
    quiet session or overnight, so replaying only today's file left the feed
    stuck on "Waiting for bot activity…" against a perfectly healthy bot.
    Returned oldest → newest.
    """
    directory = _log_dir(profile)
    today = date.today()
    lines: list[str] = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        path = directory / f"{day.isoformat()}.jsonl"
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        day_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        # Walking backwards in time, so older days go in front of what we have.
        lines = day_lines + lines
        if len(lines) >= limit:
            break
    return lines[-limit:]


@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket, profile: str | None = None) -> None:
    await websocket.accept()
    log_file = _log_path(profile)

    # Seed the feed with recent history, then tail today's file from its end.
    for line in _backfill_lines(profile):
        await websocket.send_text(line)
    try:
        position = log_file.stat().st_size if log_file.exists() else 0
    except OSError:
        position = 0

    try:
        while True:
            await asyncio.sleep(0.4)
            current = _log_path(profile)  # re-resolve in case day rolled over
            if current != log_file:
                # Day rollover: start tailing the new file from the top —
                # carrying yesterday's byte offset over would skip everything
                # until the new file outgrew the old one (feed froze daily).
                log_file = current
                position = 0
            try:
                if not log_file.exists():
                    continue
                size = log_file.stat().st_size
                if size < position:
                    position = 0  # file was truncated/replaced — re-read
                if size == position:
                    continue
                with log_file.open(encoding="utf-8") as fh:
                    fh.seek(position)
                    new_text = fh.read()
                    position = fh.tell()
            except OSError:
                continue  # transient FS race (file rotating) — retry next tick
            for line in new_text.splitlines():
                line = line.strip()
                if line:
                    await websocket.send_text(line)
    except WebSocketDisconnect:
        pass
