from __future__ import annotations

import asyncio
import json

import pytest

from api.routers import donchian_router as dr


@pytest.fixture(autouse=True)
def _isolate_memory(tmp_path, monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(dr, "MEMORY_DIR", tmp_path)
    return tmp_path


def _call(profile: str | None = None) -> dict:
    return asyncio.run(dr.get_donchian_state(profile))


def test_empty_when_no_state_files() -> None:
    out = _call("nope")
    assert out["positions"] == []
    assert out["queued_entries"] == {}
    assert out["queued_exits"] == []
    assert out["ran_eod_date"] == ""


def test_reads_per_profile_state_and_handoff(tmp_path) -> None:  # noqa: ANN001
    (tmp_path / "donchian_state_alpha.json").write_text(json.dumps({
        "positions": {
            "AAPL": {
                "symbol": "AAPL", "direction": "BUY", "entry_price": 200.0,
                "entry_date": "2026-06-09", "stop_price": 192.5,
                "channel_low": 180.0, "channel_high": 199.0,
                "peak_price": 201.0, "trailing_active": True,
                "qty": 10.0, "pending_exit": False,
            },
        },
    }), encoding="utf-8")
    (tmp_path / "donchian_handoff_alpha.json").write_text(json.dumps({
        "queued_entries": {"MSFT": "enter_long"},
        "queued_exits": ["NVDA"],
        "queued_date": "2026-06-09",
        "pending_reanchor": ["MSFT"],
        "ran_eod_date": "2026-06-09",
        "ran_open_date": "2026-06-09",
    }), encoding="utf-8")

    out = _call("alpha")
    assert len(out["positions"]) == 1
    pos = out["positions"][0]
    assert pos["symbol"] == "AAPL"
    assert pos["stop_price"] == 192.5
    assert pos["trailing_active"] is True
    assert out["queued_entries"] == {"MSFT": "enter_long"}
    assert out["queued_exits"] == ["NVDA"]
    assert out["ran_open_date"] == "2026-06-09"


def test_corrupt_files_return_empty(tmp_path) -> None:  # noqa: ANN001
    (tmp_path / "donchian_state_bad.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "donchian_handoff_bad.json").write_text("[]", encoding="utf-8")
    out = _call("bad")
    assert out["positions"] == []
    assert out["queued_entries"] == {}


def test_slugless_falls_back_to_shared_files(tmp_path) -> None:  # noqa: ANN001
    (tmp_path / "donchian_state.json").write_text(json.dumps({
        "positions": {"SPY": {"symbol": "SPY", "direction": "BUY",
                              "entry_price": 500.0, "entry_date": "2026-06-09",
                              "stop_price": 490.0, "channel_low": 480.0,
                              "channel_high": 499.0, "peak_price": 500.0}},
    }), encoding="utf-8")
    out = _call(None)
    assert out["positions"][0]["symbol"] == "SPY"
    # Missing optional fields fall back to defaults rather than erroring.
    assert out["positions"][0]["qty"] == 0.0
    assert out["positions"][0]["pending_exit"] is False
