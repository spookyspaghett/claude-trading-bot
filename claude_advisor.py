from __future__ import annotations

import json
import os
import re
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()

_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
_MODEL = "claude-opus-4-7"

try:
    import anthropic as _anthropic
    _CLIENT: _anthropic.Anthropic | None = (
        _anthropic.Anthropic(api_key=_API_KEY) if _API_KEY else None
    )
except ImportError:
    _CLIENT = None


class SignalAdvisor:
    """Uses Claude to approve or veto trading signals before execution."""

    def enabled(self) -> bool:
        return _CLIENT is not None

    async def approve(
        self,
        *,
        symbol: str,
        direction: str,
        entry_price: Decimal,
        signal_reason: str,
        research_summary: str = "",
        daily_pnl: Decimal = Decimal("0"),
    ) -> tuple[bool, str]:
        if not self.enabled():
            return True, "AI filter disabled — ANTHROPIC_API_KEY not set"
        import asyncio
        return await asyncio.to_thread(
            self._call,
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            signal_reason=signal_reason,
            research_summary=research_summary,
            daily_pnl=daily_pnl,
        )

    def _call(
        self,
        *,
        symbol: str,
        direction: str,
        entry_price: Decimal,
        signal_reason: str,
        research_summary: str,
        daily_pnl: Decimal,
    ) -> tuple[bool, str]:
        assert _CLIENT is not None
        prompt = (
            "You are a second-opinion system for a quant trading bot.\n\n"
            f"Signal: {direction} {symbol} @ ${entry_price}\n"
            f"Reason: {signal_reason}\n"
            f"Today's realized PnL so far: ${daily_pnl:.2f}\n"
        )
        if research_summary:
            prompt += f"Pre-market research: {research_summary}\n"
        prompt += (
            "\nApprove or reject this trade. Only reject if there is a strong reason "
            "(very bearish news, extreme risk, daily loss already steep). "
            "Default to approving valid technical signals.\n"
            'Reply with JSON only: {"approved": true/false, "reasoning": "one sentence"}'
        )
        try:
            msg = _CLIENT.messages.create(
                model=_MODEL,
                max_tokens=128,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return True, "parse error — approved by default"
            data = json.loads(match.group())
            return bool(data.get("approved", True)), str(data.get("reasoning", ""))
        except Exception as exc:
            return True, f"Claude error — approved by default: {exc}"
