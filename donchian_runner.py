from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from decimal import Decimal
from pathlib import Path
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
# The EOD window stays open all evening on purpose. Alpaca's consolidated daily
# bar routinely isn't published for hours after the close, and the scan refuses
# to act on a stale bar — a 7-minute window meant the scan could fail every
# single day, so ran_eod_date never advanced and no overnight plan was ever
# produced. Retries inside the window back off (see _EOD_BACKOFF_AFTER).
_EOD_END     = dtime(20, 0)
_OPEN_START  = dtime(9, 31)
_OPEN_END    = dtime(9, 36)
_MKTCLOSE    = dtime(16, 0)

# Retry pacing inside the EOD window: fast at first (the bar is usually there
# within minutes), then slow so a holiday/outage doesn't hammer the data API.
_EOD_BACKOFF_AFTER = 10
_EOD_RETRY_FAST    = 60
_EOD_RETRY_SLOW    = 600

# Proof-of-life cadence. A Donchian bot can legitimately log nothing for hours,
# which made the dashboard feed look dead on a perfectly healthy bot.
_HEARTBEAT_SECONDS = 1800

# Crypto daily scan window (UTC) — bars roll at 00:00 UTC.
_CRYPTO_SCAN_START = dtime(0, 5)
_CRYPTO_SCAN_END   = dtime(0, 20)

