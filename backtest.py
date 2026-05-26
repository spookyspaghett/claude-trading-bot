from __future__ import annotations

import asyncio
import csv
import io
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


# ── Bar dataclass ─────────────────────────────────────────────────────────────

@dataclass
class BacktestBar:
    """Minimal bar shape accepted by ORBStrategy.on_bar().

    Attribute names match alpaca.data.models.Bar so the strategy code
    works with either without modification.
    """
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


# ── Result types ──────────────────────────────────────────────────────────────

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
    exit_reason: str = ""
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
    equity_curve: list[dict[str, object]]
    stats: BacktestStats
    strategy_used: str = "ORB (1-minute bars)"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_et(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(ET)


def _is_daily_data(bars: list[BacktestBar]) -> bool:
    """Return True when timestamps suggest daily (not intraday) bars."""
    if not bars:
        return False
    sample = bars[:min(30, len(bars))]
    # Daily bars from most sources have midnight (00:00:00) timestamps
    all_midnight = all(
        b.timestamp.hour == 0 and b.timestamp.minute == 0 and b.timestamp.second == 0
        for b in sample
    )
    if all_midnight:
        return True
    # Secondary check: very few bars per trading day → not 1-min data
    et_dates = [_to_et(b.timestamp).date() for b in bars]
    unique_days = len(set(et_dates))
    if unique_days > 0 and len(bars) / unique_days < 5:
        return True
    return False


# ── ORB simulation (intraday / 1-minute bars) ─────────────────────────────────

def _run_with_bars(
    symbol: str,
    bars: list[BacktestBar],
    start: date,
    end: date,
    orb_config: OrbConfig,
    risk_config: RiskConfig,
) -> BacktestResult:
    """Replay the ORB strategy over 1-minute BacktestBar objects."""

    days: dict[date, list[BacktestBar]] = {}
    for bar in bars:
        d = _to_et(bar.timestamp).date()
        days.setdefault(d, []).append(bar)

    trades: list[Trade] = []
    equity = Decimal("100000")
    equity_curve: list[dict[str, object]] = []

    for trading_day in sorted(days.keys()):
        day_bars = sorted(days[trading_day], key=lambda b: b.timestamp)

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
            bar_high  = Decimal(str(bar.high))
            bar_low   = Decimal(str(bar.low))
            bar_close = Decimal(str(bar.close))
            bar_et    = _to_et(bar.timestamp)

            if open_trade is not None:
                stopped = False
                if open_trade.direction == "BUY" and bar_low <= open_trade.stop_price:
                    exit_px = open_trade.stop_price
                    pnl     = (exit_px - open_trade.entry_price) * open_trade.qty
                    stopped = True
                elif open_trade.direction == "SELL" and bar_high >= open_trade.stop_price:
                    exit_px = open_trade.stop_price
                    pnl     = (open_trade.entry_price - exit_px) * open_trade.qty
                    stopped = True

                if stopped:
                    open_trade.exit_time   = bar_et
                    open_trade.exit_price  = exit_px   # type: ignore[possibly-undefined]
                    open_trade.exit_reason = "stop"
                    open_trade.pnl         = pnl       # type: ignore[possibly-undefined]
                    equity += pnl                       # type: ignore[possibly-undefined]
                    trades.append(open_trade)
                    risk.record_fill(symbol, Decimal("0"), pnl)   # type: ignore[possibly-undefined]
                    open_trade = None
                    continue

            sig = strategy.on_bar(bar)  # type: ignore[arg-type]
            if sig is None:
                continue

            if sig.direction == Direction.FLAT and open_trade is not None:
                if open_trade.direction == "BUY":
                    pnl = (bar_close - open_trade.entry_price) * open_trade.qty
                else:
                    pnl = (open_trade.entry_price - bar_close) * open_trade.qty
                open_trade.exit_time   = bar_et
                open_trade.exit_price  = bar_close
                open_trade.exit_reason = "eod"
                open_trade.pnl         = pnl
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

        if open_trade is not None and day_bars:
            last   = day_bars[-1]
            exit_px = Decimal(str(last.close))
            pnl = (
                (exit_px - open_trade.entry_price) * open_trade.qty
                if open_trade.direction == "BUY"
                else (open_trade.entry_price - exit_px) * open_trade.qty
            )
            open_trade.exit_time   = _to_et(last.timestamp)
            open_trade.exit_price  = exit_px
            open_trade.exit_reason = "eod_forced"
            open_trade.pnl         = pnl
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
        strategy_used="ORB (1-minute bars)",
    )


