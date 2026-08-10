from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from alpaca.trading.enums import OrderSide, OrderStatus

from executor import OrderExecutor
from risk import RiskManager
from strategy import Direction, Signal


class _FakeOrder:
    def __init__(self, side: OrderSide, symbol: str, qty: Decimal) -> None:
        self.id = uuid.uuid4()
        self.status = OrderStatus.FILLED
        self.side = side
        self.symbol = symbol
        self.filled_qty = qty
        self.filled_avg_price = Decimal("100")


class FakeBroker:
    """Records the protective orders the executor places."""

    def __init__(self) -> None:
        self.orders: dict[str, _FakeOrder] = {}
        self.stop_orders: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.trailing_calls = 0
        self._positions: list[Any] = []

    async def submit_market_order(self, symbol: str, qty: Decimal, side: OrderSide) -> _FakeOrder:
        o = _FakeOrder(side, symbol, qty)
        self.orders[str(o.id)] = o
        return o

    async def submit_limit_order(self, symbol, qty, side, limit_price) -> _FakeOrder:  # noqa: ANN001
        o = _FakeOrder(side, symbol, qty)
        o.filled_avg_price = limit_price
        self.orders[str(o.id)] = o
        return o

    async def submit_stop_order(self, symbol, qty, side, stop_price) -> _FakeOrder:  # noqa: ANN001
        o = _FakeOrder(side, symbol, qty)
        o.status = OrderStatus.NEW
        self.orders[str(o.id)] = o
        self.stop_orders.append({"symbol": symbol, "qty": qty, "side": side, "stop_price": stop_price})
        return o

    async def cancel_order(self, order_id: str) -> None:
        self.cancelled.append(order_id)

    async def submit_trailing_stop_order(self, symbol, qty, side, trail_percent) -> _FakeOrder:  # noqa: ANN001
        self.trailing_calls += 1
        o = _FakeOrder(side, symbol, qty)
        self.orders[str(o.id)] = o
        return o

    async def get_order(self, order_id: uuid.UUID) -> _FakeOrder:
        return self.orders[str(order_id)]

    async def cancel_all_orders(self) -> None:
        return None

    async def close_all_positions(self) -> None:
        return None

    async def get_all_positions(self) -> list[Any]:
        return self._positions


def _executor(broker: FakeBroker, place_broker_stop: bool) -> OrderExecutor:
    risk = RiskManager(
        max_position_usd=Decimal("1000"), stop_loss_pct=Decimal("1"),
        daily_loss_limit_usd=Decimal("500"), max_open_positions=4,
    )
    ex = OrderExecutor(
        broker=broker, risk=risk, entry_order_type="market",
        trailing_stop_pct=10.0, place_broker_stop=place_broker_stop,
    )
    ex._journal = MagicMock()  # don't write journal files in tests
    return ex


def _run(ex: OrderExecutor, sig: Signal) -> None:
    async def go() -> None:
        await ex.process_signal(sig)
        await ex.poll_order_status()
    asyncio.run(go())


def test_stock_entry_places_fixed_protective_stop() -> None:
    broker = FakeBroker()
    ex = _executor(broker, place_broker_stop=True)
    sig = Signal(symbol="AAPL", direction=Direction.BUY,
                 entry_price=Decimal("100"), stop_price=Decimal("99"), reason="t")
    _run(ex, sig)

    # A fixed stop at the configured stop_price is placed — not a trailing stop.
    assert broker.trailing_calls == 0
    assert len(broker.stop_orders) == 1
    so = broker.stop_orders[0]
    assert so["stop_price"] == Decimal("99")
    assert so["side"] == OrderSide.SELL          # protective sell under a long
    assert len(ex._pending_stops) == 1           # tracked for cancel/fill accounting


def test_short_entry_stop_is_buy_above() -> None:
    broker = FakeBroker()
    ex = _executor(broker, place_broker_stop=True)
    sig = Signal(symbol="AAPL", direction=Direction.SELL,
                 entry_price=Decimal("100"), stop_price=Decimal("101"), reason="t")
    _run(ex, sig)
    assert broker.stop_orders[0]["side"] == OrderSide.BUY
    assert broker.stop_orders[0]["stop_price"] == Decimal("101")


def test_crypto_entry_places_no_broker_stop() -> None:
    broker = FakeBroker()
    ex = _executor(broker, place_broker_stop=False)
    sig = Signal(symbol="BTC/USD", direction=Direction.BUY,
                 entry_price=Decimal("100"), stop_price=Decimal("99"), reason="t")
    _run(ex, sig)
    assert broker.stop_orders == []
    assert broker.trailing_calls == 0
    assert ex._pending_stops == {}


def test_flatten_all_clears_tracked_stops() -> None:
    broker = FakeBroker()
    ex = _executor(broker, place_broker_stop=True)
    sig = Signal(symbol="AAPL", direction=Direction.BUY,
                 entry_price=Decimal("100"), stop_price=Decimal("99"), reason="t")
    _run(ex, sig)
    assert len(ex._pending_stops) == 1
    asyncio.run(ex.flatten_all())
    assert ex._pending_stops == {}
    assert ex._pending_entries == {}
    assert ex._open == {}


