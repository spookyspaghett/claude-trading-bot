from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

JOURNAL_PATH = Path("memory/trade_journal.jsonl")
SUMMARIES_DIR = Path("memory/daily_summaries")


class TradeJournal:
    def __init__(self) -> None:
        JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
        self._today_exits: list[dict] = []

    def record_entry(
        self,
        *,
        symbol: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        reason: str,
    ) -> None:
        self._append({
            "ts": _now(),
            "event": "entry",
            "symbol": symbol,
            "side": side,
            "qty": str(qty),
            "price": str(price),
            "reason": reason,
        })

    def record_exit(
        self,
        *,
        symbol: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        realized_pnl: Decimal,
        reason: str,
    ) -> None:
        record = {
            "ts": _now(),
            "event": "exit",
            "symbol": symbol,
            "side": side,
            "qty": str(qty),
            "price": str(price),
            "realized_pnl": str(realized_pnl),
            "reason": reason,
        }
        self._append(record)
        self._today_exits.append(record)

    def write_daily_summary(self, total_pnl: Decimal) -> None:
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        lines = [
            f"# Daily Summary {today}",
            "",
            f"**Total realized PnL:** ${total_pnl:.2f}",
            f"**Exit trades:** {len(self._today_exits)}",
            "",
            "## Trade Exits",
        ]
        for e in self._today_exits:
            lines.append(
                f"- {e['ts'][:19]} {e['symbol']} {e['side']} {e['qty']} @ ${e['price']}"
                f" → PnL: ${e.get('realized_pnl', 'N/A')} ({e.get('reason', '')})"
            )
        path = SUMMARIES_DIR / f"{today}.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._today_exits.clear()

    def _append(self, record: dict) -> None:
        with JOURNAL_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
