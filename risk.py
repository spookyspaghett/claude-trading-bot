from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from pathlib import Path


class RiskManager:
    def __init__(
        self,
        max_position_usd: Decimal,
        stop_loss_pct: Decimal,
        daily_loss_limit_usd: Decimal,
        max_open_positions: int,
        kill_switch_path: Path = Path("KILL"),
    ) -> None:
        self._max_position_usd = max_position_usd
        self._stop_loss_pct = stop_loss_pct
        self._daily_loss_limit_usd = daily_loss_limit_usd
        self._max_open_positions = max_open_positions
        self._kill_switch_path = kill_switch_path

        self._daily_realized_pnl: Decimal = Decimal("0")
        self._open_positions: dict[str, Decimal] = {}  # symbol -> net qty
        self._daily_limit_hit: bool = False
        self._kill_switch_triggered: bool = False

    def compute_qty(self, price: Decimal, fractional: bool = False) -> Decimal:
        """Return position size for the given price and max position size.

        Stocks use whole shares; crypto (``fractional=True``) allows fractional
        quantities, rounded down to 6 decimal places.
        """
        if price <= Decimal("0"):
            return Decimal("0")
        raw = self._max_position_usd / price
        if fractional:
            return raw.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
        return Decimal(int(raw))

    def compute_stop_price(self, entry_price: Decimal, side: str) -> Decimal:
        """Return stop-loss price. side must be 'buy' or 'sell'."""
        factor = self._stop_loss_pct / Decimal("100")
        if side == "buy":
            return (entry_price * (Decimal("1") - factor)).quantize(Decimal("0.01"))
        # short: stop is above entry
        return (entry_price * (Decimal("1") + factor)).quantize(Decimal("0.01"))

    def check_new_order(self, symbol: str) -> tuple[bool, str]:
        """Return (allowed, reason). Empty reason string means allowed."""
        if self._kill_switch_triggered:
            return False, "kill switch active"
        if self._daily_limit_hit:
            return False, "daily loss limit reached"
        open_count = sum(1 for q in self._open_positions.values() if q != Decimal("0"))
        if symbol not in self._open_positions and open_count >= self._max_open_positions:
            return False, f"max open positions ({self._max_open_positions}) reached"
        return True, ""

    def record_fill(
        self,
        symbol: str,
        qty: Decimal,            # positive = bought, negative = sold/covered
        realised_pnl: Decimal,
    ) -> None:
        """Update position book and daily P&L after a confirmed fill."""
        current = self._open_positions.get(symbol, Decimal("0"))
        new_qty = current + qty
        if new_qty == Decimal("0"):
            self._open_positions.pop(symbol, None)
        else:
            self._open_positions[symbol] = new_qty

        self._daily_realized_pnl += realised_pnl
        if self._daily_realized_pnl <= -abs(self._daily_loss_limit_usd):
            self._daily_limit_hit = True

    def poll_kill_switch(self) -> bool:
        """Check for KILL file on disk. Once triggered, stays triggered."""
        if not self._kill_switch_triggered:
            self._kill_switch_triggered = self._kill_switch_path.exists()
        return self._kill_switch_triggered

    @property
    def should_flatten_all(self) -> bool:
        return self._kill_switch_triggered or self._daily_limit_hit

    @property
    def open_symbols(self) -> list[str]:
        return [s for s, q in self._open_positions.items() if q != Decimal("0")]

    @property
    def daily_pnl(self) -> Decimal:
        return self._daily_realized_pnl

    def reset_day(self) -> None:
        """Reset daily counters. Kill switch is intentionally NOT reset."""
        self._daily_realized_pnl = Decimal("0")
        self._daily_limit_hit = False
        self._open_positions.clear()

    def reset_daily_limit(self) -> None:
        """Reset daily P&L counters without clearing open positions.

        Use this in multi-day backtests where each bar represents one calendar
        day — the daily loss limit should apply per day, not to the whole run.
        """
        self._daily_realized_pnl = Decimal("0")
        self._daily_limit_hit = False