def _position(symbol: str, qty: str, current_price: str, plpc: str = "0.0", pl: str = "0") -> Any:
    p = MagicMock()
    p.symbol = symbol; p.qty = qty; p.current_price = current_price
    p.unrealized_plpc = plpc; p.unrealized_pl = pl
    return p


def test_trailing_stop_ratchets_up_not_down() -> None:
    broker = FakeBroker()
    ex = _executor(broker, place_broker_stop=True)   # trailing_stop_pct=10
    sig = Signal(symbol="AAPL", direction=Direction.BUY,
                 entry_price=Decimal("100"), stop_price=Decimal("99"), reason="t")
    _run(ex, sig)
    assert ex._open["AAPL"].stop_price == Decimal("99")   # initial configured stop

    # Price runs to 130 → trail to 10% below peak = 117 (raised above the initial 99).
    broker._positions = [_position("AAPL", "1", "130")]
    asyncio.run(ex.poll_positions())
    assert ex._open["AAPL"].stop_price == Decimal("117.00")
    assert len(broker.cancelled) == 1                      # old stop cancelled
    assert broker.stop_orders[-1]["stop_price"] == Decimal("117.00")

    # Price pulls back to 120 → stop must NOT move down.
    broker._positions = [_position("AAPL", "1", "120")]
    asyncio.run(ex.poll_positions())
    assert ex._open["AAPL"].stop_price == Decimal("117.00")
    assert len(broker.cancelled) == 1                      # no extra cancel/replace


def test_loser_cut_only_touches_bot_positions() -> None:
    broker = FakeBroker()
    ex = _executor(broker, place_broker_stop=True)
    # A position the bot did NOT open (not in cost_basis) — must be ignored.
    broker._positions = [_position("TSLA", "5", "200", plpc="-0.20", pl="-100")]
    closed: list[str] = []
    broker.close_position = lambda s: closed.append(s)  # type: ignore[assignment]
    asyncio.run(ex.poll_positions())
    assert closed == []   # untracked position left alone


# ── partial fills / expired stops (long-running regressions) ─────────────────

def _partial_signal() -> Signal:
    return Signal(symbol="AAPL", direction=Direction.BUY,
                  entry_price=Decimal("100"), stop_price=Decimal("95"),
                  reason="test")


def test_partial_fill_is_booked_once_per_new_share(monkeypatch) -> None:  # noqa: ANN001
    """A PARTIALLY_FILLED order used to be re-logged and re-alerted in full on
    every poll (~10s) for as long as it stayed partial — days, on a crypto GTC
    order — flooding Telegram until it 429'd and alerting died entirely."""
    import executor as ex_mod

    alerts_sent: list[str] = []

    async def _fake_alert_fill(symbol, side, qty, price):  # noqa: ANN001, ANN202
        alerts_sent.append(qty)
    monkeypatch.setattr(ex_mod.alerts, "alert_fill", _fake_alert_fill)

    broker = FakeBroker()
    ex = _executor(broker, place_broker_stop=True)

    async def go() -> None:
        await ex.process_signal(_partial_signal())
        order_id = next(iter(ex._pending_entries))
        order = broker.orders[order_id]

        order.status = OrderStatus.PARTIALLY_FILLED
        order.filled_qty = Decimal("4")
        await ex.poll_order_status()
        await ex.poll_order_status()          # nothing new filled
        await ex.poll_order_status()

        order.filled_qty = Decimal("6")       # 2 more shares fill
        await ex.poll_order_status()

    asyncio.run(go())

    assert alerts_sent == ["4", "2"], f"expected one alert per new fill, got {alerts_sent}"


def test_partial_fill_releases_the_pending_risk_slot() -> None:
    # A partial fill is a real position, not a pending entry. Leaving it pending
    # burned an open-position slot for the life of the process.
    broker = FakeBroker()
    ex = _executor(broker, place_broker_stop=True)

    async def go() -> None:
        await ex.process_signal(_partial_signal())
        order_id = next(iter(ex._pending_entries))
        broker.orders[order_id].status = OrderStatus.PARTIALLY_FILLED
        broker.orders[order_id].filled_qty = Decimal("4")
        await ex.poll_order_status()

    asyncio.run(go())

    assert "AAPL" not in ex._risk._pending
    assert ex._risk.open_symbols == ["AAPL"]   # counted as a real position