# ── N-day Donchian breakout (daily bars) ──────────────────────────────────────

def _run_with_daily_bars(
    symbol: str,
    bars: list[BacktestBar],
    start: date,
    end: date,
    risk_config: RiskConfig,
    lookback: int = 20,
    long_only: bool = False,
    trend_ma: int = 0,
) -> BacktestResult:
    """N-day Donchian channel breakout on daily OHLC bars.

    Entry  : close breaks above N-day high  → BUY
             close breaks below N-day low   → SELL short  (skipped if long_only=True)
    Trend  : when trend_ma > 0, only go long above the MA, only go short below it
    Exit   : stop loss (fixed %) hit on any bar's intrabar range
             OR close crosses the opposite channel level (reverse signal)
             OR end of data
    """
    warmup = max(lookback, trend_ma) if trend_ma > 0 else lookback
    if len(bars) <= warmup:
        raise ValueError(
            f"Need at least {warmup + 1} daily bars for these settings "
            f"(lookback={lookback}, trend_ma={trend_ma}). "
            f"Only {len(bars)} bars found — reduce the values or upload more data."
        )

    bars = sorted(bars, key=lambda b: b.timestamp)

    stop_factor = Decimal(str(risk_config.stop_loss_pct)) / Decimal("100")

    risk = RiskManager(
        max_position_usd=risk_config.max_position_usd,
        stop_loss_pct=risk_config.stop_loss_pct,
        daily_loss_limit_usd=risk_config.daily_loss_limit_usd,
        max_open_positions=risk_config.max_open_positions,
    )

    trades: list[Trade] = []
    equity = Decimal("100000")
    equity_curve: list[dict[str, object]] = []
    open_trade: Trade | None = None

    for i in range(warmup, len(bars)):
        bar    = bars[i]
        window = bars[i - lookback: i]

        ch_high = Decimal(str(max(b.high for b in window)))
        ch_low  = Decimal(str(min(b.low  for b in window)))

        # ── Trend filter: simple moving average ───────────────────────────────
        if trend_ma > 0:
            ma_window  = bars[i - trend_ma: i]
            sma        = sum(b.close for b in ma_window) / trend_ma
            trend_up   = bar.close > sma
            trend_down = bar.close < sma
        else:
            trend_up = trend_down = True          # no filter — both directions allowed

        bar_high  = Decimal(str(bar.high))
        bar_low   = Decimal(str(bar.low))
        bar_close = Decimal(str(bar.close))
        bar_et    = _to_et(bar.timestamp)
        bar_date  = bar_et.date()

        # ── 1. Check stop loss (uses intrabar high/low) ───────────────────────
        if open_trade is not None:
            stopped = False
            if open_trade.direction == "BUY" and bar_low <= open_trade.stop_price:
                exit_px = open_trade.stop_price
                pnl     = (exit_px - open_trade.entry_price) * open_trade.qty
                stopped = True
            elif open_trade.direction == "SELL" and bar_high >= open_trade.stop_price:
                exit_px = open_trade.stop_price
                pnl     = (open_trade.entry_price - exit_px) * open_trade.qty
                stopped = True

            if stopped:
                open_trade.exit_time   = bar_et
                open_trade.exit_price  = exit_px   # type: ignore[possibly-undefined]
                open_trade.exit_reason = "stop"
                open_trade.pnl         = pnl       # type: ignore[possibly-undefined]
                equity += pnl                       # type: ignore[possibly-undefined]
                trades.append(open_trade)
                close_qty = -open_trade.qty if open_trade.direction == "BUY" else open_trade.qty
                risk.record_fill(symbol, close_qty, pnl)   # type: ignore[possibly-undefined]
                open_trade = None

        # ── 2. Check reverse / channel exit ──────────────────────────────────
        if open_trade is not None:
            reverse = (
                open_trade.direction == "BUY"  and bar_close < ch_low
                or open_trade.direction == "SELL" and bar_close > ch_high
            )
            if reverse:
                pnl = (
                    (bar_close - open_trade.entry_price) * open_trade.qty
                    if open_trade.direction == "BUY"
                    else (open_trade.entry_price - bar_close) * open_trade.qty
                )
                open_trade.exit_time   = bar_et
                open_trade.exit_price  = bar_close
                open_trade.exit_reason = "channel"
                open_trade.pnl         = pnl
                equity += pnl
                trades.append(open_trade)
                close_qty = -open_trade.qty if open_trade.direction == "BUY" else open_trade.qty
                risk.record_fill(symbol, close_qty, pnl)
                open_trade = None

        # ── 3. Entry signal ───────────────────────────────────────────────────
        if open_trade is None:
            ok, _ = risk.check_new_order(symbol)
            if ok:
                if bar_close > ch_high and trend_up:
                    qty = risk.compute_qty(bar_close)
                    if qty > Decimal("0"):
                        stop = (bar_close * (Decimal("1") - stop_factor)).quantize(Decimal("0.01"))
                        open_trade = Trade(
                            symbol=symbol, direction="BUY",
                            entry_time=bar_et, entry_price=bar_close,
                            stop_price=stop, qty=qty,
                        )
                        risk.record_fill(symbol, qty, Decimal("0"))

                elif bar_close < ch_low and not long_only and trend_down:
                    qty = risk.compute_qty(bar_close)
                    if qty > Decimal("0"):
                        stop = (bar_close * (Decimal("1") + stop_factor)).quantize(Decimal("0.01"))
                        open_trade = Trade(
                            symbol=symbol, direction="SELL",
                            entry_time=bar_et, entry_price=bar_close,
                            stop_price=stop, qty=qty,
                        )
                        risk.record_fill(symbol, -qty, Decimal("0"))

        equity_curve.append({
            "timestamp": int(
                datetime(bar_date.year, bar_date.month, bar_date.day, tzinfo=ET).timestamp()
            ),
            "equity": float(equity),
        })

    # ── Force-close open position at end of data ──────────────────────────────
    if open_trade is not None:
        last    = bars[-1]
        exit_px = Decimal(str(last.close))
        pnl = (
            (exit_px - open_trade.entry_price) * open_trade.qty
            if open_trade.direction == "BUY"
            else (open_trade.entry_price - exit_px) * open_trade.qty
        )
        open_trade.exit_time   = _to_et(last.timestamp)
        open_trade.exit_price  = exit_px
        open_trade.exit_reason = "end"
        open_trade.pnl         = pnl
        equity += pnl
        trades.append(open_trade)

    return BacktestResult(
        symbol=symbol,
        start_date=str(start),
        end_date=str(end),
        trades=trades,
        equity_curve=equity_curve,
        stats=_compute_stats(trades, equity_curve),
        strategy_used=_daily_strategy_name(lookback, long_only, trend_ma),
    )


