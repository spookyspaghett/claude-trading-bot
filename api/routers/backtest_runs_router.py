"""Saved backtest runs, and loading a run's settings from a profile or CSV.

Every backtest has always been written to memory/backtest_reports/ and then
never shown anywhere — the dashboard rendered the result once and forgot it.
These endpoints surface that history, so two runs can be put side by side
instead of compared from memory.

Reports written before this existed have no `params` block. They are listed
rather than hidden: a run you can see but can't fully explain is still more
useful than one you can't see.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

PROJECT_ROOT = Path(__file__).parent.parent.parent
REPORTS_DIR = PROJECT_ROOT / "memory" / "backtest_reports"
sys.path.insert(0, str(PROJECT_ROOT))

from backtest_params import (  # noqa: E402
    DEFAULTS,
    free_parameters,
    params_from_csv,
    params_from_profile,
    sample_verdict,
)

router = APIRouter()

MAX_CSV_BYTES = 1_000_000


def _safe_report_path(run_id: str) -> Path:
    """Resolve a run id to a file inside REPORTS_DIR, or refuse.

    The id reaches us from a URL, so it is checked for traversal the same way
    the existing report download does.
    """
    if "/" in run_id or "\\" in run_id or ".." in run_id:
        raise HTTPException(status_code=400, detail="Invalid run id.")
    path = REPORTS_DIR / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found.")
    return path


def _summarise(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None      # a half-written or hand-edited file shouldn't hide the rest
    stats = data.get("stats") or {}
    params = data.get("params") or {}
    trades = int(stats.get("total_trades") or 0)
    n_free = len(free_parameters(params)) if params else 0
    return {
        "id": path.stem,
        "created_at": data.get("created_at") or "",
        "symbol": data.get("symbol", "?"),
        "strategy_used": data.get("strategy_used", "?"),
        "start_date": data.get("start_date", ""),
        "end_date": data.get("end_date", ""),
        "has_params": bool(params),
        "params": params,
        "sample": sample_verdict(trades, n_free) if params else None,
        "stats": {
            k: stats.get(k)
            for k in (
                "total_trades", "win_rate", "profit_factor", "total_pnl",
                "total_return_pct", "max_drawdown_pct", "sharpe_ratio",
                "sortino_ratio", "calmar_ratio", "expectancy_r",
                "max_consecutive_losses", "exposure_pct",
                "mc_p95_max_dd_pct",
            )
        },
    }


@router.get("/backtest/runs")
async def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    """Saved runs, newest first. Filenames are timestamped, so name order is
    time order and nothing has to be parsed to sort them."""
    if not REPORTS_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    capped = max(1, min(limit, 500))
    for path in sorted(REPORTS_DIR.glob("*.json"), reverse=True)[:capped]:
        row = _summarise(path)
        if row is not None:
            out.append(row)
    return out


@router.get("/backtest/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    path = _safe_report_path(run_id)
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail=f"Unreadable report: {exc}"
        ) from exc
    data["id"] = run_id
    return data


@router.delete("/backtest/runs/{run_id}")
async def delete_run(run_id: str) -> dict[str, str]:
    _safe_report_path(run_id).unlink()
    return {"status": "deleted", "id": run_id}


# ── Loading settings into the backtest form ───────────────────────────────────

@router.get("/backtest/params/defaults")
async def get_defaults() -> dict[str, Any]:
    return dict(DEFAULTS)


@router.get("/backtest/params/from-profile/{slug}")
async def params_from_profile_endpoint(slug: str) -> dict[str, Any]:
    """Backtest the settings a profile is actually running."""
    try:
        params = params_from_profile(slug)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"params": params, "warnings": [], "source": f"profile:{slug}"}


@router.post("/backtest/params/from-csv")
async def params_from_csv_endpoint(
    file: UploadFile = File(...),
    profile: str = Form(""),
) -> dict[str, Any]:
    """Backtest the settings in a CSV.

    The file is merged onto a real profile before being mapped, so it goes
    through exactly the same validation as a profile import — the backtest
    cannot be pointed at a configuration the bot would refuse to load.
    """
    from profiles import get_active_slug, load_profile
    from settings_csv import CsvImportError

    slug = profile.strip() or get_active_slug()
    if not slug:
        raise HTTPException(
            status_code=422,
            detail="No profile to merge these settings onto. Create one first.",
        )
    try:
        base = load_profile(slug)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    raw = await file.read()
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"{file.filename} is {len(raw) // 1024} KB; a settings CSV is a "
                   f"few KB. This looks like a bars file — those go in the data "
                   f"upload above, not here.",
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")

    try:
        params, warnings = params_from_csv(text, base)
    except CsvImportError as exc:
        raise HTTPException(
            status_code=422, detail={"errors": exc.errors}
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "params": params,
        "warnings": warnings,
        "source": f"csv:{file.filename or 'settings.csv'}",
    }