# Where the runner persists its action queue + per-day debounce so the overnight
# handoff survives a service restart. Separate from the strategy's position state.
HANDOFF_PATH = Path("memory/donchian_handoff.json")
# Queued actions older than this many calendar days are expired rather than fired
# (covers a Fri-evening scan → Mon open long weekend; drops anything staler).
_MAX_QUEUE_AGE_DAYS = 3


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
        slug: str | None = None,
    ) -> None:
        self._symbols      = symbols
        self._broker       = broker
        self._risk         = risk
        self._strategy     = strategy
        self._is_crypto    = asset_class == "crypto"
        # Per-profile handoff file so multiple Donchian bots can run at once
        # without clobbering each other's queue. Slug-less falls back to the
        # shared default (and lets tests monkeypatch HANDOFF_PATH).
        self._handoff_path = (
            HANDOFF_PATH.with_name(f"donchian_handoff_{slug}.json") if slug
            else HANDOFF_PATH
        )
        self._data_client: CryptoHistoricalDataClient | StockHistoricalDataClient = (
            CryptoHistoricalDataClient(api_key, secret_key)
            if self._is_crypto
            else StockHistoricalDataClient(api_key, secret_key)
        )
        # Queued from EOD scan, executed at next open. Loaded from disk so the
        # handoff survives a restart between the scan and the next open.
        self._queued_entries: dict[str, str] = {}   # symbol → "enter_long"|"enter_short"
        self._queued_exits: set[str]   = set()
        self._queued_date: str = ""                 # ET date the queue was produced
        # Entries placed but whose stop isn't yet anchored to the real fill (#3).
        # Persisted so a restart still re-anchors once the fill is visible.
        self._pending_reanchor: set[str] = set()
        # Debounce: which time-windows we already ran today (persisted).
        self._ran_eod_date:   str = ""
        self._ran_open_date:  str = ""
        # Retry pacing + proof-of-life (in-memory only; a restart just resets them).
        self._eod_attempts: int = 0
        self._eod_attempt_date: str = ""
        self._last_heartbeat: datetime | None = None
        self._load_handoff()

    # ── handoff persistence (restart-safe queue + debounce) ────────────────────

    def _load_handoff(self) -> None:
        if not self._handoff_path.exists():
            return
        try:
            d = json.loads(self._handoff_path.read_text(encoding="utf-8"))
            self._queued_entries = dict(d.get("queued_entries", {}))
            self._queued_exits = set(d.get("queued_exits", []))
            self._queued_date = d.get("queued_date", "")
            self._pending_reanchor = set(d.get("pending_reanchor", []))
            self._ran_eod_date = d.get("ran_eod_date", "")
            self._ran_open_date = d.get("ran_open_date", "")
        except Exception as exc:
            log_error("donchian_handoff_load_failed", error=str(exc))

    def _save_handoff(self) -> None:
        try:
            self._handoff_path.parent.mkdir(parents=True, exist_ok=True)
            self._handoff_path.write_text(json.dumps({
                "queued_entries": self._queued_entries,
                "queued_exits": sorted(self._queued_exits),
                "queued_date": self._queued_date,
                "pending_reanchor": sorted(self._pending_reanchor),
                "ran_eod_date": self._ran_eod_date,
                "ran_open_date": self._ran_open_date,
            }, indent=2), encoding="utf-8")
        except Exception as exc:
            log_error("donchian_handoff_save_failed", error=str(exc))

    def _expire_stale_queue(self) -> None:
        """Drop a queue whose intended open has already passed so a late restart
        never fires day-old market orders. Two triggers:
          • multi-day age cap (covers week-long downtime), and
          • a missed morning window: it's a weekday past 09:36 ET on a day after
            the scan and we never ran that morning (restart after the open).
        Unfilled entries are removed; exits keep their (filled) position tracked —
        its stop still protects it and the next EOD scan re-queues the exit."""
        if not self._queued_date:
            return
        try:
            qd = date.fromisoformat(self._queued_date)
        except ValueError:
            return
        now = datetime.now(tz=ET)
        age = (now.date() - qd).days
        missed_window = (
            not self._is_crypto
            and now.date() > qd
            and now.time() > _OPEN_END
            and now.weekday() < 5                       # Mon–Fri (weekend keeps Fri→Mon)
            and self._ran_open_date != str(now.date())
        )
        if age <= _MAX_QUEUE_AGE_DAYS and not missed_window:
            return
        log_error("donchian_queue_expired", queued_date=self._queued_date, age_days=age,
                  missed_window=missed_window,
                  entries=list(self._queued_entries), exits=list(self._queued_exits))
        for sym in list(self._queued_entries):
            self._strategy.remove_position(sym)   # never filled → drop
            self._pending_reanchor.discard(sym)
        self._queued_entries.clear()
        self._queued_exits.clear()
        self._queued_date = ""
        self._save_handoff()

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
        self._expire_stale_queue()
        await self._reconcile_with_broker()

        if self._is_crypto:
            await self._run_crypto(shutdown_event)
            return

        while not shutdown_event.is_set():
            now      = datetime.now(tz=ET)
            t        = now.time()
            today_s  = str(now.date())
            weekday  = now.weekday() < 5

            self._heartbeat(now)
            # Expire on every pass, not just at startup: a bot that runs for
            # weeks without a restart would otherwise never re-evaluate a queue
            # whose open it missed, and could fire a days-old plan.
            self._expire_stale_queue()

            # Evening EOD scan on trading weekdays (once per day). The weekday
            # guard matters: a weekend scan can only ever see Friday's bar, and
            # running it used to wipe the queue Friday's scan had produced.
            in_eod_window = _EOD_START <= t <= _EOD_END
            if weekday and in_eod_window and self._ran_eod_date != today_s:
                if self._eod_attempt_date != today_s:
                    self._eod_attempt_date = today_s
                    self._eod_attempts = 0
                self._eod_attempts += 1
                await self._run_eod_scan(today_s)
                await asyncio.sleep(
                    _EOD_RETRY_FAST if self._eod_attempts <= _EOD_BACKOFF_AFTER
                    else _EOD_RETRY_SLOW
                )
                continue

            # 09:31–09:36 → place morning orders (once per day)
            in_open_window = _OPEN_START <= t <= _OPEN_END
            if weekday and in_open_window and self._ran_open_date != today_s:
                await self._place_morning_orders(today_s)
                await asyncio.sleep(60)
                continue

            # During market hours → check positions every 60 s
            if weekday and _OPEN_START <= t < _MKTCLOSE:
                await self._check_open_positions()
                await asyncio.sleep(60)
                continue

            await asyncio.sleep(30)

    def _heartbeat(self, now: datetime) -> None:
        """Emit a periodic proof-of-life line so the dashboard feed shows the
        bot is alive on days where it has nothing else to say."""
        if (self._last_heartbeat is not None
                and (now - self._last_heartbeat).total_seconds() < _HEARTBEAT_SECONDS):
            return
        self._last_heartbeat = now
        log_info("heartbeat",
                 strategy="donchian",
                 tracked=len(self._strategy.open_positions),
                 queued_entries=len(self._queued_entries),
                 queued_exits=len(self._queued_exits),
                 last_scan=self._ran_eod_date or "never",
                 last_open=self._ran_open_date or "never")

    async def _run_crypto(self, shutdown_event: asyncio.Event) -> None:
        """24/7 loop: scan once daily at ~00:05 UTC, enter immediately at market,
        and check stops every 60 s around the clock."""
        while not shutdown_event.is_set():
            now     = datetime.now(tz=timezone.utc)
            t       = now.time()
            today_s = str(now.date())

            self._heartbeat(now)

            if _CRYPTO_SCAN_START <= t <= _CRYPTO_SCAN_END and self._ran_eod_date != today_s:
                await self._run_eod_scan(today_s)
                # Crypto trades continuously — no "next open" wait, enter now.
                await self._place_morning_orders(today_s)
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

    @staticmethod
    def _bar_et_date(bar: object) -> date | None:
        ts = getattr(bar, "timestamp", None)
        if ts is None:
            return None
        try:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts.astimezone(ET).date()
        except Exception:
            return None

    async def _run_eod_scan(self, ran_date: str) -> None:
        log_info("donchian_eod_scan_start", symbols=self._symbols)
        needed = self._strategy.lookback + self._strategy.trend_ma + 20
        try:
            expected = date.fromisoformat(ran_date)
        except ValueError:
            expected = datetime.now(tz=ET).date()
        fresh_count = 0

        # Accumulate into locals and only commit once the scan is known good.
        # Clearing the live queue up-front destroyed the pending overnight plan
        # whenever the scan then aborted (stale data, data outage, a weekend
        # window firing) — the next morning found an empty queue, marked the day
        # done and persisted the emptiness, silently dropping the trades.
        new_entries: dict[str, str] = {}
        new_exits: set[str] = set()

        for symbol in self._symbols:
            try:
                bars = await self._fetch_daily_bars(symbol, n=needed)
                if not bars:
                    log_info("donchian_scan_no_bars", symbol=symbol)
                    continue

                # #5: never act on a stale daily bar (data-provider lag or a
                # non-session day). Skip the symbol so we don't trade on yesterday.
                if not self._is_crypto:
                    bar_date = self._bar_et_date(bars[-1])
                    if bar_date is None or bar_date < expected:
                        log_error("donchian_scan_stale_bar", symbol=symbol,
                                  bar_date=str(bar_date), expected=str(expected))
                        continue
                fresh_count += 1

                result = self._strategy.scan(symbol, bars)
                log_info("donchian_scan_result", symbol=symbol,
                         action=result.action, stop=result.stop_price,
                         close=result.close_price)

                if result.action in ("enter_long", "enter_short"):
                    new_entries[symbol] = result.action
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
                    new_exits.add(symbol)
                    await alerts.alert_signal(
                        symbol=symbol,
                        direction="FLAT",
                        price=str(round(result.close_price, 2)),
                        reason="Donchian channel exit — closing at tomorrow's open",
                    )

            except Exception as exc:
                log_error("donchian_scan_error", symbol=symbol, error=str(exc))

        # Total data outage (stocks): not one symbol had today's bar. Don't mark
        # the day done so the EOD window retries rather than skipping the whole
        # session (#5), and leave any existing queue intact — no symbol was
        # scanned, so we learned nothing that should invalidate last night's plan.
        if not self._is_crypto and self._symbols and fresh_count == 0:
            log_error("donchian_eod_scan_stale_all", expected=str(expected),
                      kept_entries=list(self._queued_entries),
                      kept_exits=list(self._queued_exits))
            return

        # Persist the queue + debounce so the morning handoff survives a restart.
        self._queued_entries = new_entries
        self._queued_exits = new_exits
        self._queued_date = ran_date
        self._ran_eod_date = ran_date
        self._save_handoff()
        log_info("donchian_eod_scan_done",
                 entries=list(self._queued_entries),
                 exits=list(self._queued_exits))

    # ── morning orders ────────────────────────────────────────────────────────

    async def _place_morning_orders(self, ran_date: str) -> None:
        if not self._queued_entries and not self._queued_exits:
            self._ran_open_date = ran_date
            self._save_handoff()
            return
        log_info("donchian_morning_orders",
                 entries=list(self._queued_entries),
                 exits=list(self._queued_exits))

        # Exits first to free up buying power. Only stop tracking a position once
        # its close has actually filled (#1) — close_position is a market order.
        for symbol in list(self._queued_exits):
            try:
                await self._broker.close_position(symbol)
                self._strategy.remove_position(symbol)
                self._queued_exits.discard(symbol)
                self._save_handoff()
                log_info("donchian_exit_placed", symbol=symbol)
            except Exception as exc:
                log_error("donchian_exit_failed", symbol=symbol, error=str(exc))

        # Entry orders
        entered: list[str] = []
        for symbol, action in list(self._queued_entries.items()):
            try:
                ok, reason = self._risk.check_new_order(symbol)
                if not ok:
                    log_info("donchian_entry_blocked", symbol=symbol, reason=reason)
                    self._strategy.remove_position(symbol)
                    self._queued_entries.pop(symbol, None)
                    self._save_handoff()
                    continue

                bars = await self._fetch_daily_bars(symbol, n=5)
                if not bars:
                    continue
                price = Decimal(str(bars[-1].close))
                qty   = self._risk.compute_qty(price, fractional=self._is_crypto)
                if qty <= Decimal("0"):
                    log_info("donchian_entry_zero_qty", symbol=symbol)
                    self._strategy.remove_position(symbol)
                    self._queued_entries.pop(symbol, None)
                    self._save_handoff()
                    continue

                side  = OrderSide.BUY if action == "enter_long" else OrderSide.SELL
                await self._broker.submit_market_order(symbol, qty, side)
                self._strategy.record_fill(symbol, float(qty))
                signed_qty = qty if side == OrderSide.BUY else -qty
                self._risk.record_fill(symbol, signed_qty, Decimal("0"))
                entered.append(symbol)
                # Dequeue this entry as soon as it's placed so a mid-loop restart
                # can't re-fire it; flag it for stop re-anchoring once the fill
                # price is visible (#3).
                self._queued_entries.pop(symbol, None)
                self._pending_reanchor.add(symbol)
                self._save_handoff()

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
        # Morning window complete: mark debounce and clear the day's queue.
        self._queued_entries.clear()
        self._queued_exits.clear()
        self._queued_date = ""
        self._ran_open_date = ran_date
        self._save_handoff()
        await self._reanchor_stops()

    async def _reanchor_stops(self) -> None:
        """Re-anchor pending entries' stops to their actual fill price (#3/#4).
        A market order may not be visible at the broker the instant after submit,
        so symbols stay in ``_pending_reanchor`` (persisted) and are retried on
        every position check until the fill price appears."""
        if not self._pending_reanchor:
            return
        try:
            live = {str(p.symbol): p for p in await self._broker.get_all_positions()}
        except Exception as exc:
            log_error("donchian_reanchor_failed", error=str(exc))
            return
        self._reanchor_from_map(live)

    def _reanchor_from_map(self, live_map: dict) -> None:
        if not self._pending_reanchor:
            return
        for sym in list(self._pending_reanchor):
            p = live_map.get(sym)
            fill = float(getattr(p, "avg_entry_price", 0) or 0) if p is not None else 0.0
            if fill > 0:
                self._strategy.reanchor(sym, fill)
                self._pending_reanchor.discard(sym)
                log_info("donchian_stop_reanchored", symbol=sym, fill=fill)
        self._save_handoff()

    # ── startup reconciliation (state vs broker) ───────────────────────────────

    async def _reconcile_with_broker(self) -> None:
        """Reconcile persisted state against the broker on startup (#2) so no
        live position goes unmonitored and no phantom lingers in state:
          • state has it, broker doesn't → drop it (closed/never opened while
            down). Unfilled queued entries (qty==0) are legitimately broker-absent
            and skipped.
          • broker has it, state doesn't → adopt it with a sane ATR stop so it's
            protected; loud log + alert either way."""
        try:
            live_list = await self._broker.get_all_positions()
        except Exception as exc:
            log_error("donchian_reconcile_failed", error=str(exc))
            return
        live_map = {str(p.symbol): p for p in live_list}

        # 1) Tracked but not held at broker.
        for sym, pos in list(self._strategy.open_positions.items()):
            if sym in live_map:
                continue
            if pos.qty == 0.0 and not pos.pending_exit:
                continue   # queued entry not yet placed — expected to be absent
            reason = "exit_already_filled" if pos.pending_exit else "position_vanished"
            log_error("donchian_reconcile_drop", symbol=sym, reason=reason,
                      qty=pos.qty, stop=pos.stop_price)
            await alerts.alert_error(
                "donchian_reconcile_drop",
                f"{sym} tracked but not held at broker ({reason}) — dropping from state.")
            self._strategy.remove_position(sym)
            self._queued_exits.discard(sym)
            self._queued_entries.pop(sym, None)
            self._pending_reanchor.discard(sym)

        # 2) Held at broker but untracked → adopt so it's monitored.
        for sym, live in live_map.items():
            if sym in self._strategy.open_positions:
                continue
            await self._adopt_broker_position(sym, live)

        self._save_handoff()

    async def _adopt_broker_position(self, symbol: str, live: object) -> None:
        qty   = float(getattr(live, "qty", 0) or 0)
        entry = float(getattr(live, "avg_entry_price", 0) or 0)
        if entry <= 0:
            entry = float(getattr(live, "current_price", 0) or 0)
        if entry <= 0 or qty == 0:
            log_error("donchian_reconcile_adopt_skipped", symbol=symbol,
                      entry=entry, qty=qty)
            return
        direction = "BUY" if qty > 0 else "SELL"
        dist = await self._estimate_stop_dist(symbol, entry)
        stop = round(entry - dist, 2) if direction == "BUY" else round(entry + dist, 2)
        self._strategy.adopt_position(symbol=symbol, direction=direction,
                                      entry_price=entry, stop_price=stop, qty=abs(qty))
        log_error("donchian_reconcile_adopt", symbol=symbol, direction=direction,
                  entry=entry, stop=stop, qty=abs(qty))
        await alerts.alert_error(
            "donchian_reconcile_adopt",
            f"adopted untracked broker position {symbol} {direction} "
            f"qty={abs(qty)} — stop set @ {stop}.")

    async def _estimate_stop_dist(self, symbol: str, entry: float) -> float:
        """ATR-based stop distance for an adopted position, mirroring the scan's
        1.5×ATR. Falls back to an 8% stop if bars are unavailable."""
        try:
            bars = await self._fetch_daily_bars(symbol, n=20)
            trs = []
            for j in range(1, min(15, len(bars))):
                b, prev = bars[-j], bars[-j - 1]
                trs.append(max(
                    float(b.high) - float(b.low),
                    abs(float(b.high) - float(prev.close)),
                    abs(float(b.low)  - float(prev.close)),
                ))
            if trs:
                return round(sum(trs) / len(trs) * 1.5, 2)
        except Exception as exc:
            log_error("donchian_reconcile_atr_failed", symbol=symbol, error=str(exc))
        return round(entry * 0.08, 2)

    # ── intraday stop check ───────────────────────────────────────────────────

    async def _check_open_positions(self) -> None:
        tracked = self._strategy.open_positions
        if not tracked and not self._pending_reanchor:
            return

        try:
            live_map = {str(p.symbol): p for p in await self._broker.get_all_positions()}
        except Exception as exc:
            log_error("donchian_position_check_failed", error=str(exc))
            return

        # Catch any entry whose fill wasn't yet visible at order time (#3).
        self._reanchor_from_map(live_map)

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
