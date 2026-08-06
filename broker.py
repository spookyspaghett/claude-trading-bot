from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.models import Order, Position, TradeAccount
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    StopOrderRequest,
    TrailingStopOrderRequest,
)

if TYPE_CHECKING:
    from config_loader import Config

PAPER_URL = "https://paper-api.alpaca.markets"

# alpaca-py's RESTClient builds its request options without a `timeout` key, so
# `requests` blocks until the OS gives up on the socket — effectively never for a
# half-open connection. The bot awaits these calls inline on its main loop, so a
# single stalled socket froze everything: no kill-switch poll, no bar processing,
# no shutdown on SIGINT, and nothing in the log to say why. Bound every call.
REQUEST_TIMEOUT = 30.0


class LiveTradingNotConfirmedError(RuntimeError):
    pass


class BrokerTimeoutError(RuntimeError):
    """A broker call exceeded REQUEST_TIMEOUT. Subclasses RuntimeError so the
    existing `except Exception` handlers log and retry it like any other
    transient broker failure."""


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

        # Crypto trades 24/7 and only accepts GTC/IOC/FOK (not DAY); its prices
        # also aren't 2-dp, so we skip the stock rounding on limit/stop prices.
        self._is_crypto = config.asset_class == "crypto"
        self._tif = TimeInForce.GTC if self._is_crypto else TimeInForce.DAY

    def _round_price(self, price: Decimal) -> float:
        return float(price) if self._is_crypto else round(float(price), 2)

    async def _call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run a blocking alpaca-py call off the loop, bounded by a timeout.

        The worker thread itself can't be cancelled and will finish in the
        background; the point is that the event loop is never held hostage by it.
        """
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(fn, *args, **kwargs), timeout=REQUEST_TIMEOUT,
            )
        except TimeoutError as exc:
            raise BrokerTimeoutError(
                f"broker call {getattr(fn, '__name__', fn)!r} exceeded "
                f"{REQUEST_TIMEOUT}s"
            ) from exc

    # ── account / positions ─────────────────────────────────────────────────

    async def get_account(self) -> TradeAccount:
        return await self._call(self._client.get_account)  # type: ignore[return-value]

    async def get_all_positions(self) -> list[Position]:
        return await self._call(self._client.get_all_positions)  # type: ignore[return-value]

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
            time_in_force=self._tif,
        )
        return await self._call(self._client.submit_order, request)  # type: ignore[return-value]

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
            time_in_force=self._tif,
            limit_price=self._round_price(limit_price),
        )
        return await self._call(self._client.submit_order, request)  # type: ignore[return-value]

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
            time_in_force=self._tif,
            stop_price=self._round_price(stop_price),
        )
        return await self._call(self._client.submit_order, request)  # type: ignore[return-value]

    async def submit_trailing_stop_order(
        self,
        symbol: str,
        qty: Decimal,
        side: OrderSide,
        trail_percent: float,
    ) -> Order:
        request = TrailingStopOrderRequest(
            symbol=symbol,
            qty=float(qty),
            side=side,
            time_in_force=self._tif,
            trail_percent=trail_percent,
        )
        return await self._call(self._client.submit_order, request)  # type: ignore[return-value]

    async def get_order(self, order_id: UUID) -> Order:
        return await self._call(  # type: ignore[return-value]
            self._client.get_order_by_id, str(order_id)
        )

    async def cancel_order(self, order_id: str) -> None:
        await self._call(self._client.cancel_order_by_id, order_id)

    # ── bulk operations ──────────────────────────────────────────────────────

    async def cancel_all_orders(self) -> None:
        await self._call(self._client.cancel_orders)

    async def close_position(self, symbol: str) -> None:
        await self._call(self._client.close_position, symbol)

    async def close_all_positions(self) -> None:
        await self._call(self._client.close_all_positions, cancel_orders=True)
