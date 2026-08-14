from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config_loader import RiskConfig

_HUNDRED = Decimal("100")
_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True)
class RiskPolicy:
    """The knobs that turn risk from an *output* into an *input*.

    Every field defaults to "off" so a ``RiskManager`` built without a policy
    behaves exactly as it did before: one flat notional per position, and
    whatever dollar risk the stop distance happens to imply.

    ``risk_per_trade_pct`` is the switch that matters. With it set, size is
    solved from the stop instead of the price:

        qty = (equity × risk_per_trade_pct%) / |entry − stop|

    so a wide-stop trade takes a small position and a tight-stop trade takes a
    large one, and both lose the same amount when they're wrong.
    """

    # ── per-trade sizing ──────────────────────────────────────────────────────
    # Fraction of equity risked between entry and stop. 0 = legacy fixed notional.
    risk_per_trade_pct: Decimal = _ZERO

    # ── portfolio-level caps ──────────────────────────────────────────────────
    # Ceiling on the sum of open risk (Σ |entry−stop| × qty) as % of equity.
    # This is the number that actually bounds a bad week; max_open_positions
    # only counts positions, and positions are not a unit of risk. 0 = off.
    max_portfolio_heat_pct: Decimal = _ZERO
    # Same ceiling applied within a correlation group. Four megacap tech names
    # are not four bets, and sizing them as if they were is how a book that
    # looks diversified takes one concentrated loss. 0 = off.
    max_group_heat_pct: Decimal = _ZERO
    # symbol → group label. Symbols absent from the map are their own group and
    # are constrained only by the portfolio-wide cap.
    correlation_groups: Mapping[str, str] = field(default_factory=dict)
    # Gross notional ceiling as % of equity. Replaces the old
    # "max_position_usd × position_count" projection, which assumed every open
    # position was the maximum size and so locked small accounts to one trade.
    max_gross_exposure_pct: Decimal = _HUNDRED

    # ── loss limits ───────────────────────────────────────────────────────────
    # Daily loss limit as % of equity. Combined with the absolute USD limit by
    # taking whichever is tighter, so setting it can only ever reduce risk.
    daily_loss_limit_pct: Decimal = _ZERO

    # ── drawdown throttle ─────────────────────────────────────────────────────
    # Below derisk_start_dd_pct the book runs at full size. Between there and
    # halt_dd_pct, per-trade risk scales down linearly to min_risk_scale. At
    # halt_dd_pct new entries stop entirely.
    #
    # Betting the same fraction of a shrinking account is what turns a bad run
    # into an unrecoverable one; the geometry is unforgiving well before the
    # strategy is actually broken. Note this throttles *new* entries only — it
    # deliberately does not force-liquidate, because dumping a book at the
    # bottom of a drawdown realises the exact loss it is meant to limit.
    derisk_start_dd_pct: Decimal = _ZERO
    halt_dd_pct: Decimal = _ZERO
    min_risk_scale: Decimal = Decimal("0.25")

    @classmethod
    def from_config(cls, risk: RiskConfig) -> RiskPolicy:
        """Build a policy from the ``risk:`` block of config.yaml."""
        groups: dict[str, str] = {}
        for label, syms in (risk.correlation_groups or {}).items():
            for sym in syms:
                groups[sym.upper()] = label
        return cls(
            risk_per_trade_pct=risk.risk_per_trade_pct,
            max_portfolio_heat_pct=risk.max_portfolio_heat_pct,
            max_group_heat_pct=risk.max_group_heat_pct,
            correlation_groups=groups,
            max_gross_exposure_pct=risk.max_gross_exposure_pct,
            daily_loss_limit_pct=risk.daily_loss_limit_pct,
            derisk_start_dd_pct=risk.derisk_start_dd_pct,
            halt_dd_pct=risk.halt_dd_pct,
            min_risk_scale=risk.min_risk_scale,
        )


@dataclass
class _Exposure:
    """What one open position actually commits: cash and risk-to-stop."""
    notional: Decimal = _ZERO
    risk: Decimal = _ZERO