def _daily_strategy_name(lookback: int, long_only: bool, trend_ma: int) -> str:
    parts = [f"{lookback}-Day Donchian Breakout (daily"]
    if long_only:
        parts.append(", long only")
    if trend_ma > 0:
        parts.append(f", {trend_ma}-day MA filter")
    parts.append(")")
    return "".join(parts)


# ── Alpaca data fetch ─────────────────────────────────────────────────────────

def _run_sync(
    symbol: str,
    start: date,
    end: date,
    api_key: str,
    secret_key: str,
    orb_config: OrbConfig,
    risk_config: RiskConfig,
) -> BacktestResult:
    client  = StockHistoricalDataClient(api_key, secret_key)
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(1, TimeFrameUnit.Minute),
        start=datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
        end=datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc),
    )
    raw          = client.get_stock_bars(request)
    alpaca_bars  = list(raw[symbol]) if symbol in raw else []

    bars: list[BacktestBar] = [
        BacktestBar(
            symbol=symbol,
            timestamp=b.timestamp,
            open=float(b.open),
            high=float(b.high),
            low=float(b.low),
            close=float(b.close),
            volume=float(b.volume) if getattr(b, "volume", None) else 0.0,
        )
        for b in alpaca_bars
    ]

    return _run_with_bars(symbol, bars, start, end, orb_config, risk_config)


