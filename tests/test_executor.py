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
