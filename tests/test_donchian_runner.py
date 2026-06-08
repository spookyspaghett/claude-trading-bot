from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import donchian_runner as dr
import donchian_strategy as ds
from donchian_runner import DonchianRunner
from donchian_strategy import DonchianLiveStrategy, DonchianPosition
from risk import RiskManager


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(dr, "HANDOFF_PATH", tmp_path / "handoff.json")
    monkeypatch.setattr(ds, "STATE_PATH", tmp_path / "state.json")


class _FakeBroker:
    pass  # __init__ of the runner doesn't call the broker


class _LivePos:
    def __init__(self, symbol, qty, avg_entry_price, current_price=None):  # noqa: ANN001
        self.symbol = symbol
        self.qty = qty
        self.avg_entry_price = avg_entry_price
        self.current_price = current_price if current_price is not None else avg_entry_price


class _ReconcileBroker:
    """Async broker stub exposing a fixed set of live positions."""

    def __init__(self, positions):  # noqa: ANN001
        self._positions = positions
        self.closed: list[str] = []

    async def get_all_positions(self):
        return list(self._positions)

    async def close_position(self, symbol):  # noqa: ANN001
        self.closed.append(symbol)


def _runner(broker=None) -> DonchianRunner:  # noqa: ANN001
    risk = RiskManager(
        max_position_usd=Decimal("1000"), stop_loss_pct=Decimal("1"),
        daily_loss_limit_usd=Decimal("500"), max_open_positions=4,
    )
    strat = DonchianLiveStrategy(lookback_days=40, trend_ma=200, long_only=True)
    return DonchianRunner(["AAPL"], broker or _FakeBroker(), risk, strat,
                          "k", "s", "stock")


def _et_today() -> str:
    return str(datetime.now(tz=dr.ET).date())


