from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from api.deps import get_trading_client

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

router = APIRouter()

# Supported timeframes → (TimeFrame args, minutes per bar) resolved lazily to
# avoid importing alpaca at module import time in non-bar code paths.
_TF: dict[str, tuple[int, str, int]] = {
    "1Min":  (1, "Min", 1),
    "5Min":  (5, "Min", 5),
    "15Min": (15, "Min", 15),
    "1Hour": (1, "Hour", 60),
    "1Day":  (1, "Day", 60 * 24),
}

# Accept short forms (what the UI buttons show and what's natural to type).
_TF_ALIASES: dict[str, str] = {
    "1m": "1Min", "5m": "5Min", "15m": "15Min", "1h": "1Hour", "1d": "1Day",
}


def _resolve_tf(timeframe: str) -> str:
    if timeframe in _TF:
        return timeframe
    return _TF_ALIASES.get(timeframe.lower(), timeframe)


def _indicator_meta(cfg: Any) -> dict[str, Any]:
    """Expose the moving averages / pivot params the active strategy uses so the
    chart can overlay them."""
    strat = cfg.strategy
    name = strat.name
    if name == "trend_sr":
        t = strat.trend_sr
        return {"strategy": name, "ma_fast": t.ma_fast, "ma_slow": t.ma_slow,
                "regime_ma": t.regime_ma,
                "pivot_lookback": t.pivot_lookback, "pivot_strength": t.pivot_strength}
    if name == "ema":
        return {"strategy": name, "ma_fast": strat.ema.fast_period,
                "ma_slow": strat.ema.slow_period, "regime_ma": 0,
                "pivot_lookback": 0, "pivot_strength": 0}
    if name == "donchian":
        return {"strategy": name, "ma_fast": 0, "ma_slow": strat.donchian.trend_ma,
                "regime_ma": 0,
                "pivot_lookback": strat.donchian.lookback_days, "pivot_strength": 0}
    return {"strategy": name, "ma_fast": 0, "ma_slow": 0, "regime_ma": 0,
            "pivot_lookback": 0, "pivot_strength": 0}


def _fetch_bars_sync(
    api_key: str, secret_key: str, is_crypto: bool,
    symbol: str, tf_amt: int, tf_unit: str, mins_per_bar: int, limit: int,
) -> list[dict[str, Any]]:
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import (
        CryptoHistoricalDataClient,
        StockHistoricalDataClient,
    )
    from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    unit = {"Min": TimeFrameUnit.Minute, "Hour": TimeFrameUnit.Hour,
            "Day": TimeFrameUnit.Day}[tf_unit]
    timeframe = TimeFrame(tf_amt, unit)
    now = datetime.now(tz=timezone.utc)
    # Generous window with a 1-day buffer so clock skew / quiet periods can't
    # land us in an empty slice. We don't pass `limit` to the request (which can
    # truncate to the oldest bars in the window) — we slice the newest below.
    start = now - timedelta(minutes=mins_per_bar * limit * 4 + 1440)

    if is_crypto:
        client: Any = CryptoHistoricalDataClient(api_key, secret_key)
        req: Any = CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=timeframe,
                                     start=start, end=now)
        raw = client.get_crypto_bars(req)
    else:
        client = StockHistoricalDataClient(api_key, secret_key)
        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=timeframe,
                               start=start, end=now, feed=DataFeed.IEX)
        raw = client.get_stock_bars(req)

    # BarSet access can be unreliable via `in`; read the underlying dict directly.
    raw_data = getattr(raw, "data", None)
    if isinstance(raw_data, dict):
        bars = list(raw_data.get(symbol, []))
    else:
        try:
            bars = list(raw[symbol])
        except (KeyError, TypeError):
            bars = []
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for b in bars[-limit:]:
        t = int(b.timestamp.timestamp())
        if t in seen:
            continue
        seen.add(t)
        out.append({"time": t, "open": float(b.open), "high": float(b.high),
                    "low": float(b.low), "close": float(b.close),
                    "volume": float(b.volume) if getattr(b, "volume", None) else 0.0})
    return out


def _fetch_markers_sync(
    client: Any, symbol: str, mins_per_bar: int,
) -> list[dict[str, Any]]:
    """Buy/sell markers from this account's filled orders for the symbol."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    try:
        req = GetOrdersRequest(
            status=QueryOrderStatus.CLOSED, symbols=[symbol], limit=200,
        )
        orders = client.get_orders(filter=req)
    except Exception:
        return []

    bucket = mins_per_bar * 60
    markers: list[dict[str, Any]] = []
    for o in orders:
        filled_at = getattr(o, "filled_at", None)
        price = getattr(o, "filled_avg_price", None)
        if filled_at is None or price is None:
            continue
        ts = int(filled_at.timestamp())
        snapped = (ts // bucket) * bucket  # align to the candle's open time
        side = str(o.side.value) if getattr(o, "side", None) else ""
        markers.append({"time": snapped, "side": side, "price": float(price)})
    markers.sort(key=lambda m: m["time"])
    return markers


@router.get("/bars")
async def get_bars(
    symbol: str,
    profile: str | None = None,
    timeframe: str = "15Min",
    limit: int = 200,
) -> dict[str, Any]:
    timeframe = _resolve_tf(timeframe)
    if timeframe not in _TF:
        raise HTTPException(status_code=422,
                            detail=f"timeframe must be one of {list(_TF)}")
    limit = max(10, min(limit, 1000))
    tf_amt, tf_unit, mins = _TF[timeframe]

    from config_loader import Config
    from profiles import load_active_config, load_profile
    try:
        if profile:
            data = load_profile(profile)
            cfg = Config(**{k: v for k, v in data.items() if k != "name"})
        else:
            cfg = load_active_config()
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    sym = symbol.strip().upper()
    is_crypto = cfg.asset_class == "crypto"

    try:
        bars = await asyncio.to_thread(
            _fetch_bars_sync, cfg.alpaca_api_key, cfg.alpaca_secret_key,
            is_crypto, sym, tf_amt, tf_unit, mins, limit,
        )
        client = get_trading_client(profile)
        markers = await asyncio.to_thread(_fetch_markers_sync, client, sym, mins)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Only keep markers within the visible window.
    if bars:
        lo = bars[0]["time"]
        markers = [m for m in markers if m["time"] >= lo]

    return {
        "symbol": sym,
        "timeframe": timeframe,
        "bars": bars,
        "markers": markers,
        "indicators": _indicator_meta(cfg),
    }
