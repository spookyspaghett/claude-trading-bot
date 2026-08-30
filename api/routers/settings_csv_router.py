"""Download a profile's settings as CSV, and import one back.

The import is a two-step by default: a POST with ``apply=false`` returns the
diff and writes nothing, so the dashboard can show which risk limits are about
to move before they move. Nothing here can set live mode or the Alpaca keys -
``settings_csv`` refuses those by name.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from profiles import load_profile, save_profile  # noqa: E402
from settings_csv import CsvImportError, apply_csv, export_csv  # noqa: E402

router = APIRouter()

#: A settings file is a few KB. Anything this size is a mis-picked file, and
#: reading it into memory to find that out is the wrong order of operations.
MAX_CSV_BYTES = 1_000_000


def _decode(raw: bytes) -> str:
    # utf-8-sig strips the byte-order mark Excel writes; cp1252 is what a
    # Windows spreadsheet falls back to when the file has smart quotes in it.
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


@router.get("/profiles/{slug}/settings.csv", response_class=PlainTextResponse)
async def download_settings(slug: str) -> PlainTextResponse:
    try:
        data = load_profile(slug)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        text = export_csv(slug, data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PlainTextResponse(
        text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{slug}-settings.csv"'},
    )


@router.post("/profiles/{slug}/settings/import")
async def import_settings(
    slug: str,
    file: UploadFile = File(...),
    apply: bool = Form(False),
) -> dict[str, Any]:
    """Preview (``apply=false``, the default) or write a CSV of settings."""
    try:
        existing = load_profile(slug)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    raw = await file.read()
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"{file.filename} is {len(raw) // 1024} KB; a settings CSV is a "
                   f"few KB. Check you picked the right file.",
        )

    try:
        result = apply_csv(existing, _decode(raw))
    except CsvImportError as exc:
        # The whole list, so a file with six problems takes one round trip to fix.
        raise HTTPException(
            status_code=422,
            detail={"errors": exc.errors, "applied": False},
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    payload = result.as_dict()
    payload["slug"] = slug
    payload["applied"] = False

    from api import bot_manager
    if bot_manager.is_running(slug):
        # Not a refusal: config_router already allows saving while a bot runs,
        # and the config is read at start. Saying so beats silently differing
        # from what the running process is actually using.
        result.warnings.append(
            "this profile's bot is running - it keeps the settings it started "
            "with until you restart it."
        )
        payload["warnings"] = result.warnings

    if apply and result.changes:
        save_profile(slug, result.profile)
        payload["applied"] = True
        from api.deps import reset_client
        reset_client(slug)

    return payload
