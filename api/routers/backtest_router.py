from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).parent.parent.parent
REPORTS_DIR  = PROJECT_ROOT / "memory" / "backtest_reports"
sys.path.insert(0, str(PROJECT_ROOT))

router = APIRouter()


# ── Report persistence ────────────────────────────────────────────────────────

def _save_report(payload: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts  = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    sym = payload.get("symbol", "UNKNOWN")
    path = REPORTS_DIR / f"{ts}_{sym}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


# ── Shared response serialiser ────────────────────────────────────────────────

def _result_to_dict(result: Any) -> dict[str, Any]:
    return {
        "symbol":        result.symbol,
        "start_date":    result.start_date,
        "end_date":      result.end_date,
        "strategy_used": result.strategy_used,
        "equity_curve":  result.equity_curve,
        "stats": {
            "total_trades":   result.stats.total_trades,
            "winning_trades": result.stats.winning_trades,
            "losing_trades":  result.stats.losing_trades,
            "win_rate":       round(result.stats.win_rate * 100, 1),
            "avg_win":        str(result.stats.avg_win.quantize(result.stats.avg_win.__class__("0.01"))),
            "avg_loss":       str(result.stats.avg_loss.quantize(result.stats.avg_loss.__class__("0.01"))),
            "profit_factor":  round(result.stats.profit_factor, 2),
            "total_pnl":      str(result.stats.total_pnl.quantize(result.stats.total_pnl.__class__("0.01"))),
            "max_drawdown":   str(result.stats.max_drawdown.quantize(result.stats.max_drawdown.__class__("0.01"))),
            "sharpe_ratio":   round(result.stats.sharpe_ratio, 2),
        },
        "trades": [
            {
                "symbol":      t.symbol,
                "direction":   t.direction,
                "entry_time":  t.entry_time.isoformat(),
                "entry_price": str(t.entry_price),
                "exit_time":   t.exit_time.isoformat() if t.exit_time else None,
                "exit_price":  str(t.exit_price)       if t.exit_price else None,
                "exit_reason": t.exit_reason,
                "qty":         str(t.qty),
                "pnl":         str(t.pnl.quantize(t.pnl.__class__("0.01"))),
            }
            for t in result.trades
        ],
    }


# ── Alpaca endpoint (existing) ────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    symbol: str
    start_date: str   # YYYY-MM-DD
    end_date: str     # YYYY-MM-DD


@router.post("/backtest")
async def run_backtest_endpoint(req: BacktestRequest) -> dict[str, Any]:
    try:
        from backtest import run_backtest
        from config_loader import load_config

        cfg = load_config(PROJECT_ROOT / "config.yaml")
        start = date.fromisoformat(req.start_date)
        end   = date.fromisoformat(req.end_date)

        if (end - start).days > 180:
            raise ValueError("Date range must be 180 days or less to avoid timeouts.")

        result = await run_backtest(
            symbol=req.symbol,
            start=start,
            end=end,
            api_key=cfg.alpaca_api_key,
            secret_key=cfg.alpaca_secret_key,
            orb_config=cfg.strategy.orb,
            risk_config=cfg.risk,
        )

        payload = _result_to_dict(result)
        report_path = _save_report(payload)
        payload["report_file"] = report_path.name
        return payload

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── File-upload endpoint (new) ────────────────────────────────────────────────

@router.post("/backtest/upload")
async def run_backtest_upload(
    file: UploadFile = File(...),
    symbol: str = Form(...),
    lookback_days: int = Form(40),
    long_only: bool = Form(False),
    trend_ma: int = Form(0),
    fast_ma: int = Form(50),
    atr_period: int = Form(14),
    atr_multiplier: float = Form(1.5),
    use_atr_stop: bool = Form(True),
    volume_filter_days: int = Form(20),
    trailing_activation_pct: float = Form(2.0),
    trailing_pct: float = Form(8.0),
) -> dict[str, Any]:
    """Run a backtest from an uploaded CSV or Excel file.

    The file must contain 1-minute OHLC bars.  Supported column formats:
    • Standard:  Date, Time, Open, High, Low, Close, Volume
    • Stooq:     <DATE>, <TIME>, <OPEN>, <HIGH>, <LOW>, <CLOSE>, <VOL>
    • Combined:  Datetime (or Timestamp), Open, High, Low, Close, Volume
    • TradingView: time (Unix), open, high, low, close, volume
    """
    try:
        from backtest import parse_bars_from_bytes, run_backtest_from_file
        from config_loader import load_config

        content  = await file.read()
        filename = file.filename or "upload.csv"
        sym      = symbol.strip().upper()

        if not sym:
            raise ValueError("Symbol is required.")
        if len(content) > 50 * 1024 * 1024:  # 50 MB safety limit
            raise ValueError("File exceeds 50 MB limit.")

        bars = parse_bars_from_bytes(content, filename, sym)

        cfg = load_config(PROJECT_ROOT / "config.yaml")
        result = await run_backtest_from_file(
            symbol=sym,
            bars=bars,
            orb_config=cfg.strategy.orb,
            risk_config=cfg.risk,
            lookback=max(2, lookback_days),
            long_only=long_only,
            trend_ma=max(0, trend_ma),
            fast_ma=max(0, fast_ma),
            atr_period=max(2, atr_period),
            atr_multiplier=max(0.1, atr_multiplier),
            use_atr_stop=use_atr_stop,
            volume_filter_days=max(0, volume_filter_days),
            trailing_activation_pct=max(0.0, trailing_activation_pct),
            trailing_pct=max(0.0, trailing_pct),
        )

        payload = _result_to_dict(result)
        report_path = _save_report(payload)
        payload["report_file"] = report_path.name
        return payload

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Report download ───────────────────────────────────────────────────────────

@router.get("/backtest/report/{filename}")
async def download_report(filename: str) -> FileResponse:
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")
    path = REPORTS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not found.")
    return FileResponse(
        path=str(path),
        media_type="application/json",
        filename=filename,
    )
