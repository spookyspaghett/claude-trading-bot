from __future__ import annotations

import asyncio
import signal
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from alpaca.data.models import Bar

import alerts
from broker import BrokerClient, LiveTradingNotConfirmedError
from data import DataFeed
from executor import OrderExecutor
from logger import log_error, log_info, setup_logging
from research import PremarketResearch
from risk import RiskManager
from strategy import EMAStrategy, ORBStrategy, Strategy, TrendSRStrategy

ET = ZoneInfo("America/New_York")
MARKET_OPEN: time = time(9, 30)
MARKET_CLOSE: time = time(16, 0)

_ORDER_POLL_INTERVAL = 100    # poll orders every ~10 s
_POSITION_POLL_INTERVAL = 600 # check positions for loser cut every ~60 s


def _is_market_hours(now: datetime) -> bool:
    t = now.astimezone(ET).time()
    return MARKET_OPEN <= t < MARKET_CLOSE


def _build_strategy(config: object) -> tuple[Strategy, str]:
    """Return (strategy, entry_order_type) based on config."""
    from config_loader import Config  # local import to avoid circular
    assert isinstance(config, Config)
    name = config.strategy.name
    is_crypto = config.asset_class == "crypto"
    if name == "trend_sr":
        strat = TrendSRStrategy(
            config=config.strategy.trend_sr,
            symbols=config.symbols,
            trade_24_7=is_crypto,
        )
        order_type = "market"
        log_info(
            "strategy_selected",
            strategy="trend_sr",
            ma_fast=config.strategy.trend_sr.ma_fast,
            ma_slow=config.strategy.trend_sr.ma_slow,
        )
    elif name == "ema":
        strat = EMAStrategy(
            config=config.strategy.ema,
            symbols=config.symbols,
            stop_loss_pct=config.risk.stop_loss_pct,
            trade_24_7=is_crypto,
        )
        order_type = config.strategy.ema.entry_order_type
        log_info(
            "strategy_selected",
            strategy="ema",
            fast=config.strategy.ema.fast_period,
            slow=config.strategy.ema.slow_period,
        )
    else:
        strat = ORBStrategy(
            config=config.strategy.orb,
            symbols=config.symbols,
            stop_loss_pct=config.risk.stop_loss_pct,
        )
        order_type = config.strategy.orb.entry_order_type
        log_info(
            "strategy_selected",
            strategy="orb",
            opening_range_minutes=config.strategy.orb.opening_range_minutes,
        )
    return strat, order_type


async def _run_donchian(config: object, kill_path: Path = Path("KILL")) -> None:
    """Separate run loop for the daily Donchian strategy."""
    from config_loader import Config
    from donchian_runner import DonchianRunner
    from donchian_strategy import DonchianLiveStrategy
    assert isinstance(config, Config)

    dc = config.strategy.donchian
    log_info("strategy_selected", strategy="donchian",
             lookback=dc.lookback_days, trend_ma=dc.trend_ma,
             long_only=dc.long_only)

    try:
        broker = BrokerClient(config)
    except LiveTradingNotConfirmedError as exc:
        print(str(exc), file=sys.stderr)
        return

    risk = RiskManager(
        max_position_usd=config.risk.max_position_usd,
        stop_loss_pct=config.risk.stop_loss_pct,
        daily_loss_limit_usd=config.risk.daily_loss_limit_usd,
        max_open_positions=config.risk.max_open_positions,
        kill_switch_path=kill_path,
    )
    strategy = DonchianLiveStrategy(
        lookback_days=dc.lookback_days,
        trend_ma=dc.trend_ma,
        trailing_activation_pct=dc.trailing_activation_pct,
        trailing_pct=dc.trailing_pct,
        long_only=dc.long_only,
    )
    runner = DonchianRunner(
        symbols=config.symbols,
        broker=broker,
        risk=risk,
        strategy=strategy,
        api_key=config.alpaca_api_key,
        secret_key=config.alpaca_secret_key,
        asset_class=config.asset_class,
    )

    shutdown_event = asyncio.Event()

    def _handle_sigint(sig: int, frame: object) -> None:
        log_info("shutdown_signal_received", sig=sig)
        shutdown_event.set()

    signal.signal(signal.SIGINT, _handle_sigint)
    await runner.run(shutdown_event)
    log_info("donchian_shutdown_complete")


async def _warm_up_crypto(strategy: Strategy, config: object) -> None:
    """Seed the strategy's indicators from recent crypto history so it can trade
    immediately on startup instead of waiting ~warmup_bars live candles."""
    from alpaca.data.historical import CryptoHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    from config_loader import Config
    assert isinstance(config, Config)

    is_trend_sr = config.strategy.name == "trend_sr"
    tf_min = config.strategy.trend_sr.bar_minutes if is_trend_sr else 1
    need = int(getattr(strategy, "warmup_bars", 200)) + 30
    if tf_min % 60 == 0:
        timeframe = TimeFrame(tf_min // 60, TimeFrameUnit.Hour)
    else:
        timeframe = TimeFrame(tf_min, TimeFrameUnit.Minute)

    client = CryptoHistoricalDataClient(config.alpaca_api_key, config.alpaca_secret_key)
    now = datetime.now(tz=ZoneInfo("UTC"))
    start = now - timedelta(minutes=tf_min * need * 2 + 1440)

    for symbol in config.symbols:
        try:
            req = CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=timeframe,
                                    start=start, end=now)
            raw = await asyncio.to_thread(client.get_crypto_bars, req)
            data = getattr(raw, "data", None)
            if isinstance(data, dict):
                bars = list(data.get(symbol, []))
            else:
                bars = list(raw[symbol]) if symbol in raw else []
            if bars:
                strategy.warm_up(symbol, bars[-need:])
                log_info("warmup_seeded", symbol=symbol,
                         bars=len(bars[-need:]), timeframe=f"{tf_min}m")
        except Exception as exc:
            log_error("warmup_failed", symbol=symbol, error=str(exc))


