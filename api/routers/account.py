from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from api.deps import get_trading_client

router = APIRouter()


@router.get("/account")
async def get_account() -> dict[str, str]:
    try:
        client = get_trading_client()
        acct = await asyncio.to_thread(client.get_account)
        return {
            "equity": str(acct.equity or "0"),
            "portfolio_value": str(acct.portfolio_value or "0"),
            "buying_power": str(acct.buying_power or "0"),
            "cash": str(acct.cash or "0"),
            "daily_pnl": str(getattr(acct, "equity_previous_close", None) and
                             float(str(acct.equity or 0)) - float(str(getattr(acct, "equity_previous_close", acct.equity) or 0))
                             or "0"),
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/equity-history")
async def get_equity_history() -> list[dict[str, object]]:
    """30-day daily equity curve from Alpaca portfolio history."""
    try:
        client = get_trading_client()
        from alpaca.trading.requests import GetPortfolioHistoryRequest
        req = GetPortfolioHistoryRequest(period="1M", timeframe="1D")
        history = await asyncio.to_thread(client.get_portfolio_history, req)
        if not history or not history.timestamp:
            return []
        return [
            {
                "timestamp": ts,
                "equity": float(eq) if eq is not None else 0.0,
                "profit_loss": float(pl) if pl is not None else 0.0,
            }
            for ts, eq, pl in zip(
                history.timestamp,
                history.equity or [],
                history.profit_loss or [],
            )
        ]
    except Exception:
        # Return empty list so the chart just shows nothing rather than
        # breaking the UI (common outside market hours / fresh accounts)
        return []


@router.get("/pnl-intraday")
async def get_intraday_pnl() -> list[dict[str, object]]:
    """1-minute P&L snapshots for today's session."""
    try:
        client = get_trading_client()
        from alpaca.trading.requests import GetPortfolioHistoryRequest
        req = GetPortfolioHistoryRequest(period="1D", timeframe="1Min")
        history = await asyncio.to_thread(client.get_portfolio_history, req)
        if not history or not history.timestamp:
            return []
        return [
            {
                "timestamp": ts,
                "profit_loss": float(pl) if pl is not None else 0.0,
            }
            for ts, pl in zip(
                history.timestamp,
                history.profit_loss or [],
            )
        ]
    except Exception:
        return []
