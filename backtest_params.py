"""The settings a backtest run was configured with.

Two jobs, both about closing the loop between what you test and what you run.

**Loading.** A profile's settings — or a settings CSV exported from one — become
backtest form values, so you can test exactly what the bot is about to trade
instead of retyping twenty knobs and hoping they match.

**Recording.** Every saved report carries the parameters that produced it. Without
them a run history is a list of numbers with no explanation: you can see that
run 3 beat run 5 and have no way to know what was different. The workflow in
docs/trend_sr_filters.md — baseline, add one filter, compare — is unusable
without this, which is why it was previously done by hand in a notebook.

The free-parameter count exists for the same reason. A backtest's trustworthiness
depends less on its P&L than on how many knobs were turned to get it: the widely
cited benchmark is ~30 independent trades per free parameter, and a strategy at
10:1 is very likely overfitted. Counting them is something the tool can do and
the operator usually won't.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from config_loader import Config

#: Backtest form fields, with the defaults the panel starts at. This is the one
#: description of the run's inputs — the loader fills it, the report stores it,
#: and the history compares against it.
DEFAULTS: dict[str, Any] = {
    "strategy": "auto",
    "symbol": "SPY",
    "starting_equity": 500000.0,
    "max_position_usd": 0.0,
    "stop_loss_pct": 0.0,
    # Sizing and the daily halt. Both change results, and both used to be read
    # from the root config.yaml no matter which profile you loaded — so the
    # single most result-defining setting in the risk model was silently not the
    # one you were testing. -1 means "leave config.yaml's value alone", because
    # 0 is meaningful for these (flat notional sizing, limit off).
    "risk_per_trade_pct": -1.0,
    "daily_loss_limit_usd": 0.0,
    "daily_loss_limit_pct": -1.0,
    "slippage_bps": 0.0,
    "commission": 0.0,
    "long_only": False,
    # Donchian / auto
    "lookback_days": 40,
    "exit_lookback": 0,
    "trend_ma": 0,
    "fast_ma": 50,
    "volume_filter_days": 20,
    "use_atr_stop": True,
    "atr_period": 14,
    "atr_multiplier": 1.5,
    "trailing_activation_pct": 2.0,
    "trailing_pct": 8.0,
    # Trend/SR and EMA
    "ma_fast": 21,
    "ma_slow": 55,
    "pivot_lookback": 20,
    "pivot_strength": 3,
    "min_adx": 0.0,
    "adx_period": 14,
    "volume_mult": 0.0,
    "volume_ma": 20,
}

#: Live strategy name -> the backtest's strategy selector. The backtest has no
#: separate donchian/orb entries: "auto" picks Donchian for daily bars and ORB
#: for 1-minute ones, which is the same split those two strategies live on.
_STRATEGY_MAP: dict[str, str] = {
    "donchian": "auto",
    "orb": "auto",
    "trend_sr": "trend_sr",
    "ema": "ema",
    "vwap_revert": "vwap_revert",
}

#: Knobs that can influence each strategy's result. Anything outside its list is
#: inert for that strategy and must not count toward the free-parameter ratio —
#: charging Trend/SR for Donchian's channel length would overstate the risk of
#: overfitting rather than measure it.
_KNOBS: dict[str, tuple[str, ...]] = {
    "auto": (
        "lookback_days", "exit_lookback", "trend_ma", "fast_ma",
        "volume_filter_days", "atr_period", "atr_multiplier",
        "trailing_activation_pct", "trailing_pct", "long_only",
    ),
    "trend_sr": (
        "ma_fast", "ma_slow", "pivot_lookback", "pivot_strength",
        "atr_period", "atr_multiplier", "min_adx", "adx_period",
        "volume_mult", "volume_ma",
        "trailing_activation_pct", "trailing_pct", "long_only",
    ),
    "ema": ("ma_fast", "ma_slow", "stop_loss_pct", "long_only"),
    "vwap_revert": ("long_only",),
}

#: Knobs whose zero means "switch this filter off" rather than "set it to zero".
#: An off filter is not a fitted parameter, so it doesn't count.
_OFF_AT_ZERO = frozenset({
    "trend_ma", "fast_ma", "volume_filter_days", "min_adx", "volume_mult",
    "exit_lookback", "trailing_activation_pct", "stop_loss_pct",
})


def _f(value: Any) -> float:
    return float(value) if isinstance(value, Decimal) else float(value)


def implied_equity(daily_usd: float, daily_pct: float) -> float | None:
    """The account size a profile's dollar limits were written for.

    A settings file carries absolute amounts — max_position_usd,
    daily_loss_limit_usd — that mean nothing without the equity they were sized
    against, and equity is the broker's, not a config field. But the two daily
    limits pin it exactly: they describe the same limit in dollars and in
    percent, so their ratio is the equity the author had in mind.

    Returns None when the profile only sets one of them, in which case there is
    nothing to infer and the form's own value should stand.
    """
    if daily_usd <= 0 or daily_pct <= 0:
        return None
    return daily_usd / (daily_pct / 100.0)


def params_from_config(cfg: Config) -> dict[str, Any]:
    """Map a live profile's Config onto backtest form values.

    Only the knobs the chosen strategy actually reads are pulled from that
    strategy's block; the rest keep their defaults, so loading a Trend/SR
    profile doesn't quietly rewrite the Donchian channel you had set up.
    """
    out = dict(DEFAULTS)
    name = cfg.strategy.name
    out["strategy"] = _STRATEGY_MAP.get(name, "auto")
    if cfg.symbols:
        out["symbol"] = cfg.symbols[0]
    out["max_position_usd"] = _f(cfg.risk.max_position_usd)
    out["stop_loss_pct"] = _f(cfg.risk.stop_loss_pct)
    out["risk_per_trade_pct"] = _f(cfg.risk.risk_per_trade_pct)
    out["daily_loss_limit_usd"] = _f(cfg.risk.daily_loss_limit_usd)
    out["daily_loss_limit_pct"] = _f(cfg.risk.daily_loss_limit_pct)

    # Recover the equity those dollar limits were written against. Without it a
    # $150 position cap loaded onto the form's default $500,000 book caps every
    # trade at 0.03% of equity and the backtest reports roughly nothing.
    equity = implied_equity(
        out["daily_loss_limit_usd"], out["daily_loss_limit_pct"]
    )
    if equity is not None:
        out["starting_equity"] = round(equity, 2)

    # orb rides along with donchian: both map to the "auto" selector, and auto
    # dispatches to Donchian on daily bars. Seeding the channel from the profile
    # beats leaving it on module defaults for a run the profile could produce.
    if name in ("donchian", "orb"):
        dc = cfg.strategy.donchian
        out.update(
            lookback_days=dc.lookback_days,
            exit_lookback=dc.exit_lookback,
            trend_ma=dc.trend_ma,
            long_only=dc.long_only,
            trailing_activation_pct=dc.trailing_activation_pct,
            trailing_pct=dc.trailing_pct,
            use_atr_stop=True,
            # The live Donchian runner hardcodes ATR(14) x 1.5 and exposes no
            # setting for it. Backtesting any other multiplier would be testing
            # a strategy this bot cannot actually run.
            atr_period=14,
            atr_multiplier=1.5,
        )
    elif name == "trend_sr":
        ts = cfg.strategy.trend_sr
        out.update(
            ma_fast=ts.ma_fast,
            ma_slow=ts.ma_slow,
            trend_ma=ts.regime_ma,
            pivot_lookback=ts.pivot_lookback,
            pivot_strength=ts.pivot_strength,
            atr_period=ts.atr_period,
            atr_multiplier=ts.atr_mult,
            min_adx=ts.min_adx,
            adx_period=ts.adx_period,
            volume_mult=ts.volume_mult,
            volume_ma=ts.volume_ma,
            long_only=ts.long_only,
            trailing_activation_pct=ts.trailing_activation_pct,
            trailing_pct=ts.trailing_pct,
        )
    elif name == "ema":
        em = cfg.strategy.ema
        out.update(ma_fast=em.fast_period, ma_slow=em.slow_period)
    elif name == "vwap_revert":
        out["long_only"] = cfg.strategy.vwap_revert.long_only

    return out


def params_from_profile(slug: str) -> dict[str, Any]:
    from profiles import load_profile
    data = load_profile(slug)
    return params_from_config(Config(**{k: v for k, v in data.items() if k != "name"}))


def params_from_csv(
    text: str, base: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Backtest params from a settings CSV, merged onto *base* profile data.

    The CSV goes through the same parser, coercion and validation as a profile
    import, so a file that would be refused there is refused here too — the
    backtest never runs settings the bot could not be given.
    """
    from settings_csv import apply_csv
    result = apply_csv(base, text)
    cfg = Config(**{k: v for k, v in result.profile.items() if k != "name"})
    return params_from_config(cfg), result.warnings