async def run(slug: str | None = None) -> None:
    from config_loader import Config
    from profiles import load_active_config, load_profile

    if slug:
        data = load_profile(slug)
        config = Config(**{k: v for k, v in data.items() if k != "name"})
        log_dir = Path("logs") / slug
        kill_path = log_dir / "KILL"
    else:
        config = load_active_config()
        log_dir = Path("logs")
        kill_path = Path("KILL")

    setup_logging(log_dir)
    is_crypto = config.asset_class == "crypto"
    log_info("startup", symbols=config.symbols, live=config.live,
             asset_class=config.asset_class,
             mode="paper" if not config.live else "LIVE")

    # Donchian is a separate daily-bar loop — hand off and return
    if config.strategy.name == "donchian":
        await _run_donchian(config, kill_path)
        return

    try:
        broker = BrokerClient(config)
    except LiveTradingNotConfirmedError as exc:
        print(str(exc), file=sys.stderr)
        return

    await alerts.alert_startup(config.symbols)

    risk = RiskManager(
        max_position_usd=config.risk.max_position_usd,
        stop_loss_pct=config.risk.stop_loss_pct,
        daily_loss_limit_usd=config.risk.daily_loss_limit_usd,
        max_open_positions=config.risk.max_open_positions,
        kill_switch_path=kill_path,
    )
    strategy, entry_order_type = _build_strategy(config)
    executor = OrderExecutor(
        broker=broker,
        risk=risk,
        entry_order_type=entry_order_type,
        trailing_stop_pct=float(config.risk.trailing_stop_pct),
        loser_cut_pct=config.risk.loser_cut_pct,
        enable_claude_filter=config.ai.enable_claude_filter,
        fractional=is_crypto,
        broker_trailing_stop=not is_crypto,
    )

    # Pre-market research: score each symbol before the trading loop
    if config.ai.enable_research:
        research = PremarketResearch()
        symbol_research = await research.run(config.symbols)
        executor.set_research(symbol_research)
        log_info("research_complete", symbols_scored=list(symbol_research.keys()))

    # Seed indicators from history so the bot can trade immediately on startup
    # (otherwise a long regime MA needs ~warmup_bars live candles first).
    if is_crypto:
        await _warm_up_crypto(strategy, config)

    feed = DataFeed(config)

    shutdown_event = asyncio.Event()

    def _handle_sigint(sig: int, frame: object) -> None:
        log_info("shutdown_signal_received", sig=sig)
        shutdown_event.set()

    signal.signal(signal.SIGINT, _handle_sigint)

    feed_task = asyncio.create_task(feed.run(), name="data-feed")

    tick         = 0
    current_day: date | None = None

    try:
        while not shutdown_event.is_set():
            if risk.poll_kill_switch():
                log_info("kill_switch_triggered")
                await alerts.alert_kill_switch()
                await executor.flatten_all()
                break

            if risk.should_flatten_all:
                log_info("daily_loss_limit_hit", daily_pnl=str(risk.daily_pnl))
                await alerts.alert_daily_limit(str(risk.daily_pnl))
                await executor.flatten_all()
                break

            try:
                bar = feed.queue.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.1)
                tick += 1
                if tick % _ORDER_POLL_INTERVAL == 0:
                    await executor.poll_order_status()
                if tick % _POSITION_POLL_INTERVAL == 0:
                    await executor.poll_positions()
                continue

            now = datetime.now(tz=ET)

            # Stocks only trade during market hours and reset each session.
            # Crypto trades 24/7 — no market-hours gate, no daily flatten/reset.
            if not is_crypto:
                if not _is_market_hours(now):
                    continue

                if not feed.connected:
                    continue

                # ── Daily reset at the start of each new trading day ──────────
                today = now.date()
                if today != current_day:
                    if current_day is not None:      # not the very first bar
                        executor.end_of_day()
                        strategy.reset_day()
                        risk.reset_day()
                        log_info("new_trading_day", date=str(today))
                    current_day = today
            elif not feed.connected:
                continue

            if isinstance(bar, Bar):
                sig = strategy.on_bar(bar)
                if sig is not None:
                    await executor.process_signal(sig)

    except Exception as exc:
        log_error("main_loop_error", error=str(exc), exc_info=True)
        await alerts.alert_error("main_loop_error", str(exc))
    finally:
        log_info("shutting_down")
        # Stocks flatten on shutdown. Crypto positions ride untouched so a
        # restart/deploy doesn't churn them — but note crypto exits are managed
        # by the strategy while running (Alpaca has no broker-side crypto stop),
        # so a stopped bot leaves crypto positions unmanaged until restarted.
        if not is_crypto:
            await executor.flatten_all()
            executor.end_of_day()
        feed_task.cancel()
        try:
            await feed_task
        except asyncio.CancelledError:
            pass
        await alerts.alert_shutdown()
        log_info("shutdown_complete")


def _parse_profile_arg(argv: list[str]) -> str | None:
    if "--profile" in argv:
        i = argv.index("--profile")
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


if __name__ == "__main__":
    asyncio.run(run(_parse_profile_arg(sys.argv[1:])))