def test_per_profile_handoff_files_are_isolated(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    # Two Donchian bots running at once must not clobber each other's queue.
    monkeypatch.setattr(dr, "HANDOFF_PATH", tmp_path / "donchian_handoff.json")

    def _runner_for(slug: str) -> DonchianRunner:
        risk = RiskManager(
            max_position_usd=Decimal("1000"), stop_loss_pct=Decimal("1"),
            daily_loss_limit_usd=Decimal("500"), max_open_positions=4,
        )
        strat = DonchianLiveStrategy(lookback_days=40, trend_ma=200, long_only=True)
        return DonchianRunner(["AAPL"], _FakeBroker(), risk, strat,
                              "k", "s", "stock", slug=slug)

    a = _runner_for("alpha")
    b = _runner_for("beta")
    assert a._handoff_path != b._handoff_path
    assert a._handoff_path.name == "donchian_handoff_alpha.json"
    assert b._handoff_path.name == "donchian_handoff_beta.json"

    a._queued_entries = {"AAPL": "enter_long"}
    a._queued_date = "2026-06-09"
    a._save_handoff()
    b._queued_entries = {"MSFT": "enter_long"}
    b._queued_date = "2026-06-09"
    b._save_handoff()

    # Reload each independently — no cross-contamination.
    a2 = _runner_for("alpha")
    b2 = _runner_for("beta")
    assert a2._queued_entries == {"AAPL": "enter_long"}
    assert b2._queued_entries == {"MSFT": "enter_long"}


def test_handoff_persists_across_restart() -> None:
    r1 = _runner()
    r1._queued_entries = {"AAPL": "enter_long"}
    r1._queued_exits = {"MSFT"}
    r1._queued_date = "2026-06-09"
    r1._ran_eod_date = "2026-06-09"
    r1._ran_open_date = "2026-06-08"
    r1._save_handoff()

    r2 = _runner()   # simulates a service restart: loads handoff in __init__
    assert r2._queued_entries == {"AAPL": "enter_long"}
    assert r2._queued_exits == {"MSFT"}
    assert r2._queued_date == "2026-06-09"
    assert r2._ran_eod_date == "2026-06-09"      # debounce survives → no double scan
    assert r2._ran_open_date == "2026-06-08"


def test_stale_queue_expires_and_drops_unfilled_entry() -> None:
    r = _runner()
    r._strategy._positions["AAPL"] = DonchianPosition(
        symbol="AAPL", direction="BUY", entry_price=100.0, entry_date="x",
        stop_price=98.0, channel_low=95.0, channel_high=101.0, peak_price=100.0,
        qty=0.0,   # never filled
    )
    r._queued_entries = {"AAPL": "enter_long"}
    r._queued_date = str((datetime.now(tz=dr.ET) - timedelta(days=10)).date())

    r._expire_stale_queue()

    assert r._queued_entries == {}
    assert "AAPL" not in r._strategy.open_positions   # unfilled entry dropped
    assert r._queued_date == ""


def test_fresh_queue_is_not_expired() -> None:
    r = _runner()
    r._queued_entries = {"AAPL": "enter_long"}
    r._queued_date = _et_today()
    r._expire_stale_queue()
    assert r._queued_entries == {"AAPL": "enter_long"}   # today's queue survives


def test_weekend_queue_survives_until_monday(monkeypatch) -> None:  # noqa: ANN001
    import datetime as dtm

    # A guaranteed Saturday: a Friday-evening queue checked over the weekend must
    # survive (no session opened) so it can execute at Monday's open.
    sat = dtm.date(2026, 6, 13)
    while sat.weekday() != 5:
        sat += dtm.timedelta(days=1)
    fixed = dtm.datetime(sat.year, sat.month, sat.day, 11, 0, tzinfo=dr.ET)

    class _FixedDateTime:
        @staticmethod
        def now(tz=None):  # noqa: ANN001, ANN205
            return fixed

    monkeypatch.setattr(dr, "datetime", _FixedDateTime)

    r = _runner()
    r._queued_entries = {"AAPL": "enter_long"}
    r._queued_date = str(sat - dtm.timedelta(days=1))   # Friday
    r._expire_stale_queue()
    assert r._queued_entries == {"AAPL": "enter_long"}   # weekend → not expired


@pytest.mark.asyncio
async def test_reconcile_drops_tracked_position_broker_doesnt_have() -> None:
    r = _runner(_ReconcileBroker([]))   # broker holds nothing
    r._strategy._positions["AAPL"] = DonchianPosition(
        symbol="AAPL", direction="BUY", entry_price=100.0, entry_date="x",
        stop_price=98.0, channel_low=95.0, channel_high=101.0, peak_price=100.0,
        qty=10.0,   # we believe we hold it
    )
    await r._reconcile_with_broker()
    assert "AAPL" not in r._strategy.open_positions   # phantom dropped


@pytest.mark.asyncio
async def test_reconcile_keeps_unfilled_queued_entry() -> None:
    r = _runner(_ReconcileBroker([]))
    r._strategy._positions["AAPL"] = DonchianPosition(
        symbol="AAPL", direction="BUY", entry_price=100.0, entry_date="x",
        stop_price=98.0, channel_low=95.0, channel_high=101.0, peak_price=100.0,
        qty=0.0,   # queued entry, not yet placed → legitimately broker-absent
    )
    await r._reconcile_with_broker()
    assert "AAPL" in r._strategy.open_positions   # not dropped


@pytest.mark.asyncio
async def test_reconcile_adopts_untracked_broker_position(monkeypatch) -> None:  # noqa: ANN001
    broker = _ReconcileBroker([_LivePos("MSFT", qty=5.0, avg_entry_price=400.0)])
    r = _runner(broker)
    # No bars available → adoption falls back to the 8% stop.
    async def _no_bars(symbol, n=260):  # noqa: ANN001, ANN202
        return []
    monkeypatch.setattr(r, "_fetch_daily_bars", _no_bars)

    await r._reconcile_with_broker()

    adopted = r._strategy.open_positions.get("MSFT")
    assert adopted is not None
    assert adopted.direction == "BUY"
    assert adopted.qty == 5.0
    assert adopted.entry_price == 400.0
    assert adopted.stop_price == round(400.0 * 0.92, 2)   # 8% fallback stop


@pytest.mark.asyncio
async def test_reanchor_retries_until_fill_visible() -> None:
    broker = _ReconcileBroker([])   # fill not yet visible at the broker
    r = _runner(broker)
    r._strategy._positions["AAPL"] = DonchianPosition(
        symbol="AAPL", direction="BUY", entry_price=100.0, entry_date="x",
        stop_price=97.0, channel_low=95.0, channel_high=101.0, peak_price=100.0,
        qty=10.0,   # stop distance = 3
    )
    r._pending_reanchor = {"AAPL"}

    await r._reanchor_stops()   # broker empty → can't anchor yet
    assert "AAPL" in r._pending_reanchor
    assert r._strategy.open_positions["AAPL"].stop_price == 97.0

    broker._positions = [_LivePos("AAPL", qty=10.0, avg_entry_price=105.0)]
    await r._reanchor_stops()   # fill now visible → re-anchor, preserving dist
    pos = r._strategy.open_positions["AAPL"]
    assert pos.entry_price == 105.0
    assert pos.stop_price == 102.0          # 105 − 3
    assert "AAPL" not in r._pending_reanchor


def test_pending_reanchor_persists_across_restart() -> None:
    r1 = _runner()
    r1._pending_reanchor = {"AAPL"}
    r1._save_handoff()
    r2 = _runner()   # restart
    assert r2._pending_reanchor == {"AAPL"}


class _Bar:
    def __init__(self, ts, o, h, low, c):  # noqa: ANN001
        self.timestamp = ts
        self.open = o
        self.high = h
        self.low = low
        self.close = c


def _daily_series(last_date, n=300, price=100.0):  # noqa: ANN001
    """n daily bars ending on last_date (a date), one calendar day apart."""
    import datetime as dtm
    out = []
    for i in range(n):
        d = last_date - dtm.timedelta(days=(n - 1 - i))
        ts = dtm.datetime(d.year, d.month, d.day, 5, 0, tzinfo=timezone.utc)
        out.append(_Bar(ts, price, price, price, price))
    return out


@pytest.mark.asyncio
async def test_eod_scan_skips_stale_bar_and_retries(monkeypatch) -> None:  # noqa: ANN001
    import datetime as dtm
    r = _runner()
    ran_date = "2026-06-10"
    yesterday = dtm.date(2026, 6, 9)   # latest bar is a day old → stale

    async def _stale_bars(symbol, n=260):  # noqa: ANN001, ANN202
        return _daily_series(yesterday, n=max(n, 5))
    monkeypatch.setattr(r, "_fetch_daily_bars", _stale_bars)

    scanned: list[str] = []
    monkeypatch.setattr(r._strategy, "scan",
                        lambda sym, bars: scanned.append(sym))  # noqa: ARG005

    await r._run_eod_scan(ran_date)

    assert scanned == []                  # stale bar → strategy.scan never called
    assert r._ran_eod_date != ran_date    # day NOT marked done → window retries


@pytest.mark.asyncio
async def test_eod_scan_runs_on_fresh_bar(monkeypatch) -> None:  # noqa: ANN001
    import datetime as dtm
    from donchian_strategy import ScanResult

    r = _runner()
    ran_date = "2026-06-10"
    today = dtm.date(2026, 6, 10)

    async def _fresh_bars(symbol, n=260):  # noqa: ANN001, ANN202
        return _daily_series(today, n=max(n, 5))
    monkeypatch.setattr(r, "_fetch_daily_bars", _fresh_bars)

    scanned: list[str] = []

    def _scan(sym, bars):  # noqa: ANN001, ANN202
        scanned.append(sym)
        return ScanResult(action="none", symbol=sym)
    monkeypatch.setattr(r._strategy, "scan", _scan)

    await r._run_eod_scan(ran_date)

    assert scanned == ["AAPL"]            # fresh bar → scanned
    assert r._ran_eod_date == ran_date    # day marked done


def test_missed_morning_window_expires_even_when_recent(monkeypatch) -> None:  # noqa: ANN001
    import datetime as dtm

    # A guaranteed Wednesday, 11:00 ET (past the 09:36 morning window).
    d = dtm.date(2026, 6, 10)
    while d.weekday() != 2:
        d += dtm.timedelta(days=1)
    fixed = dtm.datetime(d.year, d.month, d.day, 11, 0, tzinfo=dr.ET)

    class _FixedDateTime:
        @staticmethod
        def now(tz=None):  # noqa: ANN001, ANN205
            return fixed

    monkeypatch.setattr(dr, "datetime", _FixedDateTime)

    r = _runner()
    r._strategy._positions["AAPL"] = DonchianPosition(
        symbol="AAPL", direction="BUY", entry_price=100.0, entry_date="x",
        stop_price=98.0, channel_low=95.0, channel_high=101.0, peak_price=100.0, qty=0.0,
    )
    r._queued_entries = {"AAPL": "enter_long"}
    r._queued_date = str(d - dtm.timedelta(days=1))   # scanned yesterday (age 1 day)
    r._ran_open_date = ""                              # never ran this morning

    r._expire_stale_queue()   # 1 day old but the open was missed → expire
    assert r._queued_entries == {}
    assert "AAPL" not in r._strategy.open_positions