class RiskManager:
    def __init__(
        self,
        max_position_usd: Decimal,
        stop_loss_pct: Decimal,
        daily_loss_limit_usd: Decimal,
        max_open_positions: int,
        kill_switch_path: Path = Path("KILL"),
        policy: RiskPolicy | None = None,
    ) -> None:
        self._max_position_usd = max_position_usd
        self._stop_loss_pct = stop_loss_pct
        self._daily_loss_limit_usd = daily_loss_limit_usd
        self._max_open_positions = max_open_positions
        self._kill_switch_path = kill_switch_path
        self._policy = policy or RiskPolicy()

        self._daily_realized_pnl: Decimal = Decimal("0")
        self._open_positions: dict[str, Decimal] = {}  # symbol -> net qty
        self._exposure: dict[str, _Exposure] = {}      # symbol -> committed cash/risk
        self._pending: set[str] = set()                # submitted entries not yet filled
        self._unrealized_pnl: Decimal = Decimal("0")   # latest mark-to-market across positions
        self._account_equity: Decimal | None = None    # latest account equity (for exposure cap)
        self._high_water: Decimal | None = None        # peak equity seen (for the DD throttle)
        self._daily_limit_hit: bool = False
        self._kill_switch_triggered: bool = False

    # ── sizing ────────────────────────────────────────────────────────────────

    def _position_budget(self) -> Decimal:
        """Notional we can actually commit to one position.

        Sizing used to use the configured max unconditionally, while
        `check_new_order` capped `max_position_usd × positions` against equity.
        On an account smaller than `max_position_usd` those two disagreed: the
        very first entry projected more than the account held and was refused,
        so a book whose equity dipped below the configured max went permanently
        silent — every signal rejected as "aggregate exposure would exceed
        account equity", with nothing open to explain it. Size to what the
        account can afford instead of refusing to trade at all.
        """
        if self._account_equity is None or self._account_equity <= _ZERO:
            return self._max_position_usd
        return min(self._max_position_usd, self._account_equity)

    def _risk_capital(self) -> Decimal | None:
        """Dollars this account is willing to lose on the next trade, after the
        drawdown throttle. None when risk-based sizing is not configured."""
        p = self._policy
        if p.risk_per_trade_pct <= _ZERO:
            return None
        if self._account_equity is None or self._account_equity <= _ZERO:
            return None
        return self._account_equity * p.risk_per_trade_pct / _HUNDRED * self.risk_scale

    def _remaining_heat(self, symbol: str | None) -> Decimal | None:
        """Open-risk headroom left under the portfolio and group caps."""
        p = self._policy
        if self._account_equity is None or self._account_equity <= _ZERO:
            return None
        caps: list[Decimal] = []
        if p.max_portfolio_heat_pct > _ZERO:
            budget = self._account_equity * p.max_portfolio_heat_pct / _HUNDRED
            caps.append(budget - self.open_risk_total)
        if p.max_group_heat_pct > _ZERO and symbol:
            group = p.correlation_groups.get(symbol.upper())
            if group:
                budget = self._account_equity * p.max_group_heat_pct / _HUNDRED
                caps.append(budget - self.open_risk_in_group(group))
        if not caps:
            return None
        return max(min(caps), _ZERO)

    def _gross_capacity(self) -> Decimal | None:
        """Notional headroom left under the gross-exposure cap.

        Counts cash committed to *filled* positions only. Entries in flight are
        bounded by the open-position count and the heat caps instead — reserving
        a full ``max_position_usd`` for each pending entry is what produced the
        original one-position lockout on small accounts.
        """
        p = self._policy
        if self._account_equity is None or p.max_gross_exposure_pct <= _ZERO:
            return None
        ceiling = self._account_equity * p.max_gross_exposure_pct / _HUNDRED
        return max(ceiling - self.open_notional_total, _ZERO)

    def compute_qty(
        self,
        price: Decimal,
        fractional: bool = False,
        *,
        symbol: str | None = None,
        stop_price: Decimal | None = None,
    ) -> Decimal:
        """Return position size for the given price.

        Size is the tightest of three ceilings:

        1. **Notional** — ``max_position_usd`` (clamped to equity).
        2. **Risk** — ``equity × risk_per_trade_pct`` divided by the stop
           distance, then clipped to whatever portfolio/group heat is left.
           Requires both a ``stop_price`` and a known equity; without either it
           does not apply and sizing falls back to notional alone.
        3. **Gross exposure** — cash not already committed to open positions.

        Stocks use whole shares; crypto (``fractional=True``) allows fractional
        quantities, rounded down to 6 decimal places.
        """
        if price <= _ZERO:
            return _ZERO

        caps = [self._position_budget() / price]

        risk_capital = self._risk_capital()
        # A stop of 0 means "this signal carries no stop" (exit signals, and
        # crypto strategies that rely on their own exit logic), not "stop at
        # zero" — treat it as absent rather than as a price-wide risk budget.
        if risk_capital is not None and stop_price is not None and stop_price > _ZERO:
            stop_distance = abs(price - stop_price)
            if stop_distance <= _ZERO:
                # A stop at the entry implies infinite size. Refuse rather than
                # silently falling back to the notional cap, which would be the
                # largest position the account can hold on its worst-defined
                # trade.
                return _ZERO
            headroom = self._remaining_heat(symbol)
            if headroom is not None:
                risk_capital = min(risk_capital, headroom)
            caps.append(risk_capital / stop_distance)

        gross = self._gross_capacity()
        if gross is not None:
            caps.append(gross / price)

        raw = max(min(caps), _ZERO)
        if fractional:
            return raw.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
        return Decimal(int(raw))

    def describe_size_limit(
        self,
        price: Decimal,
        *,
        symbol: str | None = None,
        stop_price: Decimal | None = None,
    ) -> str:
        """Name the ceiling that bound the size just computed.

        Risk-based sizing legitimately returns zero when one whole share would
        risk more than the budget allows — on a small account with a wide stop
        that is the correct answer, not a fault. But "position size computed as
        0" on its own reads exactly like the silent lockouts this book has hit
        before, so say which limit bound and what it would take to clear it.
        """
        if price <= _ZERO:
            return "price is not positive"

        caps: list[tuple[str, Decimal]] = [
            (f"max_position_usd ({self._position_budget():.2f})",
             self._position_budget() / price),
        ]
        risk_capital = self._risk_capital()
        if risk_capital is not None and stop_price is not None and stop_price > _ZERO:
            distance = abs(price - stop_price)
            if distance <= _ZERO:
                return "stop equals entry, so risk-based size is undefined"
            headroom = self._remaining_heat(symbol)
            effective = (
                min(risk_capital, headroom) if headroom is not None
                else risk_capital
            )
            label = f"risk budget ({effective:.2f} over a {distance:.2f} stop)"
            if headroom is not None and headroom < risk_capital:
                label = f"remaining portfolio/group heat ({headroom:.2f})"
            caps.append((label, effective / distance))
        gross = self._gross_capacity()
        if gross is not None:
            caps.append((f"gross exposure headroom ({gross:.2f})", gross / price))

        name, qty = min(caps, key=lambda kv: kv[1])
        return f"{name} allows {qty:.4f} units"

    def compute_stop_price(self, entry_price: Decimal, side: str) -> Decimal:
        """Return stop-loss price. side must be 'buy' or 'sell'."""
        factor = self._stop_loss_pct / _HUNDRED
        if side == "buy":
            return (entry_price * (_ONE - factor)).quantize(Decimal("0.01"))
        # short: stop is above entry
        return (entry_price * (_ONE + factor)).quantize(Decimal("0.01"))

    # ── book keeping ──────────────────────────────────────────────────────────

    def register_pending(self, symbol: str) -> None:
        """Mark an entry as submitted-but-unfilled so simultaneous signals can't
        momentarily exceed the open-position limit before fills land (#9)."""
        self._pending.add(symbol)

    def clear_pending(self, symbol: str) -> None:
        self._pending.discard(symbol)

    def set_account_equity(self, equity: Decimal) -> None:
        """Update equity and the high-water mark the drawdown throttle reads.

        Everything percentage-based — per-trade risk, heat, gross exposure, the
        percentage daily limit — is inert until this is called, so every runner
        and every backtest loop must feed it or they silently revert to flat
        notional sizing.
        """
        self._account_equity = equity
        if equity > _ZERO:
            self._high_water = (
                equity if self._high_water is None
                else max(self._high_water, equity)
            )

    def set_unrealized(self, unrealized_pnl: Decimal) -> None:
        """Update mark-to-market PnL; trips the daily limit on deep unrealized
        drawdown, not just realized losses (#9)."""
        self._unrealized_pnl = unrealized_pnl
        total = self._daily_realized_pnl + self._unrealized_pnl
        if total <= -self._effective_daily_limit():
            self._daily_limit_hit = True

    def _effective_daily_limit(self) -> Decimal:
        """The tighter of the absolute and percentage daily loss limits."""
        limits = [abs(self._daily_loss_limit_usd)]
        pct = self._policy.daily_loss_limit_pct
        equity = self._account_equity
        if pct > _ZERO and equity is not None and equity > _ZERO:
            limits.append(equity * pct / _HUNDRED)
        return min(limits)

    def _active_symbols(self) -> set[str]:
        open_syms = {s for s, q in self._open_positions.items() if q != _ZERO}
        return open_syms | self._pending

    def check_new_order(self, symbol: str) -> tuple[bool, str]:
        """Return (allowed, reason). Empty reason string means allowed.

        Gate only — this answers "may we open anything at all", not "how much".
        Position-level exposure is enforced in ``compute_qty``, which knows the
        stop and can therefore size *into* the remaining headroom rather than
        rejecting the trade outright.
        """
        if self._kill_switch_triggered:
            return False, "kill switch active"
        if self._daily_limit_hit:
            return False, "daily loss limit reached"
        halt = self._policy.halt_dd_pct
        if halt > _ZERO and self.current_drawdown_pct >= halt:
            return False, (
                f"drawdown {self.current_drawdown_pct:.1f}% ≥ halt threshold {halt}%"
            )
        active = self._active_symbols()
        # Count open AND pending entries toward the limit (#9).
        if symbol not in active and len(active) >= self._max_open_positions:
            return False, f"max open positions ({self._max_open_positions}) reached"

        if self._account_equity is None:
            return True, ""

        if self._policy.risk_per_trade_pct <= _ZERO:
            # Legacy sizing: every position takes the full budget, so the only
            # honest pre-trade projection assumes the new one will too.
            new_count = len(active) + (0 if symbol in active else 1)
            if self._position_budget() * new_count > self._account_equity:
                return False, "aggregate exposure would exceed account equity"
        elif symbol not in active:
            # Risk sizing knows the stop, so `compute_qty` can size *into* the
            # remaining headroom. Reject here only when there is none left —
            # otherwise a small account is locked to a single position, which is
            # precisely the concentration the risk model exists to prevent.
            gross = self._gross_capacity()
            if gross is not None and gross <= _ZERO:
                return False, "gross exposure cap reached"
            heat = self._remaining_heat(symbol)
            if heat is not None and heat <= _ZERO:
                return False, "portfolio heat cap reached"
        return True, ""

    def record_fill(
        self,
        symbol: str,
        qty: Decimal,            # positive = bought, negative = sold/covered
        realised_pnl: Decimal,
        *,
        entry_price: Decimal | None = None,
        stop_price: Decimal | None = None,
    ) -> None:
        """Update position book and daily P&L after a confirmed fill.

        Pass ``entry_price``/``stop_price`` on entry fills to register what the
        position commits in cash and in risk-to-stop. Without them the position
        is tracked but contributes nothing to the heat and gross-exposure caps,
        so partial plumbing shows up as caps that never bind rather than as an
        error.
        """
        current = self._open_positions.get(symbol, _ZERO)
        new_qty = current + qty
        if new_qty == _ZERO:
            self._open_positions.pop(symbol, None)
            self._exposure.pop(symbol, None)
        else:
            self._open_positions[symbol] = new_qty
            if entry_price is not None:
                size = abs(new_qty)
                has_stop = stop_price is not None and stop_price > _ZERO
                risk = abs(entry_price - stop_price) * size if has_stop else _ZERO  # type: ignore[operator]
                self._exposure[symbol] = _Exposure(
                    notional=entry_price * size, risk=risk,
                )

        self._daily_realized_pnl += realised_pnl
        if self._daily_realized_pnl <= -self._effective_daily_limit():
            self._daily_limit_hit = True

    def poll_kill_switch(self) -> bool:
        """Check for KILL file on disk. Once triggered, stays triggered."""
        if not self._kill_switch_triggered:
            self._kill_switch_triggered = self._kill_switch_path.exists()
        return self._kill_switch_triggered

    # ── risk reporting ────────────────────────────────────────────────────────

    @property
    def open_risk_total(self) -> Decimal:
        """Σ (entry − stop) × qty across open positions: what the book loses if
        every stop fills. The single most useful number in live risk, and the
        one a position count cannot substitute for."""
        return sum((e.risk for e in self._exposure.values()), _ZERO)

    def open_risk_in_group(self, group: str) -> Decimal:
        groups = self._policy.correlation_groups
        return sum(
            (e.risk for s, e in self._exposure.items()
             if groups.get(s.upper()) == group),
            _ZERO,
        )

    @property
    def open_notional_total(self) -> Decimal:
        return sum((e.notional for e in self._exposure.values()), _ZERO)

    @property
    def portfolio_heat_pct(self) -> Decimal:
        """Open risk as a percentage of equity."""
        if self._account_equity is None or self._account_equity <= _ZERO:
            return _ZERO
        return self.open_risk_total / self._account_equity * _HUNDRED

    @property
    def current_drawdown_pct(self) -> Decimal:
        """Peak-to-current equity drawdown, in percent."""
        if self._high_water is None or self._high_water <= _ZERO:
            return _ZERO
        if self._account_equity is None:
            return _ZERO
        dd = (self._high_water - self._account_equity) / self._high_water * _HUNDRED
        return max(dd, _ZERO)

    @property
    def risk_scale(self) -> Decimal:
        """Multiplier applied to per-trade risk, from the drawdown throttle.

        1.0 while the account is near its high-water mark, sliding linearly to
        ``min_risk_scale`` as drawdown approaches ``halt_dd_pct``, then 0.
        """
        p = self._policy
        if p.derisk_start_dd_pct <= _ZERO or p.halt_dd_pct <= p.derisk_start_dd_pct:
            return _ONE
        dd = self.current_drawdown_pct
        if dd <= p.derisk_start_dd_pct:
            return _ONE
        if dd >= p.halt_dd_pct:
            return _ZERO
        span = p.halt_dd_pct - p.derisk_start_dd_pct
        travelled = (dd - p.derisk_start_dd_pct) / span
        return _ONE - travelled * (_ONE - p.min_risk_scale)

    @property
    def should_flatten_all(self) -> bool:
        return self._kill_switch_triggered or self._daily_limit_hit

    @property
    def open_symbols(self) -> list[str]:
        return [s for s, q in self._open_positions.items() if q != _ZERO]

    @property
    def daily_pnl(self) -> Decimal:
        return self._daily_realized_pnl

    def snapshot(self) -> dict[str, float]:
        """Flat risk summary for logging and the API."""
        return {
            "equity": float(self._account_equity or 0),
            "high_water": float(self._high_water or 0),
            "drawdown_pct": float(self.current_drawdown_pct),
            "risk_scale": float(self.risk_scale),
            "open_risk_usd": float(self.open_risk_total),
            "portfolio_heat_pct": float(self.portfolio_heat_pct),
            "open_notional_usd": float(self.open_notional_total),
            "daily_pnl": float(self._daily_realized_pnl),
            "daily_limit_usd": float(self._effective_daily_limit()),
            "open_positions": len(self.open_symbols),
        }

    # ── resets ────────────────────────────────────────────────────────────────

    def clear_positions(self) -> None:
        """Forget the position book after a flatten, keeping daily P&L intact.

        Every exit routes through ``OrderExecutor.flatten_all``, which used to
        clear only its own maps — so symbols stayed in ``_open_positions`` /
        ``_pending`` forever and ``check_new_order`` eventually rejected
        everything with "max open positions reached". Crypto bots never call
        ``reset_day()``, so for them the lockout was permanent.
        """
        self._open_positions.clear()
        self._exposure.clear()
        self._pending.clear()
        self._unrealized_pnl = _ZERO

    def reset_day(self) -> None:
        """Reset daily counters. Kill switch is intentionally NOT reset."""
        self._daily_realized_pnl = _ZERO
        self._daily_limit_hit = False
        self._open_positions.clear()
        self._exposure.clear()
        self._pending.clear()
        self._unrealized_pnl = _ZERO

    def reset_daily_limit(self) -> None:
        """Reset daily P&L counters without clearing open positions.

        Use this in multi-day backtests where each bar represents one calendar
        day — the daily loss limit should apply per day, not to the whole run.
        """
        self._daily_realized_pnl = _ZERO
        self._daily_limit_hit = False
