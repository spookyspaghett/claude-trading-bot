from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from decimal import Decimal
from zoneinfo import ZoneInfo

from alpaca.data.enums import DataFeed
from alpaca.data.historical import (
    CryptoHistoricalDataClient,
    StockHistoricalDataClient,
)
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.enums import OrderSide

import alerts
from broker import BrokerClient
from donchian_strategy import DonchianLiveStrategy
from logger import log_error, log_info
from risk import RiskManager

ET = ZoneInfo("America/New_York")

# Time windows (ET)
_EOD_START   = dtime(16, 5)
_EOD_END     = dtime(16, 12)
_OPEN_START  = dtime(9, 31)
_OPEN_END    = dtime(9, 36)
_MKTCLOSE    = dtime(16, 0)

# Crypto daily scan window (UTC) — bars roll at 00:00 UTC.
_CRYPTO_SCAN_START = dtime(0, 5)
_CRYPTO_SCAN_END   = dtime(0, 20)


class DonchianRunner:
    """Drives the daily Donchian strategy in live/paper mode.

    Timeline each trading day:
      16:05 ET  — EOD scan: compute signals for all symbols, queue actions
      09:31 ET  — Place orders queued from last night's scan
      Every 60s during market hours — Check open positions vs stop prices
    """

    def __init__(
        self,
        symbols: list[str],
        broker: BrokerClient,
        risk: RiskManager,
        strategy: DonchianLiveStrategy,
        api_key: str,
        secret_key: str,
        asset_class: str = "stock",
    ) -> None:
        self._symbols      = symbols
        self._broker       = broker
        self._risk         = risk
        self._strategy     = strategy
        self._is_crypto    = asset_class == "crypto"
        self._data_client: CryptoHistoricalDataClient | StockHistoricalDataClient = (
            CryptoHistoricalDataClient(api_key, secret_key)
            if self._is_crypto
            else StockHistoricalDataClient(api_key, secret_key)
        )
        # Queued from EOD scan, executed at next open
        self._queued_entries: dict[str, str] = {}   # symbol → "enter_long"|"enter_short"
        self._queued_exits: set[str]   = set()
        # Debounce: track which time-windows we already ran today
        self._ran_eod_date:   str = ""
        self._ran_open_date:  str = ""

    # ── main loop ─────────────────────────────────────────────────────────────

    def _rederive_queues(self) -> None:
        """Rebuild the in-memory entry/exit queues from persisted state so a
        restart can't strand a position or lose a queued action (#5)."""
        for sym in self._strategy.positions_pending_exit():
            self._queued_exits.add(sym)
        for sym in self._strategy.positions_pending_entry():
            direction = self._strategy.direction_of(sym)
            self._queued_entries[sym] = "enter_long" if direction == "BUY" else "enter_short"
        if self._queued_entries or self._queued_exits:
            log_info("donchian_queues_rederived",
                     entries=list(self._queued_entries), exits=list(self._queued_exits))

    async def run(self, shutdown_event: asyncio.Event) -> None:
        log_info("donchian_runner_started", symbols=self._symbols,
                 asset_class="crypto" if self._is_crypto else "stock")
        await alerts.alert_startup(self._symbols)
        self._rederive_queues()

        if self._is_crypto:
            await self._run_crypto(shutdown_event)
            return

        while not shutdown_event.is_set():
            now      = datetime.now(tz=ET)
            t        = now.time()
            today_s  = str(now.date())

            # 16:05–16:12 → EOD scan (once per day)
            if _EOD_START <= t <= _EOD_END and self._ran_eod_date != today_s:
                self._ran_eod_date = today_s
                await self._run_eod_scan()
                await asyncio.sleep(60)
                continue

            # 09:31–09:36 → place morning orders (once per day)
            if _OPEN_START <= t <= _OPEN_END and self._ran_open_date != today_s:
                self._ran_open_date = today_s
                await self._place_morning_orders()
                await asyncio.sleep(60)
                continue

            # During market hours → check positions every 60 s
            if _OPEN_START <= t < _MKTCLOSE:
                await self._check_open_positions()
                await asyncio.sleep(60)
                continue

            await asyncio.sleep(30)

    async def _run_crypto(self, shutdown_event: asyncio.Event) -> None:
        """24/7 loop: scan once daily at ~00:05 UTC, enter immediately at market,
        and check stops every 60 s around the clock."""
        while not shutdown_event.is_set():
            now     = datetime.now(tz=timezone.utc)
            t       = now.time()
            today_s = str(now.date())

            if _CRYPTO_SCAN_START <= t <= _CRYPTO_SCAN_END and self._ran_eod_date != today_s:
                self._ran_eod_date = today_s
                await self._run_eod_scan()
                # Crypto trades continuously — no "next open" wait, enter now.
                await self._place_morning_orders()
                await asyncio.sleep(60)
                continue

            await self._check_open_positions()
            await asyncio.sleep(60)

    # ── EOD scan ─────────────────────────────────────────────────────────────

    async def _fetch_daily_bars(self, symbol: str, n: int = 260) -> list:
        now = datetime.now(tz=timezone.utc if self._is_crypto else ET)
        if self._is_crypto:
            # Crypto trades every calendar day — no weekend/holiday buffer needed.
            start = now - timedelta(days=n + 5)
            req: CryptoBarsRequest | StockBarsRequest = CryptoBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame(1, TimeFrameUnit.Day),
                start=start,
                end=now,
            )
            raw = await asyncio.to_thread(self._data_client.get_crypto_bars, req)
        else:
            start = now - timedelta(days=n * 2)   # buffer for weekends/holidays
            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame(1, TimeFrameUnit.Day),
                start=start,
                end=now,
                feed=DataFeed.IEX,
            )
            raw = await asyncio.to_thread(self._data_client.get_stock_bars, req)
        bars = list(raw[symbol]) if symbol in raw else []
        return bars[-n:] if len(bars) > n else bars

    async def _run_eod_scan(self) -> None:
        log_info("donchian_eod_scan_start", symbols=self._symbols)
        self._queued_entries.clear()
        self._queued_exits.clear()
        needed = self._strategy.lookback + self._strategy.trend_ma + 20

        for symbol in self._symbols:
            try:
                bars = await self._fetch_daily_bars(symbol, n=needed)
                if not bars:
                    log_info("donchian_scan_no_bars", symbol=symbol)
                    continue

                result = self._strategy.scan(symbol, bars)
                log_info("donchian_scan_result", symbol=symbol,
                         action=result.action, stop=result.stop_price,
                         close=result.close_price)

                if result.action in ("enter_long", "enter_short"):
                    self._queued_entries[symbol] = result.action
                    await alerts.alert_signal(
                        symbol=symbol,
                        direction="BUY" if result.action == "enter_long" else "SELL",
                        price=str(round(result.close_price, 2)),
                        reason=(
                            f"Donchian {self._strategy.lookback}-day breakout — "
                            f"order at tomorrow's open | stop ${result.stop_price}"
                        ),
                    )

                elif result.action == "exit":
                    self._queued_exits.add(symbol)
                    await alerts.alert_signal(
                        symbol=symbol,
                        direction="FLAT",
                        price=str(round(result.close_price, 2)),
                        reason="Donchian channel exit — closing at tomorrow's open",
                    )

            except Exception as exc:
                log_error("donchian_scan_error", symbol=symbol, error=str(exc))

        log_info("donchian_eod_scan_done",
                 entries=list(self._queued_entries),
                 exits=list(self._queued_exits))

    # ── morning orders ────────────────────────────────────────────────────────

    async def _place_morning_orders(self) -> None:
        if not self._queued_entries and not self._queued_exits:
            return
        log_info("donchian_morning_orders",
                 entries=list(self._queued_entries),
                 exits=list(self._queued_exits))

        # Exits first to free up buying power
        for symbol in list(self._queued_exits):
            try:
                await self._broker.close_position(symbol)
                self._strategy.remove_position(symbol)
                log_info("donchian_exit_placed", symbol=symbol)
            except Exception as exc:
                log_error("donchian_exit_failed", symbol=symbol, error=str(exc))
        self._queued_exits.clear()

        # Entry orders
        entered: list[str] = []
        for symbol, action in list(self._queued_entries.items()):
            try:
                ok, reason = self._risk.check_new_order(symbol)
                if not ok:
                    log_info("donchian_entry_blocked", symbol=symbol, reason=reason)
                    self._strategy.remove_position(symbol)
                    continue

                bars = await self._fetch_daily_bars(symbol, n=5)
                if not bars:
                    continue
                price = Decimal(str(bars[-1].close))
                qty   = self._risk.compute_qty(price, fractional=self._is_crypto)
                if qty <= Decimal("0"):
                    log_info("donchian_entry_zero_qty", symbol=symbol)
                    continue

                side  = OrderSide.BUY if action == "enter_long" else OrderSide.SELL
                await self._broker.submit_market_order(symbol, qty, side)
                self._strategy.record_fill(symbol, float(qty))
                signed_qty = qty if side == OrderSide.BUY else -qty
                self._risk.record_fill(symbol, signed_qty, Decimal("0"))
                entered.append(symbol)

                log_info("donchian_entry_placed",
                         symbol=symbol, side=side.value, qty=str(qty))
                await alerts.alert_fill(
                    symbol=symbol,
                    side=side.value,
                    qty=str(qty),
                    price="market open",
                )

            except Exception as exc:
                log_error("donchian_entry_failed", symbol=symbol, error=str(exc))
        self._queued_entries.clear()
        await self._reanchor_stops(entered)

    async def _reanchor_stops(self, symbols: list[str]) -> None:
        """Re-anchor each entry's stop to its actual fill price (#4). Market
        orders fill ~immediately, so the live position's avg_entry_price is the
        true fill we anchor the stop distance off of."""
        if not symbols:
            return
        try:
            live = {str(p.symbol): p for p in await self._broker.get_all_positions()}
        except Exception as exc:
            log_error("donchian_reanchor_failed", error=str(exc))
            return
        for sym in symbols:
            p = live.get(sym)
            fill = float(getattr(p, "avg_entry_price", 0) or 0) if p is not None else 0.0
            if fill > 0:
                self._strategy.reanchor(sym, fill)
                log_info("donchian_stop_reanchored", symbol=sym, fill=fill)

    # ── intraday stop check ───────────────────────────────────────────────────

    async def _check_open_positions(self) -> None:
        tracked = self._strategy.open_positions
        if not tracked:
            return

        try:
            live_map = {str(p.symbol): p for p in await self._broker.get_all_positions()}
        except Exception as exc:
            log_error("donchian_position_check_failed", error=str(exc))
            return

        for symbol, pos in list(tracked.items()):
            live = live_map.get(symbol)
            if live is None:
                # Position disappeared (Alpaca stop triggered or manual close)
                log_info("donchian_position_gone", symbol=symbol)
                self._strategy.remove_position(symbol)
                continue

            current = float(live.current_price or 0)
            stop    = pos.stop_price

            hit = (
                pos.direction == "BUY"  and current <= stop
                or pos.direction == "SELL" and current >= stop
            )
            if hit:
                try:
                    await self._broker.close_position(symbol)
                    pnl = Decimal(str(live.unrealized_pl or "0"))
                    qty = abs(Decimal(str(live.qty or "0")))
                    self._risk.record_fill(
                        symbol=symbol,
                        qty=-qty if pos.direction == "BUY" else qty,
                        realised_pnl=pnl,
                    )
                    self._strategy.remove_position(symbol)
                    log_info("donchian_stop_hit", symbol=symbol,
                             stop=stop, price=current, pnl=str(pnl))
                    await alerts.alert_fill(
                        symbol=symbol,
                        side="SELL" if pos.direction == "BUY" else "BUY",
                        qty=str(qty),
                        price=str(round(current, 2)),
                    )
                except Exception as exc:
                    log_error("donchian_stop_close_failed",
                              symbol=symbol, error=str(exc))
