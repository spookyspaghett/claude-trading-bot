#!/usr/bin/env python3
"""Run one backtest configuration across several symbols and compare.

A single-symbol backtest can't tell you whether a strategy works or whether you
found a configuration that happens to fit one instrument's history. Running the
SAME settings across a basket separates those: an edge should show up broadly,
noise shows up once.

The settings below are deliberately fixed in one place and applied to every
symbol. Change them per-symbol and the comparison stops meaning anything.

Usage:
    python basket_test.py data/*.csv
    python basket_test.py --trailing-pct 0 data/*.csv

Each CSV needs Date,Open,High,Low,Close,Volume daily bars; the symbol is taken
from the filename (SLV-daily.csv -> SLV).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="+", help="daily-bar CSV/XLSX files")
    p.add_argument("--equity", type=float, default=200.0)
    p.add_argument("--position-size", type=float, default=150.0)
    p.add_argument("--lookback", type=int, default=40)
    p.add_argument("--exit-lookback", type=int, default=20)
    p.add_argument("--trend-ma", type=int, default=200)
    p.add_argument("--atr-period", type=int, default=14)
    p.add_argument("--atr-multiplier", type=float, default=2.5)
    p.add_argument("--trailing-activation-pct", type=float, default=1.0)
    p.add_argument("--trailing-pct", type=float, default=8.0,
                   help="0 disables the trail so the channel/ATR stop exits")
    p.add_argument("--slippage-bps", type=float, default=5.0)
    p.add_argument("--commission", type=float, default=0.0)
    return p


async def main() -> int:
    args = build_parser().parse_args()

    from backtest import parse_bars_from_bytes, run_backtest_from_file
    from config_loader import load_config

    cfg = load_config(PROJECT_ROOT / "config.yaml")
    risk = cfg.risk.model_copy(update={
        "max_position_usd": Decimal(str(args.position_size)),
    })

    rows: list[tuple[str, object]] = []
    exits: dict[str, int] = {}

    for path_s in args.files:
        path = Path(path_s)
        symbol = path.stem.split("-")[0].split("_")[0].upper()
        try:
            bars = parse_bars_from_bytes(path.read_bytes(), path.name, symbol)
            result = await run_backtest_from_file(
                symbol=symbol,
                bars=bars,
                orb_config=cfg.strategy.orb,
                risk_config=risk,
                lookback=args.lookback,
                exit_lookback=args.exit_lookback,
                trend_ma=args.trend_ma,
                long_only=True,
                use_atr_stop=True,
                atr_period=args.atr_period,
                atr_multiplier=args.atr_multiplier,
                fast_ma=0,
                volume_filter_days=0,
                trailing_activation_pct=args.trailing_activation_pct,
                trailing_pct=args.trailing_pct,
                starting_equity=Decimal(str(args.equity)),
                slippage_bps=args.slippage_bps,
                commission=args.commission,
                strategy="auto",
            )
        except Exception as exc:
            print(f"{symbol:<8} SKIPPED: {exc}", file=sys.stderr)
            continue

        rows.append((symbol, result))
        for t in result.trades:
            exits[t.exit_reason] = exits.get(t.exit_reason, 0) + 1

    if not rows:
        print("No symbol produced a result.", file=sys.stderr)
        return 1

    print(f"\nEquity {args.equity:g} | position {args.position_size:g} | "
          f"{args.lookback}-in/{args.exit_lookback}-out | MA{args.trend_ma} | "
          f"ATR{args.atr_period}x{args.atr_multiplier:g} | "
          f"trail {args.trailing_activation_pct:g}%/{args.trailing_pct:g}% | "
          f"{args.slippage_bps:g}bps\n")

    hdr = (f"{'SYMBOL':<8}{'TRADES':>7}{'WIN%':>7}{'PF':>7}"
           f"{'P&L':>10}{'FINAL':>10}{'MAXDD':>9}{'SHARPE':>8}")
    print(hdr)
    print("-" * len(hdr))

    wins = 0
    for symbol, r in rows:
        final = float(args.equity) + float(r.stats.total_pnl)
        if r.stats.total_pnl > 0:
            wins += 1
        # BacktestStats.win_rate is a fraction; only the API layer scales it.
        print(f"{symbol:<8}{r.stats.total_trades:>7}{r.stats.win_rate * 100:>7.1f}"
              f"{r.stats.profit_factor:>7.2f}{float(r.stats.total_pnl):>10.2f}"
              f"{final:>10.2f}{float(r.stats.max_drawdown):>9.2f}"
              f"{r.stats.sharpe_ratio:>8.2f}")

    print("-" * len(hdr))
    print(f"{wins}/{len(rows)} symbols profitable")
    print("exit reasons: " + ", ".join(f"{k}={v}" for k, v in sorted(exits.items())))

    # The honest read, stated up front so it isn't rationalised afterwards.
    if wins <= len(rows) // 3:
        print("\n=> Negative across most of the basket. That's an answer: this "
              "configuration has no broad edge. Tuning it per-symbol from here "
              "is fitting noise.")
    elif wins >= (len(rows) * 2) // 3:
        print("\n=> Positive across most of the basket - the first result worth "
              "taking seriously, because the settings were not tuned per symbol. "
              "Paper trade it before funding it.")
    else:
        print("\n=> Mixed. Too weak to act on: a real edge should not depend on "
              "which symbol you happened to pick.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
