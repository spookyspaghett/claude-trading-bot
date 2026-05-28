from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import UUID

import structlog
from alpaca.trading.enums import OrderSide, OrderStatus
from alpaca.trading.models import Order

import alerts
from broker import BrokerClient
from claude_advisor import SignalAdvisor
from journal import TradeJournal
from logger import log_error, log_fill, log_order, log_rejection, log_signal
from research import SymbolResearch
from risk import RiskManager
from strategy import Direction, Signal

_log: structlog.stdlib.BoundLogger = structlog.get_logger()  # type: ignore[assignment]


class OrderExecutor:
    """Converts strategy signals into broker orders after risk + AI checks."""

    def __init__(
        self,
        broker: BrokerClient,
        risk: RiskManager,
        entry_order_type: str = "limit",
        trailing_stop_pct: float = 10.0,
        loser_cut_pct: Decimal = Decimal("7"),
        enable_claude_filter: bool = False,
    ) -> None:
        self._broker = broker
        self._risk = risk
        self._entry_order_type = entry_order_type
        self._trailing_stop_pct = trailing_stop_pct
        self._loser_cut_threshold = loser_cut_pct / Decimal("100")
        # order_id → (Order, direction)
        self._pending_entries: dict[str, tuple[Order, Direction]] = {}
        # stop_order_id → symbol
        self._pending_stops: dict[str, str] = {}
        # symbol → (fill_price, direction)
        self._cost_basis: dict[str, tuple[Decimal, Direction]] = {}
        self._journal = TradeJournal()
        self._advisor = SignalAdvisor() if enable_claude_filter else None
        self._research: dict[str, SymbolResearch] = {}

    def set_research(self, research: dict[str, SymbolResearch]) -> None:
        self._research = research

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

        # Research gate: skip symbols with very low scores
        research = self._research.get(signal.symbol)
        if research is not None and research.score < 4:
            log_rejection(
                order_id="N/A",
                symbol=signal.symbol,
                reason=f"research score {research.score}/10 — skipping",
            )
            return

        # Claude approval gate
        if self._advisor is not None and self._advisor.enabled():
            approved, reasoning = await self._advisor.approve(
                symbol=signal.symbol,
                direction=signal.direction.value,
                entry_price=signal.entry_price,
                signal_reason=signal.reason,
                research_summary=research.summary if research else "",
                daily_pnl=self._risk.daily_pnl,
            )
            if not approved:
                log_rejection(
                    order_id="N/A",
                    symbol=signal.symbol,
                    reason=f"Claude rejected: {reasoning}",
                )
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
        self._pending_entries[order_id] = (order, signal.direction)

        # Trailing stop on the protective side
        stop_side = OrderSide.SELL if signal.direction == Direction.BUY else OrderSide.BUY
        try:
            stop_order = await self._broker.submit_trailing_stop_order(
                symbol=signal.symbol,
                qty=qty,
                side=stop_side,
                trail_percent=self._trailing_stop_pct,
            )
            self._pending_stops[str(stop_order.id)] = signal.symbol
        except Exception as exc:
            log_error(
                "trailing_stop_failed",
                symbol=signal.symbol,
                trail_pct=self._trailing_stop_pct,
                error=str(exc),
            )

    async def flatten_all(self) -> None:
        """Cancel all open orders and market-close all positions."""
        try:
            await self._broker.cancel_all_orders()
            await self._broker.close_all_positions()
            self._pending_entries.clear()
            self._pending_stops.clear()
            self._cost_basis.clear()
        except Exception as exc:
            log_error("flatten_all_failed", error=str(exc))

    async def poll_order_status(self) -> None:
        """Refresh pending entry and stop orders; log fills and update state."""
        await self._poll_entries()
        await self._poll_stops()

    async def poll_positions(self) -> None:
        """Close any open position whose unrealized loss exceeds the cut threshold."""
        try:
            positions = await self._broker.get_all_positions()
        except Exception as exc:
            log_error("poll_positions_failed", error=str(exc))
            return
        for pos in positions:
            try:
                plpc = Decimal(str(pos.unrealized_plpc or "0"))
            except Exception:
                continue
            if plpc <= -self._loser_cut_threshold:
                symbol = str(pos.symbol)
                _log.info("loser_cut", symbol=symbol, unrealized_plpc=str(plpc))
                try:
                    await self._broker.close_position(symbol)
                    pnl = Decimal(str(pos.unrealized_pl or "0"))
                    qty = abs(Decimal(str(pos.qty or "0")))
                    price = Decimal(str(pos.current_price or "0"))
                    self._journal.record_exit(
                        symbol=symbol,
                        side="loser_cut",
                        qty=qty,
                        price=price,
                        realized_pnl=pnl,
                        reason=f"loser cut at {float(plpc) * 100:.1f}%",
                    )
                    pos_qty = Decimal(str(pos.qty or "0"))
                    self._risk.record_fill(symbol=symbol, qty=-pos_qty, realised_pnl=pnl)
                    self._cost_basis.pop(symbol, None)
                except Exception as exc:
                    log_error("loser_cut_failed", symbol=symbol, error=str(exc))

    def end_of_day(self) -> None:
        """Write daily trade summary. Call once at EOD or shutdown."""
        self._journal.write_daily_summary(self._risk.daily_pnl)

    # ── internals ─────────────────────────────────────────────────────────────

    async def _poll_entries(self) -> None:
        for order_id in list(self._pending_entries.keys()):
            try:
                order = await self._broker.get_order(UUID(order_id))
            except Exception as exc:
                log_error("poll_order_failed", order_id=order_id, error=str(exc))
                continue

            status = order.status
            if status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
                filled_qty_str = str(order.filled_qty or "0")
                filled_price_str = str(order.filled_avg_price or "0")
                log_fill(
                    order_id=order_id,
                    symbol=str(order.symbol),
                    filled_qty=filled_qty_str,
                    filled_avg_price=filled_price_str,
                )
                await alerts.alert_fill(
                    symbol=str(order.symbol),
                    side=str(order.side.value) if order.side else "",
                    qty=filled_qty_str,
                    price=filled_price_str,
                )
                if status == OrderStatus.FILLED:
                    _, direction = self._pending_entries[order_id]
                    symbol = str(order.symbol)
                    fill_price = Decimal(filled_price_str)
                    fill_qty = Decimal(filled_qty_str)
                    self._cost_basis[symbol] = (fill_price, direction)
                    self._journal.record_entry(
                        symbol=symbol,
                        side=direction.value,
                        qty=fill_qty,
                        price=fill_price,
                        reason="entry fill",
                    )
                    qty_signed = fill_qty if order.side == OrderSide.BUY else -fill_qty
                    self._risk.record_fill(symbol=symbol, qty=qty_signed, realised_pnl=Decimal("0"))
                    del self._pending_entries[order_id]

            elif status in (OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED):
                log_rejection(
                    order_id=order_id,
                    symbol=str(order.symbol),
                    reason=str(status.value),
                )
                del self._pending_entries[order_id]

    async def _poll_stops(self) -> None:
        for order_id in list(self._pending_stops.keys()):
            try:
                order = await self._broker.get_order(UUID(order_id))
            except Exception as exc:
                log_error("poll_stop_failed", order_id=order_id, error=str(exc))
                continue

            status = order.status
            if status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
                symbol = self._pending_stops[order_id]
                filled_qty = Decimal(str(order.filled_qty or "0"))
                filled_price = Decimal(str(order.filled_avg_price or "0"))
                log_fill(
                    order_id=order_id,
                    symbol=symbol,
                    filled_qty=str(filled_qty),
                    filled_avg_price=str(filled_price),
                )
                await alerts.alert_fill(
                    symbol=symbol,
                    side=str(order.side.value) if order.side else "",
                    qty=str(filled_qty),
                    price=str(filled_price),
                )
                if status == OrderStatus.FILLED:
                    pnl = Decimal("0")
                    basis = self._cost_basis.get(symbol)
                    if basis is not None:
                        entry_price, entry_dir = basis
                        if entry_dir == Direction.BUY:
                            pnl = (filled_price - entry_price) * filled_qty
                        else:
                            pnl = (entry_price - filled_price) * filled_qty
                        del self._cost_basis[symbol]
                    self._journal.record_exit(
                        symbol=symbol,
                        side=str(order.side.value) if order.side else "",
                        qty=filled_qty,
                        price=filled_price,
                        realized_pnl=pnl,
                        reason="trailing stop triggered",
                    )
                    qty_signed = filled_qty if order.side == OrderSide.BUY else -filled_qty
                    self._risk.record_fill(symbol=symbol, qty=qty_signed, realised_pnl=pnl)
                    del self._pending_stops[order_id]

            elif status in (OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED):
                del self._pending_stops[order_id]