# ── File parsing ──────────────────────────────────────────────────────────────

def _find_col(headers: list[str], candidates: list[str]) -> int | None:
    """Find column index case-insensitively, stripping Stooq angle-bracket names."""
    cleaned = [h.lower().strip().strip("<>").strip() for h in headers]
    for c in candidates:
        try:
            return cleaned.index(c.lower())
        except ValueError:
            pass
    return None


def _parse_dt(s: str) -> datetime:
    """Try common date/time formats and Unix timestamps."""
    s = s.strip()
    fmts = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y%m%d %H%M%S",   # Stooq combined: "20240102 093000"
        "%Y%m%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y%m%d",
        "%m/%d/%Y",
    ]
    for fmt in fmts:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(int(s), tz=timezone.utc)
    except (ValueError, OSError):
        pass
    raise ValueError(f"Cannot parse datetime: {s!r}")


def _rows_to_bars(
    headers: list[str],
    rows: list[list[str]],
    symbol: str,
) -> list[BacktestBar]:
    dt_col   = _find_col(headers, ["datetime", "timestamp", "date_time"])
    date_col = _find_col(headers, ["date"])
    time_col = _find_col(headers, ["time"])
    high_col  = _find_col(headers, ["high", "h"])
    low_col   = _find_col(headers, ["low", "l"])
    close_col = _find_col(headers, ["close", "c", "last", "price"])
    open_col  = _find_col(headers, ["open", "o"])
    vol_col   = _find_col(headers, ["volume", "vol", "v"])

    if high_col is None or low_col is None or close_col is None:
        raise ValueError(
            f"Could not find High, Low, and Close columns.\n"
            f"Detected headers: {headers}\n"
            "Expected headers like: Date, Time, Open, High, Low, Close, Volume"
        )

    # TradingView exports a single 'time' column (Unix timestamp) with no 'date' column
    if dt_col is None and date_col is None and time_col is not None:
        dt_col   = time_col
        time_col = None

    bars: list[BacktestBar] = []
    for row in rows:
        if not any(cell.strip() for cell in row):
            continue
        try:
            if dt_col is not None:
                ts = _parse_dt(row[dt_col])
            elif date_col is not None and time_col is not None:
                d_str = row[date_col].strip()
                t_str = row[time_col].strip()
                if len(t_str) in (5, 6) and t_str.isdigit():
                    t_str = t_str.zfill(6)
                ts = _parse_dt(f"{d_str} {t_str}")
            elif date_col is not None:
                ts = _parse_dt(row[date_col])
            else:
                continue

            high  = float(row[high_col])
            low   = float(row[low_col])
            close = float(row[close_col])
            open_ = float(row[open_col]) if open_col is not None else close
            vol   = float(row[vol_col])  if vol_col  is not None else 0.0

            bars.append(BacktestBar(
                symbol=symbol, timestamp=ts,
                open=open_, high=high, low=low, close=close, volume=vol,
            ))
        except (ValueError, IndexError, TypeError):
            continue

    if not bars:
        raise ValueError(
            "No valid OHLC bars could be parsed from the file. "
            "Check that the file has Date/Time, High, Low, Close columns."
        )
    return bars


