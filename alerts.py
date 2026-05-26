from __future__ import annotations

import os
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")


def _enabled() -> bool:
    return bool(_TOKEN and _CHAT_ID)


async def _send(text: str) -> None:
    """Fire-and-forget Telegram message. Never raises — alerts must not crash the bot."""
    if not _enabled():
        return
    url = f"https://api.telegram.org/bot{_TOKEN}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": _CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload, timeout=5.0)
    except Exception:
        pass


async def alert_startup(symbols: list[str]) -> None:
    await _send(f"🤖 *Claude Trading started*\nWatching: `{', '.join(symbols)}`")


async def alert_shutdown() -> None:
    await _send("👋 *Claude Trading stopped*")


async def alert_signal(symbol: str, direction: str, price: str, reason: str) -> None:
    emoji = "🟢" if direction == "BUY" else "🔴" if direction == "SELL" else "⚪"
    await _send(
        f"{emoji} *{direction}* — `{symbol}`\n"
        f"Price: `${price}`\n"
        f"_{reason}_"
    )


async def alert_fill(symbol: str, side: str, qty: str, price: str) -> None:
    await _send(f"✅ *FILL* — {side} `{qty}` x `{symbol}` @ `${price}`")


async def alert_daily_limit(pnl: str) -> None:
    await _send(
        f"🚨 *DAILY LOSS LIMIT HIT*\n"
        f"P&L: `${pnl}`\n"
        f"All positions flattened. Bot halted for today."
    )


async def alert_kill_switch() -> None:
    await _send("🛑 *KILL SWITCH ACTIVATED*\nFlattening all positions and shutting down.")


async def alert_error(event: str, detail: str) -> None:
    await _send(f"⚠️ *Error:* `{event}`\n`{detail[:200]}`")
