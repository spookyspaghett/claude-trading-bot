# Risk model

## The change in one line

Risk used to be an **output** of the bot — you set a position size, and whatever
the stop distance happened to be decided how much you lost when wrong. It is now
an **input**: you set what you are willing to lose, and size is solved from it.

```
qty = (equity × risk_per_trade_pct%) ÷ |entry − stop|
```

Everything else in this document exists to bound that number at the portfolio
level or to shrink it when the account is losing.

## Why the old model was a problem

Under flat notional sizing every position was `max_position_usd / price`, so
dollar risk varied with whatever stop the strategy produced:

| Strategy   | Stop                          | Risk on a $50k position |
|------------|-------------------------------|-------------------------|
| ORB / EMA  | `stop_loss_pct` = 1%          | $500                    |
| Donchian   | ATR × 1.5, typically 3–6%     | $1,500 – $3,000         |
| trend_sr   | `max(support, entry − ATR×2)` | varies per setup        |

With `daily_loss_limit_usd: 500`, one ORB stop-out consumed the entire daily
budget exactly, while one Donchian stop-out could breach it 3–6× before the
limit could react. The two settings were not describing the same system.

Measured on synthetic data, flat sizing produced a **2.6× spread** in dollars
risked per trade. Risk-based sizing flattens that to 1.0× while letting notional
vary with volatility, which is the correct way round.

## Settings

All live under `risk:` in `config.yaml` (and in each `profiles/<slug>.yaml`).
Every one defaults to off, so a config without them behaves exactly as before.

### Per-trade sizing

- **`risk_per_trade_pct`** (0.75) — percent of equity risked between entry and
  stop. `0` restores flat `max_position_usd` sizing.

Size is the tightest of three ceilings: this risk budget, `max_position_usd`,
and unused buying power. Risk-based sizing needs both a stop and a known
account equity; without either it falls back to notional sizing silently, which
is why every runner must call `set_account_equity`.

### Portfolio caps

- **`max_portfolio_heat_pct`** (4.0) — ceiling on total open risk,
  `Σ |entry − stop| × qty`, as a percent of equity. This is the number that
  bounds a bad week. `max_open_positions` only counts positions, and a position
  is not a unit of risk.
- **`max_group_heat_pct`** (3.0) + **`correlation_groups`** — the same cap
  within a cluster of names that move together. SPY, AAPL, MSFT and NVDA are
  close to one bet; sizing them as four independent ones is how a book that
  looks diversified takes a single concentrated loss.
- **`max_gross_exposure_pct`** (100) — gross notional ceiling. 100 = never
  borrow.

Open risk is recorded at entry and **not** revised as stops trail up, so the
figure is *initial* risk. That overstates true exposure on a winning position,
which is the conservative direction.

### Drawdown throttle

- **`derisk_start_dd_pct`** (8) — below this, full size.
- **`halt_dd_pct`** (20) — no new positions at or beyond this.
- **`min_risk_scale`** (0.35) — floor for the taper between the two.

Between the two thresholds, per-trade risk scales down linearly. Betting a
constant fraction of a shrinking account is what turns a bad run into an
unrecoverable one, well before the strategy is actually broken.

The throttle **never force-liquidates**. Halting new risk is a decision you can
reverse; dumping the book at the bottom of a drawdown realises exactly the loss
the throttle exists to avoid. Only the kill switch and the daily loss limit
flatten.

- **`daily_loss_limit_pct`** (2.0) — daily limit as a percent of equity, applied
  together with `daily_loss_limit_usd` by taking whichever is **tighter**.
  Enabling it can only ever reduce risk.

## Reading a backtest

`_compute_stats` now reports the numbers that decide whether a result is worth
acting on, not just whether it made money.

- **Expectancy (R)** — average P&L per trade in units of the risk it was opened
  with. This is the honest measure of edge: it is independent of position size,
  so it stays comparable across symbols, settings and account sizes. If sizing
  changes and expectancy doesn't, the edge is unchanged and only the dollar
  translation moved.
- **Max drawdown %** and **duration** — depth gets the attention, but duration
  is what decides whether a strategy gets abandoned mid-run.
- **Sortino** — Sharpe penalises upside volatility as if it were risk, which is
  precisely backwards for trend following. Sortino uses downside deviation only.
- **Calmar** — CAGR ÷ max drawdown.
- **Monte Carlo** — the realised equity curve is *one ordering* of the trades.
  Resampling 1,000 times shows how deep a drawdown a merely unlucky run
  produces. **Size against the p95 figure, not against the one drawdown that
  happened.** Caveat: bootstrapping assumes trades are independent; trend
  systems cluster their wins, so read these as a floor on the risk, not a
  ceiling.

## Small accounts

Risk-based sizing plus whole-share rounding can legitimately produce **zero
shares** — when one share would risk more than the budget allows, not trading is
the correct answer. Taking one share anyway would risk several times the
intended amount.

Because that looks identical to a silent lockout, the binding constraint is now
named in the logs (`describe_size_limit`) and `basket_test.py` prints an
explicit "sizing floor, not a verdict" message instead of concluding the
strategy has no edge.

If you hit this: raise equity, raise `risk_per_trade_pct`, or trade an
instrument that supports fractional size (crypto already does).

## Bug fixed along the way

Any account smaller than `max_position_usd` could previously hold exactly **one**
position, regardless of `max_open_positions`. The pre-trade check projected
`max_position_usd × position_count` against equity while sizing clamped the
budget to equity, so the second position always projected 2× equity and was
refused. Exposure is now summed from actual open notional, and with risk sizing
active the check sizes *into* the remaining headroom rather than rejecting.
