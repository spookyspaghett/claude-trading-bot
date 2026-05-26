from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import UUID

import structlog
from alpaca.trading.enums import OrderSide, OrderStatus
from alpaca.trading.models import Order

import alerts
from broker import BrokerClient
from logger import log_error, log_fill, log_order, log_rejection, log_signal
from risk import RiskManager
from strategy import Direction, Signal

_log: structlog.stdlib.BoundLogger = structlog.get_logger()  # type: ignore[assignment]


class OrderExecutor:
    """Converts strategy signals into broker orders after passing risk checks."""

    def __init__(
        self,
        broker: BrokerClient,
        risk: RiskManager,
        entry_order_type: str = "limit",
    ) -> None:
        self._broker = broker
        self._risk = risk
        self._entry_order_type = entry_order_type
        self._pending: dict[str, Order] = {}  # order_id -> Order

    # ── public interface ──────────────────────────────────────────────────────

    async def process_signal(self, signal: Signal) -> None:
        log_signal(
            symbol=signal.symbol,
            direction=signal.direction.value,
            price=str(signal.entry_price),
            reason=signal.reason,
        )
        await alerts.alert_signal(
            symbol=signal.symbol,
            direction=signal.direction.value,
            price=str(signal.entry_price),
            reason=signal.reason,
        )

        if signal.direction == Direction.FLAT:
            await self.flatten_all()
            return

        ok, reason = self._risk.check_new_order(signal.symbol)
        if not ok:
            log_rejection(order_id="N/A", symbol=signal.symbol, reason=reason)
            return

        qty = self._risk.compute_qty(signal.entry_price)
        if qty <= Decimal("0"):
            log_rejection(
                order_id="N/A",
                symbol=signal.symbol,
                reason="position size computed as 0 shares",
            )
            return

        side = OrderSide.BUY if signal.direction == Direction.BUY else OrderSide.SELL

        try:
            if self._entry_order_type == "limit":
                order = await self._broker.submit_limit_order(
                    symbol=signal.symbol,
                    qty=qty,
                    side=side,
                    limit_price=signal.entry_price,
                )
            else:
                order = await self._broker.submit_market_order(
                    symbol=signal.symbol,
                    qty=qty,
                    side=side,
                )
        except Exception as exc:
            log_error("order_submit_failed", symbol=signal.symbol, error=str(exc))
            await alerts.alert_error("order_submit_failed", str(exc))
            return

        order_id = str(order.id)
        log_order(
            order_id=order_id,
            symbol=signal.symbol,
            side=side.value,
            qty=str(qty),
            price=str(signal.entry_price),
            order_type=self._entry_order_type,
        )
        self._pending[order_id] = order

        # Attach a protective stop on the other side
        stop_side = OrderSide.SELL if signal.direction == Direction.BUY else OrderSide.BUY
        try:
            await self._broker.submit_stop_order(
                symbol=signal.symbol,
                qty=qty,
                side=stop_side,
                stop_price=signal.stop_price,
            )
        except Exception as exc:
            log_error(
                "stop_order_failed",
                symbol=signal.symbol,
                stop_price=str(signal.stop_price),
                error=str(exc),
            )

    async def flatten_all(self) -> None:
        """Cancel all open orders and market-close all positions."""
        try:
            await self._broker.cancel_all_orders()
            await self._broker.close_all_positions()
            self._pending.clear()
        except Exception as exc:
            log_error("flatten_all_failed", error=str(exc))

    async def poll_order_status(self) -> None:
        """Refresh pending orders and log fills / rejections."""
        for order_id in list(self._pending.keys()):
            try:
                order = await self._broker.get_order(UUID(order_id))
            except Exception as exc:
                log_error("poll_order_failed", order_id=order_id, error=str(exc))
                continue

            status = order.status
            if status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
                filled_qty = str(order.filled_qty or "0")
                filled_price = str(order.filled_avg_price or "0")
                log_fill(
                    order_id=order_id,
                    symbol=str(order.symbol),
                    filled_qty=filled_qty,
                    filled_avg_price=filled_price,
                )
                await alerts.alert_fill(
                    symbol=str(order.symbol),
                    side=str(order.side.value) if order.side else "",
                    qty=filled_qty,
                    price=filled_price,
                )
                if status == OrderStatus.FILLED:
                    qty_signed = Decimal(filled_qty)
                    if order.side == OrderSide.SELL:
                        qty_signed = -qty_signed
                    self._risk.record_fill(
                        symbol=str(order.symbol),
                        qty=qty_signed,
                        realised_pnl=Decimal("0"),
                    )
                    del self._pending[order_id]

            elif status in (OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED):
                log_rejection(
                    order_id=order_id,
                    symbol=str(order.symbol),
                    reason=str(status.value),
                )
                del self._pending[order_id]
