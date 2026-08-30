"""Saved-run history and the backtest settings loader endpoints."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient

import profiles
from api.main import app
from api.routers import backtest_runs_router as runs_mod

_CREDS = {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}

_PROFILE: dict[str, Any] = {
    "name": "BT", "asset_class": "stock", "live": False, "symbols": ["QQQ"],
    "risk": {
        "max_position_usd": 25000, "stop_loss_pct": 2.0,
        "daily_loss_limit_usd": 4000, "max_open_positions": 8,
    },
    "strategy": {
        "name": "trend_sr",
        "trend_sr": {"ma_fast": 34, "ma_slow": 89, "min_adx": 22.0, "atr_mult": 2.5},
    },
    "ai": {}, "alpaca_api_key": "k", "alpaca_secret_key": "s",
}


def _report(stem: str, *, trades: int, params: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "symbol": "QQQ", "start_date": "2025-01-01", "end_date": "2025-06-01",
        "strategy_used": "trend_sr",
        "created_at": f"2026-08-{stem[6:8]}T10:00:00+00:00",
        "equity_curve": [], "trades": [],
        "params": params or {},
        "stats": {
            "total_trades": trades, "win_rate": 55.0, "total_return_pct": 12.0,
            "max_drawdown_pct": 8.0, "expectancy_r": 0.25, "profit_factor": 1.4,
        },
    }


@pytest.fixture
def client(tmp_path: Path) -> Any:
    reports = tmp_path / "reports"
    reports.mkdir()
    (tmp_path / "bt.yaml").write_text(yaml.dump(_PROFILE), encoding="utf-8")
    with patch.dict(os.environ, _CREDS), \
         patch.object(runs_mod, "REPORTS_DIR", reports), \
         patch.object(profiles, "PROFILES_DIR", tmp_path), \
         patch.object(profiles, "ACTIVE_FILE", tmp_path / "active.txt"), \
         patch.object(profiles, "LEGACY_CONFIG", tmp_path / "nope.yaml"):
        (tmp_path / "active.txt").write_text("bt", encoding="utf-8")
        yield TestClient(app), reports


def _write(reports: Path, stem: str, **kw: Any) -> None:
    (reports / f"{stem}.json").write_text(
        json.dumps(_report(stem, **kw)), encoding="utf-8"
    )


# ── History ───────────────────────────────────────────────────────────────────

def test_runs_list_is_newest_first(client: Any) -> None:
    api, reports = client
    _write(reports, "20260801_100000_QQQ", trades=40, params={"strategy": "trend_sr"})
    _write(reports, "20260815_100000_QQQ", trades=60, params={"strategy": "trend_sr"})
    rows = api.get("/api/backtest/runs").json()
    assert [r["id"] for r in rows] == [
        "20260815_100000_QQQ", "20260801_100000_QQQ",
    ]


def test_empty_history_is_an_empty_list_not_an_error(client: Any) -> None:
    api, _ = client
    assert api.get("/api/backtest/runs").json() == []


def test_run_carries_a_sample_verdict_when_params_were_recorded(client: Any) -> None:
    api, reports = client
    _write(reports, "20260810_100000_QQQ", trades=40,
           params={"strategy": "trend_sr", "min_adx": 22.0, "ma_fast": 21})
    row = api.get("/api/backtest/runs").json()[0]
    assert row["has_params"] is True
    assert row["sample"]["level"] in ("ok", "warn", "bad")
    assert row["sample"]["free_parameters"] > 0


def test_legacy_report_without_params_is_listed_not_hidden(client: Any) -> None:
    """A run you can see but can't fully explain beats one you can't see."""
    api, reports = client
    _write(reports, "20260805_100000_QQQ", trades=25, params=None)
    row = api.get("/api/backtest/runs").json()[0]
    assert row["has_params"] is False
    assert row["sample"] is None
    assert row["stats"]["total_trades"] == 25


def test_a_corrupt_report_does_not_hide_the_others(client: Any) -> None:
    api, reports = client
    _write(reports, "20260801_100000_QQQ", trades=40, params={"strategy": "auto"})
    (reports / "20260802_broken.json").write_text("{not json", encoding="utf-8")
    rows = api.get("/api/backtest/runs").json()
    assert [r["id"] for r in rows] == ["20260801_100000_QQQ"]


def test_get_and_delete_a_run(client: Any) -> None:
    api, reports = client
    _write(reports, "20260801_100000_QQQ", trades=40, params={"strategy": "auto"})
    assert api.get("/api/backtest/runs/20260801_100000_QQQ").status_code == 200
    assert api.delete("/api/backtest/runs/20260801_100000_QQQ").status_code == 200
    assert api.get("/api/backtest/runs").json() == []


def test_missing_run_is_404(client: Any) -> None:
    api, _ = client
    assert api.get("/api/backtest/runs/nope").status_code == 404


@pytest.mark.parametrize("bad", ["../secrets", "a/b", "a\\b"])
def test_run_id_cannot_escape_the_reports_directory(client: Any, bad: str) -> None:
    api, _ = client
    assert api.get(f"/api/backtest/runs/{bad}").status_code in (400, 404)


# ── Loading settings ──────────────────────────────────────────────────────────

def test_params_from_profile(client: Any) -> None:
    api, _ = client
    body = api.get("/api/backtest/params/from-profile/bt").json()
    assert body["params"]["strategy"] == "trend_sr"
    assert body["params"]["ma_fast"] == 34
    assert body["params"]["min_adx"] == 22.0
    assert body["params"]["symbol"] == "QQQ"


def test_params_from_missing_profile_is_404(client: Any) -> None:
    api, _ = client
    assert api.get("/api/backtest/params/from-profile/nope").status_code == 404


def _upload(api: Any, text: str) -> Any:
    return api.post(
        "/api/backtest/params/from-csv",
        files={"file": ("settings.csv", text.encode("utf-8"), "text/csv")},
        data={"profile": "bt"},
    )


def test_params_from_csv(client: Any) -> None:
    api, _ = client
    res = _upload(api, "key,value\nstrategy.trend_sr.min_adx,25\n")
    assert res.status_code == 200
    assert res.json()["params"]["min_adx"] == 25.0


def test_csv_errors_come_back_as_a_list(client: Any) -> None:
    api, _ = client
    res = _upload(api, "key,value\nlive,true\nbogus.key,1\n")
    assert res.status_code == 422
    assert len(res.json()["detail"]["errors"]) == 2


def test_a_bars_file_uploaded_here_is_refused_with_a_pointer(client: Any) -> None:
    """The panel has two CSV inputs; confusing them is the obvious mistake."""
    api, _ = client
    res = _upload(api, "date,open,high,low,close\n" + "x" * 1_100_000)
    assert res.status_code == 413
    assert "data upload" in res.json()["detail"]
