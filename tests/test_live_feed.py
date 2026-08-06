"""Regressions for the dashboard's live feed staying empty on a healthy bot.

Two independent causes, both only visible after the bot has been up a while:
  • the websocket replayed only *today's* log file, and a long-running bot
    legitimately writes nothing today on a quiet session;
  • the data feed treated a clean websocket close as "still connected" and
    reconnected in a tight, unlogged loop.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import data as data_mod
from api.routers import ws


# ── websocket backfill ────────────────────────────────────────────────────────

@pytest.fixture()
def log_root(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(ws, "PROJECT_ROOT", tmp_path)
    directory = tmp_path / "logs" / "alpha"
    directory.mkdir(parents=True)
    return directory


def _write_day(directory, day, events):  # noqa: ANN001, ANN202
    lines = [f'{{"event": "{e}", "timestamp": "{day.isoformat()}T12:00:00Z"}}'
             for e in events]
    (directory / f"{day.isoformat()}.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


def test_backfill_falls_back_to_previous_days(log_root) -> None:  # noqa: ANN001
    # Bot started three days ago and has logged nothing today — the feed must
    # still show its history instead of "Waiting for bot activity…".
    _write_day(log_root, date.today() - timedelta(days=3), ["startup", "signal"])
    _write_day(log_root, date.today() - timedelta(days=1), ["fill"])

    lines = ws._backfill_lines("alpha")

    assert len(lines) == 3
    assert "startup" in lines[0]
    assert "fill" in lines[-1]          # oldest → newest


def test_backfill_is_chronological_across_days(log_root) -> None:  # noqa: ANN001
    _write_day(log_root, date.today() - timedelta(days=2), ["older"])
    _write_day(log_root, date.today(), ["newer"])

    lines = ws._backfill_lines("alpha")

    assert "older" in lines[0]
    assert "newer" in lines[1]


def test_backfill_is_capped(log_root) -> None:  # noqa: ANN001
    _write_day(log_root, date.today(), [f"e{i}" for i in range(500)])
    lines = ws._backfill_lines("alpha", limit=10)
    assert len(lines) == 10
    assert "e499" in lines[-1]          # keeps the newest, drops the oldest


def test_backfill_empty_when_no_logs(log_root) -> None:  # noqa: ANN001
    assert ws._backfill_lines("alpha") == []


# ── data feed reconnect ───────────────────────────────────────────────────────

def _cfg():  # noqa: ANN202
    return SimpleNamespace(alpaca_api_key="k", alpaca_secret_key="s",
                           asset_class="stock", symbols=["AAPL"])


@pytest.mark.asyncio
async def test_clean_stream_close_backs_off_instead_of_spinning(monkeypatch) -> None:  # noqa: ANN001
    """`_run_forever()` returning normally means the server closed the socket.

    It used to fall through to a zero-delay reconnect with `connected` still
    True, so a dead feed reported itself healthy and hammered Alpaca until it
    hit the connection limit — the bot went quiet with nothing in the log.
    """
    connects = {"n": 0}
    events: list[str] = []

    class _Stream:
        def __init__(self, *_a, **_kw) -> None:
            connects["n"] += 1
            self.stopped = False

        def subscribe_bars(self, _handler, *_symbols) -> None:  # noqa: ANN001
            pass

        def stop(self) -> None:
            self.stopped = True

        async def _run_forever(self) -> None:
            return          # clean close, immediately

    monkeypatch.setattr(data_mod, "StockDataStream", _Stream)
    monkeypatch.setattr(data_mod, "log_error", lambda e, **kw: events.append(e))
    monkeypatch.setattr(data_mod, "log_info", lambda e, **kw: events.append(e))

    feed = data_mod.DataFeed(_cfg())
    task = asyncio.create_task(feed.run())
    await asyncio.sleep(0.05)           # plenty of time to spin, if it were going to
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert connects["n"] == 1, "reconnected with no backoff — busy loop"
    assert feed.connected is False, "clean close left the feed reporting connected"
    assert "data_feed_disconnected" in events


@pytest.mark.asyncio
async def test_stream_error_is_logged(monkeypatch) -> None:  # noqa: ANN001
    events: list[str] = []

    class _Stream:
        def __init__(self, *_a, **_kw) -> None:
            pass

        def subscribe_bars(self, _handler, *_symbols) -> None:  # noqa: ANN001
            raise RuntimeError("connection limit exceeded")

        def stop(self) -> None:
            pass

    monkeypatch.setattr(data_mod, "StockDataStream", _Stream)
    monkeypatch.setattr(data_mod, "log_error", lambda e, **kw: events.append(e))
    monkeypatch.setattr(data_mod, "log_info", lambda e, **kw: events.append(e))

    feed = data_mod.DataFeed(_cfg())
    task = asyncio.create_task(feed.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert "data_feed_error" in events   # was swallowed silently
    assert feed.connected is False
