from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from api.deps import get_trading_client

router = APIRouter()


@router.get("/positions")
async def get_positions(profile: str | None = None) -> list[dict[str, str]]:
    try:
        client = get_trading_client(profile)
        positions = await asyncio.to_thread(client.get_all_positions)
        return [
            {
                "symbol": str(p.symbol),
                "qty": str(p.qty),
                "side": str(p.side.value) if p.side else "",
                "avg_entry_price": str(p.avg_entry_price or "0"),
                "current_price": str(p.current_price or "0"),
                "unrealized_pl": str(p.unrealized_pl or "0"),
                "unrealized_plpc": str(p.unrealized_plpc or "0"),
                "market_value": str(p.market_value or "0"),
            }
            for p in positions
        ]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
