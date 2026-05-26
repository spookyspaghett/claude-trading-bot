from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from risk import RiskManager
from strategy import Direction, ORBStrategy

if TYPE_CHECKING:
    from config_loader import OrbConfig, RiskConfig

ET = ZoneInfo("America/New_York")


@dataclass
class Trade:
    symbol: str
    direction: str          # "BUY" or "SELL"
    entry_time: datetime
    entry_price: Decimal
    stop_price: Decimal
    qty: Decimal
    exit_time: datetime | None = None
    exit_price: Decimal | None = None
    exit_reason: str = ""   # "stop" | "eod" | "eod_forced"
    pnl: Decimal = field(default_factory=lambda: Decimal("0"))


@dataclass
class BacktestStats:
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_win: Decimal
    avg_loss: Decimal
    profit_factor: float
    total_pnl: Decimal
    max_drawdown: Decimal
    sharpe_ratio: float


@dataclass
class BacktestResult:
    symbol: str
    start_date: str
    end_date: str
    trades: list[Trade]
    equity_curve: list[dict[str, object]]   # [{timestamp, equity}]
    stats: BacktestStats


# ── Core sync engine ──────────────────────────────────────────────────────────

def _to_et(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(ET)


def _run_sync(
    symbol: str,
    start: date,
    end: date,
    api_key: str,
    secret_key: str,
    orb_config: OrbConfig,
    risk_config: RiskConfig,
) -> BacktestResult:
    # ── Fetch 1-minute bars ───────────────────────────────────────────────────
    client = StockHistoricalDataClient(api_key, secret_key)
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(1, TimeFrameUnit.Minute),
        start=datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
        end=datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc),
    )
    raw = client.get_stock_bars(request)
    all_bars = list(raw[symbol]) if symbol in raw else []

    # ── Group by ET trading day ───────────────────────────────────────────────
    days: dict[date, list[object]] = {}
    for bar in all_bars:
        d = _to_et(bar.timestamp).date()  # type: ignore[union-attr]
        days.setdefault(d, []).append(bar)

    trades: list[Trade] = []
    equity = Decimal("100000")
    equity_curve: list[dict[str, object]] = []

    for trading_day in sorted(days.keys()):
        day_bars = sorted(days[trading_day], key=lambda b: b.timestamp)  # type: ignore[attr-defined]

        strategy = ORBStrategy(
            config=orb_config,
            symbols=[symbol],
            stop_loss_pct=risk_config.stop_loss_pct,
        )
        risk = RiskManager(
            max_position_usd=risk_config.max_position_usd,
            stop_loss_pct=risk_config.stop_loss_pct,
            daily_loss_limit_usd=risk_config.daily_loss_limit_usd,
            max_open_positions=risk_config.max_open_positions,
        )

        open_trade: Trade | None = None

        for bar in day_bars:
            bar_high = Decimal(str(bar.high))  # type: ignore[attr-defined]
            bar_low = Decimal(str(bar.low))   # type: ignore[attr-defined]
            bar_close = Decimal(str(bar.close))  # type: ignore[attr-defined]
            bar_et = _to_et(bar.timestamp)   # type: ignore[union-attr]

            # ── Check stop loss on open position ──────────────────────────────
            if open_trade is not None:
                stopped = False
                if open_trade.direction == "BUY" and bar_low <= open_trade.stop_price:
                    exit_px = open_trade.stop_price
                    pnl = (exit_px - open_trade.entry_price) * open_trade.qty
                    stopped = True
                elif open_trade.direction == "SELL" and bar_high >= open_trade.stop_price:
                    exit_px = open_trade.stop_price
                    pnl = (open_trade.entry_price - exit_px) * open_trade.qty
                    stopped = True

                if stopped:
                    open_trade.exit_time = bar_et
                    open_trade.exit_price = exit_px  # type: ignore[possibly-undefined]
                    open_trade.exit_reason = "stop"
                    open_trade.pnl = pnl  # type: ignore[possibly-undefined]
                    equity += pnl  # type: ignore[possibly-undefined]
                    trades.append(open_trade)
                    risk.record_fill(symbol, Decimal("0"), pnl)  # type: ignore[possibly-undefined]
                    open_trade = None
                    continue

            # ── Feed bar to strategy ──────────────────────────────────────────
            sig = strategy.on_bar(bar)  # type: ignore[arg-type]
            if sig is None:
                continue

            if sig.direction == Direction.FLAT and open_trade is not None:
                if open_trade.direction == "BUY":
                    pnl = (bar_close - open_trade.entry_price) * open_trade.qty
                else:
                    pnl = (open_trade.entry_price - bar_close) * open_trade.qty
                open_trade.exit_time = bar_et
                open_trade.exit_price = bar_close
                open_trade.exit_reason = "eod"
                open_trade.pnl = pnl
                equity += pnl
                trades.append(open_trade)
                open_trade = None

            elif sig.direction in (Direction.BUY, Direction.SELL) and open_trade is None:
                ok, _ = risk.check_new_order(symbol)
                if not ok:
                    continue
                qty = risk.compute_qty(sig.entry_price)
                if qty <= Decimal("0"):
                    continue
                open_trade = Trade(
                    symbol=symbol,
                    direction=sig.direction.value,
                    entry_time=bar_et,
                    entry_price=sig.entry_price,
                    stop_price=sig.stop_price,
                    qty=qty,
                )
                risk.record_fill(symbol, qty, Decimal("0"))

        # ── Force-close anything still open at end of day ─────────────────────
        if open_trade is not None and day_bars:
            last = day_bars[-1]
            exit_px = Decimal(str(last.close))  # type: ignore[attr-defined]
            if open_trade.direction == "BUY":
                pnl = (exit_px - open_trade.entry_price) * open_trade.qty
            else:
                pnl = (open_trade.entry_price - exit_px) * open_trade.qty
            open_trade.exit_time = _to_et(last.timestamp)   # type: ignore[union-attr]
            open_trade.exit_price = exit_px
            open_trade.exit_reason = "eod_forced"
            open_trade.pnl = pnl
            equity += pnl
            trades.append(open_trade)

        equity_curve.append({
            "timestamp": int(
                datetime(trading_day.year, trading_day.month, trading_day.day, tzinfo=ET).timestamp()
            ),
            "equity": float(equity),
        })

    return BacktestResult(
        symbol=symbol,
        start_date=str(start),
        end_date=str(end),
        trades=trades,
        equity_curve=equity_curve,
        stats=_compute_stats(trades, equity_curve),
    )


