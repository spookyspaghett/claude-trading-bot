# Trend/SR Entry Filters — ADX & Volume Confirmation

Two optional entry filters for the **Trend + Support/Resistance** (`trend_sr`)
strategy. Both are **off by default** and apply to live trading *and* the
backtest, because both run the exact same strategy core
(`TrendSRStrategy._evaluate` in `strategy.py`).

The goal of both filters is the same: **trade fewer, higher-quality breakouts.**
A raw breakout strategy fires on every close that pokes above resistance —
including the low-conviction pokes that immediately fail. These filters demand
extra evidence before committing capital.

---

## TL;DR

| Filter | Config key | Off value | Typical on value | What it does |
|--------|-----------|-----------|------------------|--------------|
| ADX trend-strength gate | `min_adx` | `0` | `20`–`25` | Skip breakouts when there's no real trend (choppy market) |
| Volume confirmation | `volume_mult` | `0` | `1.2`–`1.5` | Skip breakouts that aren't backed by above-average volume |

Both are exposed in three places:

- **Backtest UI** — the *Min ADX* and *Volume ×* boxes in the Trend/SR row.
- **`config.yaml`** — under `strategy.trend_sr` (for the live bot).
- **Profiles** — same keys inside a profile's `strategy.trend_sr` block.

Leave them at `0` and the strategy behaves exactly as it did before these
filters existed — there is no silent behaviour change.

---

## 1. ADX trend-strength gate (`min_adx`)

**ADX (Average Directional Index)** measures *how strong* a trend is, regardless
of its direction. It ranges 0–100:

- **ADX < 20** → no real trend; the market is ranging/choppy.
- **ADX 20–25** → a trend is establishing.
- **ADX > 25** → a strong, established trend.

The strategy already has a *direction* filter (the regime MA — only go long
above it). ADX adds a *strength* filter on top: even above the regime MA, don't
buy a breakout unless the move has genuine momentum behind it.

### How it's computed

Standard Wilder ADX over `adx_period` bars (default 14):

1. Per bar, compute True Range, +DM (directional movement up), −DM (down).
2. Wilder-smooth each over `adx_period`.
3. `+DI = 100 × smoothed(+DM) / smoothed(TR)`, likewise `−DI`.
4. `DX = 100 × |+DI − −DI| / (+DI + −DI)`.
5. `ADX = Wilder average of DX`.

Implemented in `TrendSRStrategy._adx()`. It needs `2 × adx_period + 1` bars of
history before it returns a value; until then the gate blocks entries (no trade
on insufficient data).

### Setting it

- `min_adx: 0` → disabled.
- `min_adx: 20` → moderate filtering; a good starting point.
- `min_adx: 25` → only strong trends.
- Higher → progressively fewer, stronger-trend trades.

---

## 2. Volume confirmation (`volume_mult`)

Real breakouts are accompanied by a surge in participation. A breakout on thin
volume is far more likely to be a fake-out. This filter requires the breakout
bar's volume to clear a multiple of its own recent average:

```
breakout_volume ≥ volume_mult × average_volume(last volume_ma bars)
```

Implemented via `TrendSRStrategy._avg_volume()` (default `volume_ma = 20`).

### Setting it

- `volume_mult: 0` → disabled.
- `volume_mult: 1.2` → breakout volume must be 20 % above average.
- `volume_mult: 1.5` → 50 % above average (stricter).

### Important caveats

- **No volume data → filter is skipped, not blocking.** If the feed/file has no
  volume column, `_avg_volume()` returns `None` and the gate passes everything,
  so you never accidentally block all trades.
- **Crypto volume is partial.** Alpaca reports volume from its own venues only;
  crypto liquidity is fragmented across exchanges. The signal is still useful but
  noisier than for equities.
- **Daily data is cleaner than 1-minute** for this filter.

---

## 3. The fresh-breakout rule (why these are real filters, not delays)

This is the subtle but critical part.

