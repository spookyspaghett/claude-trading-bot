from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

router = APIRouter()


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

        return {
            "symbol": result.symbol,
            "start_date": result.start_date,
            "end_date": result.end_date,
            "equity_curve": result.equity_curve,
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
                    "symbol":       t.symbol,
                    "direction":    t.direction,
                    "entry_time":   t.entry_time.isoformat(),
                    "entry_price":  str(t.entry_price),
                    "exit_time":    t.exit_time.isoformat() if t.exit_time else None,
                    "exit_price":   str(t.exit_price) if t.exit_price else None,
                    "exit_reason":  t.exit_reason,
                    "qty":          str(t.qty),
                    "pnl":          str(t.pnl.quantize(t.pnl.__class__("0.01"))),
                }
                for t in result.trades
            ],
        }

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