def test_partial_fill_gets_a_protective_stop() -> None:
    # The stop used to be gated on a full fill, so the filled shares rode naked.
    broker = FakeBroker()
    ex = _executor(broker, place_broker_stop=True)

    async def go() -> None:
        await ex.process_signal(_partial_signal())
        order_id = next(iter(ex._pending_entries))
        broker.orders[order_id].status = OrderStatus.PARTIALLY_FILLED
        broker.orders[order_id].filled_qty = Decimal("4")
        await ex.poll_order_status()

    asyncio.run(go())

    assert len(broker.stop_orders) == 1
    assert broker.stop_orders[0]["qty"] == Decimal("4")
    assert ex._open["AAPL"].qty == Decimal("4")


def test_stop_is_resized_when_more_of_the_entry_fills() -> None:
    broker = FakeBroker()
    ex = _executor(broker, place_broker_stop=True)

    async def go() -> None:
        await ex.process_signal(_partial_signal())
        order_id = next(iter(ex._pending_entries))
        order = broker.orders[order_id]
        order.status = OrderStatus.PARTIALLY_FILLED
        order.filled_qty = Decimal("4")
        await ex.poll_order_status()
        order.filled_qty = Decimal("10")
        await ex.poll_order_status()

    asyncio.run(go())

    assert len(broker.stop_orders) == 2          # replaced, not duplicated
    assert broker.stop_orders[-1]["qty"] == Decimal("10")
    assert len(broker.cancelled) == 1            # old stop cancelled first
    assert ex._open["AAPL"].qty == Decimal("10")


def test_expired_stop_is_replaced_not_abandoned() -> None:
    """Stock stops are TimeInForce.DAY, so every protective stop dies at the
    close. The position kept a dead stop_order_id, so _raise_stop threw forever
    and the stop was never re-created — an overnight position lost its
    protection silently."""
    broker = FakeBroker()
    ex = _executor(broker, place_broker_stop=True)

    async def go() -> None:
        await ex.process_signal(_partial_signal())   # fills fully by default
        await ex.poll_order_status()
        stop_id = ex._open["AAPL"].stop_order_id
        assert stop_id

        broker.orders[stop_id].status = OrderStatus.EXPIRED
        await ex.poll_order_status()
        assert ex._open["AAPL"].stop_order_id == ""   # flagged unprotected

        await ex._update_trailing_stop("AAPL", Decimal("101"))

    asyncio.run(go())

    assert len(broker.stop_orders) == 2, "expired stop was not re-placed"
    assert ex._open["AAPL"].stop_order_id != ""


def test_failed_flatten_keeps_tracking_the_position() -> None:
    """If close_all_positions() fails, those positions are still live at the
    broker. Forgetting them means nothing trails their stop, nothing cuts them,
    and the next signal opens a second position on top of the first."""
    broker = FakeBroker()
    ex = _executor(broker, place_broker_stop=True)

    async def go() -> None:
        await ex.process_signal(_partial_signal())
        await ex.poll_order_status()
        assert "AAPL" in ex._open

        async def _boom() -> None:
            raise RuntimeError("Temporary failure in name resolution")
        broker.close_all_positions = _boom       # type: ignore[assignment]

        await ex.flatten_all()

    asyncio.run(go())

    assert "AAPL" in ex._open, "dropped a position the broker never closed"
    assert "AAPL" in ex._cost_basis
    assert ex._risk.open_symbols == ["AAPL"]


def test_successful_flatten_releases_everything() -> None:
    broker = FakeBroker()
    ex = _executor(broker, place_broker_stop=True)

    async def go() -> None:
        await ex.process_signal(_partial_signal())
        await ex.poll_order_status()
        await ex.flatten_all()

    asyncio.run(go())

    assert ex._open == {}
    assert ex._cost_basis == {}
    assert ex._risk.open_symbols == []      # risk book released too
    assert ex._risk.check_new_order("MSFT")[0] is True


# ── broker-outage logging (a resolver failure used to log identically forever) ─

def test_poll_failures_throttle_then_report_recovery(monkeypatch: Any) -> None:
    """A sustained outage logs on the 1st and every 10th failure, not all 25 —
    and announces recovery once the broker answers again."""
    broker = FakeBroker()
    ex = _executor(broker, place_broker_stop=True)

    errors: list[dict[str, Any]] = []
    infos: list[str] = []
    monkeypatch.setattr("executor.log_error",
                        lambda event, **kw: errors.append({"event": event, **kw}))

    async def boom() -> list[Any]:
        raise OSError("Temporary failure in name resolution")

    broker.get_all_positions = boom  # type: ignore[assignment]
    for _ in range(25):
        asyncio.run(ex.poll_positions())

    assert ex._poll_failures == 25
    assert [e["consecutive_failures"] for e in errors] == [1, 10, 20]

    # Broker comes back: one recovery line, counter cleared.
    import executor as executor_mod
    monkeypatch.setattr(executor_mod._log, "info",
                        lambda event, **kw: infos.append(event))
    broker._positions = []

    async def ok() -> list[Any]:
        return []

    broker.get_all_positions = ok  # type: ignore[assignment]
    asyncio.run(ex.poll_positions())
    assert ex._poll_failures == 0
    assert "poll_positions_recovered" in infos