def _parse_csv_bytes(content: bytes, symbol: str) -> list[BacktestBar]:
    text   = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows   = [r for r in reader if r]
    if not rows:
        raise ValueError("CSV file is empty.")
    return _rows_to_bars(rows[0], rows[1:], symbol)


def _parse_excel_bytes(content: bytes, symbol: str) -> list[BacktestBar]:
    try:
        import openpyxl  # noqa: PLC0415
    except ImportError as exc:
        raise ValueError(
            "openpyxl is required to read Excel files. "
            "Re-run setup.sh to install updated dependencies."
        ) from exc

    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    ws = wb.active
    if ws is None:
        raise ValueError("Excel file has no active sheet.")
    rows_raw = list(ws.values)
    if not rows_raw:
        raise ValueError("Excel file is empty.")

    headers   = [str(c) if c is not None else "" for c in rows_raw[0]]
    data_rows = [[str(c) if c is not None else "" for c in row] for row in rows_raw[1:]]
    return _rows_to_bars(headers, data_rows, symbol)


def parse_bars_from_bytes(content: bytes, filename: str, symbol: str) -> list[BacktestBar]:
    """Parse a CSV or Excel upload into BacktestBar objects."""
    if filename.lower().endswith((".xlsx", ".xls")):
        return _parse_excel_bytes(content, symbol)
    return _parse_csv_bytes(content, symbol)


# ── Routing dispatcher ────────────────────────────────────────────────────────

def _run_sync_from_bars(
    symbol: str,
    bars: list[BacktestBar],
    orb_config: OrbConfig,
    risk_config: RiskConfig,
    lookback: int = 20,
    long_only: bool = False,
    trend_ma: int = 0,
) -> BacktestResult:
    if not bars:
        raise ValueError("No bars provided.")

    et_dates = [_to_et(b.timestamp).date() for b in bars]
    start, end = min(et_dates), max(et_dates)

    if _is_daily_data(bars):
        return _run_with_daily_bars(
            symbol, bars, start, end, risk_config,
            lookback=lookback, long_only=long_only, trend_ma=trend_ma,
        )
    else:
        return _run_with_bars(symbol, bars, start, end, orb_config, risk_config)


# ── Stats ─────────────────────────────────────────────────────────────────────

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

    total_pnl    = sum((t.pnl for t in trades), Decimal("0"))
    avg_win      = sum((t.pnl for t in wins),   Decimal("0")) / len(wins)   if wins   else Decimal("0")
    avg_loss     = abs(sum((t.pnl for t in losses), Decimal("0")) / len(losses)) if losses else Decimal("0")
    gross_profit = sum((t.pnl for t in wins),   Decimal("0"))
    gross_loss   = abs(sum((t.pnl for t in losses), Decimal("0")))
    profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    peak   = Decimal("0")
    max_dd = Decimal("0")
    for point in equity_curve:
        eq     = Decimal(str(point["equity"]))
        peak   = max(peak, eq)
        max_dd = max(max_dd, peak - eq)

    sharpe = 0.0
    if len(equity_curve) > 1:
        equities = [Decimal(str(p["equity"])) for p in equity_curve]
        rets = [
            (equities[i] - equities[i - 1]) / equities[i - 1]
            for i in range(1, len(equities))
            if equities[i - 1] != 0
        ]
        if len(rets) > 1:
            avg_r    = sum(rets) / len(rets)
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


# ── Async entry points ────────────────────────────────────────────────────────

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


async def run_backtest_from_file(
    symbol: str,
    bars: list[BacktestBar],
    orb_config: OrbConfig,
    risk_config: RiskConfig,
    lookback: int = 20,
    long_only: bool = False,
    trend_ma: int = 0,
) -> BacktestResult:
    return await asyncio.to_thread(
        _run_sync_from_bars, symbol, bars, orb_config, risk_config,
        lookback, long_only, trend_ma,
    )