def _compute_stats(
    trades: list[Trade],
    equity_curve: list[dict[str, object]],
) -> BacktestStats:
    if not trades:
        return BacktestStats(
            total_trades=0, winning_trades=0, losing_trades=0,
            win_rate=0.0, avg_win=Decimal("0"), avg_loss=Decimal("0"),
            profit_factor=0.0, total_pnl=Decimal("0"),
            max_drawdown=Decimal("0"), sharpe_ratio=0.0,
        )

    wins   = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]

    total_pnl   = sum((t.pnl for t in trades), Decimal("0"))
    avg_win     = sum((t.pnl for t in wins), Decimal("0")) / len(wins) if wins else Decimal("0")
    avg_loss    = abs(sum((t.pnl for t in losses), Decimal("0")) / len(losses)) if losses else Decimal("0")
    gross_profit = sum((t.pnl for t in wins), Decimal("0"))
    gross_loss   = abs(sum((t.pnl for t in losses), Decimal("0")))
    profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    # Max drawdown from equity curve
    peak = Decimal("0")
    max_dd = Decimal("0")
    for point in equity_curve:
        eq = Decimal(str(point["equity"]))
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)

    # Annualised Sharpe from daily equity returns (sqrt(252) factor)
    sharpe = 0.0
    if len(equity_curve) > 1:
        equities = [Decimal(str(p["equity"])) for p in equity_curve]
        rets = [
            (equities[i] - equities[i - 1]) / equities[i - 1]
            for i in range(1, len(equities))
            if equities[i - 1] != 0
        ]
        if len(rets) > 1:
            avg_r = sum(rets) / len(rets)
            variance = sum((r - avg_r) ** 2 for r in rets) / len(rets)
            try:
                std = variance.sqrt()
                if std > 0:
                    sharpe = float(avg_r / std * Decimal("15.87"))  # ≈ sqrt(252)
            except InvalidOperation:
                pass

    return BacktestStats(
        total_trades=len(trades),
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate=len(wins) / len(trades),
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        total_pnl=total_pnl,
        max_drawdown=max_dd,
        sharpe_ratio=sharpe,
    )


# ── Async entry point ─────────────────────────────────────────────────────────

async def run_backtest(
    symbol: str,
    start: date,
    end: date,
    api_key: str,
    secret_key: str,
    orb_config: OrbConfig,
    risk_config: RiskConfig,
) -> BacktestResult:
    return await asyncio.to_thread(
        _run_sync, symbol, start, end, api_key, secret_key, orb_config, risk_config,
    )
