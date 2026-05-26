from __future__ import annotations

import asyncio
import signal
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from alpaca.data.models import Bar

import alerts
from broker import BrokerClient, LiveTradingNotConfirmedError
from config_loader import load_config
from data import AggregatedBar, DataFeed
from executor import OrderExecutor
from logger import log_error, log_info, setup_logging
from risk import RiskManager
from strategy import ORBStrategy

ET = ZoneInfo("America/New_York")
MARKET_OPEN: time = time(9, 30)
MARKET_CLOSE: time = time(16, 0)

_ORDER_POLL_INTERVAL = 100


def _is_market_hours(now: datetime) -> bool:
    t = now.astimezone(ET).time()
    return MARKET_OPEN <= t < MARKET_CLOSE


async def run() -> None:
    config = load_config(Path("config.yaml"))
    setup_logging()
    log_info("startup", symbols=config.symbols, live=config.live, mode="paper" if not config.live else "LIVE")

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
    )
    strategy = ORBStrategy(
        config=config.strategy.orb,
        symbols=config.symbols,
        stop_loss_pct=config.risk.stop_loss_pct,
    )
    executor = OrderExecutor(
        broker=broker,
        risk=risk,
        entry_order_type=config.strategy.orb.entry_order_type,
    )
    feed = DataFeed(config)

    shutdown_event = asyncio.Event()

    def _handle_sigint(sig: int, frame: object) -> None:
        log_info("shutdown_signal_received", sig=sig)
        shutdown_event.set()

    signal.signal(signal.SIGINT, _handle_sigint)

    feed_task = asyncio.create_task(feed.run(), name="data-feed")

    tick = 0
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
                continue

            now = datetime.now(tz=ET)

            if not _is_market_hours(now):
                continue

            if not feed.connected:
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
        await executor.flatten_all()
        feed_task.cancel()
        try:
            await feed_task
        except asyncio.CancelledError:
            pass
        await alerts.alert_shutdown()
        log_info("shutdown_complete")


if __name__ == "__main__":
    asyncio.run(run())
