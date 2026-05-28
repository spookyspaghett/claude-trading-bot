from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

_API_KEY: str = os.environ.get("PERPLEXITY_API_KEY", "")
_URL = "https://api.perplexity.ai/chat/completions"
_MODEL = "sonar"
RESEARCH_LOG = Path("memory/research_log.md")


@dataclass
class SymbolResearch:
    symbol: str
    score: int       # 1–10, higher = better trading candidate today
    sentiment: str   # "bullish" | "bearish" | "neutral"
    catalysts: str
    risks: str
    summary: str


class PremarketResearch:
    def __init__(self) -> None:
        RESEARCH_LOG.parent.mkdir(parents=True, exist_ok=True)

    def enabled(self) -> bool:
        return bool(_API_KEY)

    async def run(self, symbols: list[str]) -> dict[str, SymbolResearch]:
        if not self.enabled():
            return {}
        results: dict[str, SymbolResearch] = {}
        async with httpx.AsyncClient(timeout=30.0) as client:
            for symbol in symbols:
                r = await self._query(client, symbol)
                if r is not None:
                    results[symbol] = r
        if results:
            self._write_log(results)
        return results

    async def _query(
        self, client: httpx.AsyncClient, symbol: str
    ) -> SymbolResearch | None:
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        prompt = (
            f"Analyze {symbol} stock for today {today}. "
            "Provide sentiment (bullish/bearish/neutral), "
            "key catalysts today (news/earnings/macro), "
            "key risks, "
            "a score 1-10 for trading opportunity today (10=best), "
            "and a one-sentence summary. "
            'Reply with JSON only: {"sentiment":"...","catalysts":"...","risks":"...","score":N,"summary":"..."}'
        )
        try:
            resp = await client.post(
                _URL,
                headers={"Authorization": f"Bearer {_API_KEY}"},
                json={
                    "model": _MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                },
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return None
            data = json.loads(match.group())
            return SymbolResearch(
                symbol=symbol,
                score=int(data.get("score", 5)),
                sentiment=str(data.get("sentiment", "neutral")),
                catalysts=str(data.get("catalysts", "")),
                risks=str(data.get("risks", "")),
                summary=str(data.get("summary", "")),
            )
        except Exception:
            return None

    def _write_log(self, results: dict[str, SymbolResearch]) -> None:
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [f"\n# Pre-market Research — {ts}\n"]
        for r in results.values():
            lines += [
                f"## {r.symbol} — {r.score}/10 ({r.sentiment})",
                f"**Summary:** {r.summary}",
                f"**Catalysts:** {r.catalysts}",
                f"**Risks:** {r.risks}",
                "",
            ]
        with RESEARCH_LOG.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines))
