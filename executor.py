from __future__ import annotations

from dataclasses import dataclass
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


@dataclass
class _PendingEntry:
    """An entry order awaiting fill, with the protective stop to place on fill."""
    order: Order
    direction: Direction
    qty: Decimal
    stop_price: Decimal


@dataclass
class _OpenPos:
    """A filled position with a live broker stop that trails the peak price."""
    direction: Direction
    qty: Decimal
    stop_order_id: str
    stop_price: Decimal
    peak: Decimal          # best price seen since entry (high for long, low for short)


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
        fractional: bool = False,
        place_broker_stop: bool = True,
    ) -> None:
        self._broker = broker
        self._risk = risk
        self._entry_order_type = entry_order_type
        self._fractional = fractional
        # Stocks get a real broker-side protective stop at the configured
        # stop_loss price. Alpaca rejects stop orders for crypto, so crypto
        # relies on the strategy's own exit signals instead.
        self._place_broker_stop = place_broker_stop
        self._trailing_stop_pct = trailing_stop_pct
        self._trail = Decimal(str(trailing_stop_pct)) / Decimal("100")
        self._loser_cut_threshold = loser_cut_pct / Decimal("100")
        self._pending_entries: dict[str, _PendingEntry] = {}
        # stop_order_id → symbol
        self._pending_stops: dict[str, str] = {}
        # symbol → live position whose broker stop trails the peak price
        self._open: dict[str, _OpenPos] = {}
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

        qty = self._risk.compute_qty(signal.entry_price, fractional=self._fractional)
        if qty <= Decimal("0"):
            log_rejection(
                order_id="N/A",
                symbol=signal.symbol,
                reason="position size computed as 0",
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
        # Store the protective stop price so _poll_entries can place it on fill.
        self._pending_entries[order_id] = _PendingEntry(
            order=order, direction=signal.direction, qty=qty,
            stop_price=signal.stop_price,
        )
        # Count this toward the open limit until it fills/cancels (#9).
        self._risk.register_pending(signal.symbol)

    async def flatten_all(self) -> None:
        """Cancel all open orders and market-close all positions."""
        try:
            await self._broker.cancel_all_orders()
            await self._broker.close_all_positions()
            self._pending_entries.clear()
            self._pending_stops.clear()
            self._cost_basis.clear()
            self._open.clear()
        except Exception as exc:
            log_error("flatten_all_failed", error=str(exc))

    async def poll_order_status(self) -> None:
        """Refresh pending entry and stop orders; log fills and update state."""
        await self._poll_entries()
        await self._poll_stops()

    async def _raise_stop(
        self, symbol: str, op: _OpenPos, new_stop: Decimal, stop_side: OrderSide,
    ) -> None:
        """Cancel the live stop and re-place it at a tighter (trailed) price."""
        try:
            await self._broker.cancel_order(op.stop_order_id)
            self._pending_stops.pop(op.stop_order_id, None)
            order = await self._broker.submit_stop_order(
                symbol=symbol, qty=op.qty, side=stop_side, stop_price=new_stop,
            )
            op.stop_order_id = str(order.id)
            op.stop_price = new_stop
            self._pending_stops[op.stop_order_id] = symbol
            _log.info("trail_raised", symbol=symbol, new_stop=str(new_stop))
        except Exception as exc:
            log_error("trail_raise_failed", symbol=symbol, error=str(exc))

    async def _update_trailing_stop(self, symbol: str, current_price: Decimal) -> None:
        op = self._open.get(symbol)
        if op is None or not self._place_broker_stop:
            return
        if self._trail <= 0 or current_price <= 0:
            return
        one = Decimal("1")
        if op.direction == Direction.BUY:
            op.peak = max(op.peak, current_price)
            candidate = (op.peak * (one - self._trail)).quantize(Decimal("0.01"))
            if candidate > op.stop_price:
                await self._raise_stop(symbol, op, candidate, OrderSide.SELL)
        else:
            op.peak = min(op.peak, current_price)
            candidate = (op.peak * (one + self._trail)).quantize(Decimal("0.01"))
            if candidate < op.stop_price:
                await self._raise_stop(symbol, op, candidate, OrderSide.BUY)

    async def poll_positions(self) -> None:
        """Trail each open position's stop up toward its peak, and hard-cut any
        position whose unrealized loss exceeds the cut threshold."""
        try:
            positions = await self._broker.get_all_positions()
        except Exception as exc:
            log_error("poll_positions_failed", error=str(exc))
            return

        # Feed equity + total unrealized PnL to the risk manager so the daily
        # loss limit trips on deep drawdown and exposure is capped to equity (#9).
        total_unrealized = sum(
            (Decimal(str(p.unrealized_pl or "0")) for p in positions), Decimal("0")
        )
        self._risk.set_unrealized(total_unrealized)
        try:
            acct = await self._broker.get_account()
            self._risk.set_account_equity(Decimal(str(acct.equity or "0")))
        except Exception:
            pass

        for pos in positions:
            symbol = str(pos.symbol)
            cur_price = Decimal(str(pos.current_price or "0"))
            await self._update_trailing_stop(symbol, cur_price)
            try:
                plpc = Decimal(str(pos.unrealized_plpc or "0"))
            except Exception:
                continue
            # Only manage positions the bot itself opened (#12).
            if symbol not in self._cost_basis:
                continue
            if plpc <= -self._loser_cut_threshold:
                _log.info("loser_cut", symbol=symbol, unrealized_plpc=str(plpc))
                try:
                    # Cancel the dangling protective stop before closing.
                    op = self._open.get(symbol)
                    if op is not None:
                        try:
                            await self._broker.cancel_order(op.stop_order_id)
                        except Exception:
                            pass
                        self._pending_stops.pop(op.stop_order_id, None)
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
                    self._open.pop(symbol, None)
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
                    pending = self._pending_entries[order_id]
                    direction = pending.direction
                    stop_price = pending.stop_price
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
                    self._risk.clear_pending(symbol)
                    del self._pending_entries[order_id]

                    # Place the configured protective stop as a real broker order
                    # (stocks only). Crypto can't use broker stops on Alpaca, so it
                    # relies on the strategy's own exit signals instead.
                    if self._place_broker_stop and stop_price > 0:
                        stop_side = OrderSide.SELL if direction == Direction.BUY else OrderSide.BUY
                        try:
                            stop_order = await self._broker.submit_stop_order(
                                symbol=symbol,
                                qty=fill_qty,
                                side=stop_side,
                                stop_price=stop_price,
                            )
                            stop_id = str(stop_order.id)
                            self._pending_stops[stop_id] = symbol
                            # Track for trailing: the stop will ratchet up toward
                            # the peak price on each poll_positions cycle.
                            self._open[symbol] = _OpenPos(
                                direction=direction, qty=fill_qty,
                                stop_order_id=stop_id, stop_price=stop_price,
                                peak=fill_price,
                            )
                        except Exception as exc:
                            log_error(
                                "protective_stop_failed",
                                symbol=symbol,
                                stop=str(stop_price),
                                error=str(exc),
                            )

            elif status in (OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED):
                log_rejection(
                    order_id=order_id,
                    symbol=str(order.symbol),
                    reason=str(status.value),
                )
                self._risk.clear_pending(str(order.symbol))
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
                        reason="protective stop triggered",
                    )
                    qty_signed = filled_qty if order.side == OrderSide.BUY else -filled_qty
                    self._risk.record_fill(symbol=symbol, qty=qty_signed, realised_pnl=pnl)
                    del self._pending_stops[order_id]
                    self._open.pop(symbol, None)

            elif status in (OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED):
                del self._pending_stops[order_id]