The strategy's breakout condition (`close > resistance + buffer`) stays true for
*many consecutive bars* once price is extended above the level. A naive filter
that simply rejects an entry on a failing bar doesn't actually filter anything —
it just **delays** the entry to a later bar where the condition passes. For ADX
specifically, ADX *rises* during a breakout, so a delayed entry always
eventually fires, only **later and at a worse price**. That's a regression.

This was caught during development with a threshold sweep: trade count stayed
identical across every ADX threshold while entry *timestamps* shifted later.

The fix: **when a filter is active, an entry fires only on the bar where price
first crosses the level** (a "fresh" breakout). If a filter rejects that bar, the
trade is skipped outright — there is no second attempt until price falls back and
crosses again. State flags `was_above_res` / `was_below_sup` track this.

This is scoped to activate *only when a filter is enabled*. With both filters off
(`min_adx = 0` and `volume_mult = 0`), the original "enter on any bar beyond the
level" behaviour is preserved byte-for-byte.

### Evidence

Threshold sweep on a synthetic choppy-but-rising series (filters as true filters):

```
baseline (filters off):  19 trades
min_adx = 15:            12 trades
min_adx = 20:            11 trades
min_adx = 25:            10 trades
min_adx = 35:             9 trades
min_adx = 50:             7 trades
```

Monotonic reduction = genuine filtering. (Before the fresh-breakout fix the count
stayed flat at 19 — proof the gate was only delaying.)

---

## 4. How to use it (recommended workflow)

1. **Backtest the baseline first.** Run `trend_sr` with both filters at `0` on a
   daily CSV (Stooq links are in the Backtest panel). Note the trade count, win
   rate, profit factor, and max drawdown.
2. **Add ONE filter at a time.** Set `min_adx = 20`, re-run, compare. Then try
   `25`. A good filter should *raise win rate / profit factor* even though it
   *lowers* total trades and total P&L. If it lowers everything proportionally,
   it isn't adding value on that symbol.
3. **Try volume confirmation** the same way (`volume_mult = 1.2`, then `1.5`).
4. **Combine** only if each helped on its own.
5. **Promote to live** by copying the winning values into `strategy.trend_sr` in
   `config.yaml` (or the relevant profile), then paper-trade before anything else.

### What "better" looks like

You are trading off *quantity* for *quality*. Expect:

- ✅ Higher win rate and/or profit factor.
- ✅ Smaller max drawdown (fewer junk trades).
- ⬇️ Fewer total trades and lower gross P&L (this is expected and fine).
- ❌ If win rate *drops* when you add a filter, that filter is hurting on that
  symbol/timeframe — turn it back off.

---

## 5. Config reference

```yaml
strategy:
  name: trend_sr
  trend_sr:
    # ... core params (bar_minutes, ma_fast, ma_slow, regime_ma, pivots, atr) ...

    # Optional entry filters — 0 disables each.
    min_adx: 0.0       # ADX ≥ this to enter (20–25 typical)
    adx_period: 14     # ADX smoothing window
    volume_mult: 0.0   # breakout volume ≥ this × average (1.2–1.5 typical)
    volume_ma: 20      # average-volume lookback
```

Backtest upload (multipart form) accepts the same as form fields:
`min_adx`, `adx_period`, `volume_mult`, `volume_ma`.

---

## 6. Where it lives in the code

| Concern | Location |
|---------|----------|
| Filter logic + fresh-breakout gating | `strategy.py` → `TrendSRStrategy._evaluate`, `_adx`, `_avg_volume` |
| Config fields + validation | `config_loader.py` → `TrendSRConfig` |
| Backtest plumbing | `backtest.py` → `_run_with_trend_sr`, `_run_sync_from_bars`, `run_backtest_from_file` |
| HTTP form params | `api/routers/backtest_router.py` → `/backtest/upload` |
| UI inputs | `ui/src/components/BacktestPanel.tsx` (Trend/SR strategy row) |
