"""Daily summary dating and exit-buffer growth."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import journal as journal_mod
from journal import TradeJournal


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(journal_mod, "JOURNAL_PATH", tmp_path / "journal.jsonl")
    monkeypatch.setattr(journal_mod, "SUMMARIES_DIR", tmp_path / "summaries")
    (tmp_path / "summaries").mkdir()
    return tmp_path


def _summaries(root: Path) -> list[Path]:
    return sorted((root / "summaries").glob("*.md"))


def test_summary_is_filed_under_the_session_it_covers(_isolate) -> None:  # noqa: ANN001
    """end_of_day() runs at the START of the next trading day, so dating the
    summary by the wall clock filed every file under the following day's name —
    from day two onward each file held the previous day's trades."""
    j = TradeJournal()
    j.record_exit(symbol="AAPL", side="sell", qty=Decimal("1"),
                  price=Decimal("100"), realized_pnl=Decimal("5"), reason="x")
    # Rewrite the timestamp to yesterday, as if the day has since rolled over.
    j._today_exits[-1]["ts"] = "2026-06-10T19:55:00+00:00"

    j.write_daily_summary(Decimal("5"))

    written = _summaries(_isolate)
    assert [p.name for p in written] == ["2026-06-10.md"]
    assert "Daily Summary 2026-06-10" in written[0].read_text(encoding="utf-8")


def test_summary_falls_back_to_today_when_there_are_no_exits(_isolate) -> None:  # noqa: ANN001
    j = TradeJournal()
    j.write_daily_summary(Decimal("0"))

    written = _summaries(_isolate)
    assert len(written) == 1
    assert written[0].stem == datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


def test_exit_buffer_is_capped(_isolate) -> None:  # noqa: ANN001
    # Drained only by write_daily_summary, which a bot whose day never rolls
    # over may not reach for a long time — it used to grow for the whole
    # process lifetime.
    cap = journal_mod.MAX_TRACKED_EXITS
    j = TradeJournal()
    for _ in range(cap + 250):
        j.record_exit(symbol="BTC/USD", side="sell", qty=Decimal("1"),
                      price=Decimal("100"), realized_pnl=Decimal("0"), reason="x")

    assert len(j._today_exits) == cap


def test_summary_clears_the_buffer(_isolate) -> None:  # noqa: ANN001
    j = TradeJournal()
    j.record_exit(symbol="AAPL", side="sell", qty=Decimal("1"),
                  price=Decimal("100"), realized_pnl=Decimal("1"), reason="x")
    j.write_daily_summary(Decimal("1"))
    assert j._today_exits == []
