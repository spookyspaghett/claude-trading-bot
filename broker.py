from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.models import Order, Position, TradeAccount
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    StopOrderRequest,
)

if TYPE_CHECKING:
    from config_loader import Config

PAPER_URL = "https://paper-api.alpaca.markets"


class LiveTradingNotConfirmedError(RuntimeError):
    pass


class BrokerClient:
    def __init__(self, config: Config) -> None:
        if config.live:
            print(
                "\n"
                "╔══════════════════════════════════════════════════╗\n"
                "║   WARNING: LIVE TRADING MODE ENABLED             ║\n"
                "║   This will place REAL orders with REAL money.   ║\n"
                "╚══════════════════════════════════════════════════╝\n"
                "\nType YES to confirm, anything else to abort: ",
                end="",
                flush=True,
            )
            answer = sys.stdin.readline().strip()
            if answer != "YES":
                raise LiveTradingNotConfirmedError("Live trading not confirmed. Aborting.")

        self._client = TradingClient(
            api_key=config.alpaca_api_key,
            secret_key=config.alpaca_secret_key,
            paper=not config.live,
        )

    # ── account / positions ─────────────────────────────────────────────────

    async def get_account(self) -> TradeAccount:
        return await asyncio.to_thread(self._client.get_account)  # type: ignore[return-value]

    async def get_all_positions(self) -> list[Position]:
        return await asyncio.to_thread(self._client.get_all_positions)  # type: ignore[return-value]

    # ── order submission ─────────────────────────────────────────────────────

    async def submit_market_order(
        self,
        symbol: str,
        qty: Decimal,
        side: OrderSide,
    ) -> Order:
        request = MarketOrderRequest(
            symbol=symbol,
            qty=float(qty),
            side=side,
            time_in_force=TimeInForce.DAY,
        )
        return await asyncio.to_thread(self._client.submit_order, request)  # type: ignore[return-value]

    async def submit_limit_order(
        self,
        symbol: str,
        qty: Decimal,
        side: OrderSide,
        limit_price: Decimal,
    ) -> Order:
        request = LimitOrderRequest(
            symbol=symbol,
            qty=float(qty),
            side=side,
            time_in_force=TimeInForce.DAY,
            limit_price=float(limit_price),
        )
        return await asyncio.to_thread(self._client.submit_order, request)  # type: ignore[return-value]

    async def submit_stop_order(
        self,
        symbol: str,
        qty: Decimal,
        side: OrderSide,
        stop_price: Decimal,
    ) -> Order:
        request = StopOrderRequest(
            symbol=symbol,
            qty=float(qty),
            side=side,
            time_in_force=TimeInForce.DAY,
            stop_price=float(stop_price),
        )
        return await asyncio.to_thread(self._client.submit_order, request)  # type: ignore[return-value]

    async def get_order(self, order_id: UUID) -> Order:
        return await asyncio.to_thread(  # type: ignore[return-value]
            self._client.get_order_by_id, str(order_id)
        )

    # ── bulk operations ──────────────────────────────────────────────────────

    async def cancel_all_orders(self) -> None:
        await asyncio.to_thread(self._client.cancel_orders)

    async def close_all_positions(self) -> None:
        await asyncio.to_thread(self._client.close_all_positions, cancel_orders=True)