def free_parameters(params: dict[str, Any]) -> list[str]:
    """Knobs actually in play for this run.

    A filter switched off is not a fitted parameter, and a knob the chosen
    strategy never reads is not one either. What's left is the number the
    trades-per-parameter ratio should be measured against.
    """
    strategy = str(params.get("strategy", "auto"))
    active: list[str] = []
    for knob in _KNOBS.get(strategy, ()):
        value = params.get(knob, DEFAULTS.get(knob))
        if knob in _OFF_AT_ZERO and not value:
            continue
        if knob == "long_only" and not value:
            # Not restricting direction is the absence of a choice.
            continue
        atr_knob = knob in ("atr_period", "atr_multiplier")
        if atr_knob and not params.get("use_atr_stop", True):
            continue
        active.append(knob)
    return active


def sample_verdict(trades: int, n_params: int) -> dict[str, Any]:
    """Whether this run's trade count can support its own statistics.

    Thresholds are the conventional ones: under ~30 trades there is nothing to
    infer from, 100+ is where metrics start being reliable, and ~30 trades per
    free parameter is the usual guard against a curve-fitted result.
    """
    ratio = (trades / n_params) if n_params else float(trades)
    if trades < 30:
        level, headline = "bad", "Too few trades to conclude anything"
    elif ratio <= 10:
        # Inclusive: 10:1 is the ratio itself cited as very likely overfitted,
        # not the last acceptable value above it.
        level, headline = "bad", "Very likely overfitted"
    elif trades < 100 or ratio < 30:
        level, headline = "warn", "Treat this as a hypothesis, not a result"
    else:
        level, headline = "ok", "Sample is large enough to read normally"
    return {
        "level": level,
        "headline": headline,
        "trades": trades,
        "free_parameters": n_params,
        "ratio": round(ratio, 1),
        "target_ratio": 30,
    }
