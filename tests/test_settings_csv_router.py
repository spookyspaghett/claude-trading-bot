"""The CSV settings endpoints, exercised through the real FastAPI app."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient

import profiles
from api.main import app

_CREDS = {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}

_PROFILE: dict[str, Any] = {
    "name": "Router Test",
    "asset_class": "stock",
    "live": False,
    "symbols": ["SPY", "AAPL"],
    "risk": {
        "max_position_usd": 50000,
        "stop_loss_pct": 1.0,
        "daily_loss_limit_usd": 500,
        "max_open_positions": 4,
    },
    "strategy": {"name": "orb"},
    "ai": {"enable_research": False, "enable_claude_filter": False},
    "alpaca_api_key": "k",
    "alpaca_secret_key": "s",
}


@pytest.fixture
def client(tmp_path: Path) -> Any:
    """Point the profile store at a temp dir so tests never touch real profiles."""
    (tmp_path / "rt.yaml").write_text(yaml.dump(_PROFILE), encoding="utf-8")
    with patch.dict(os.environ, _CREDS), \
         patch.object(profiles, "PROFILES_DIR", tmp_path), \
         patch.object(profiles, "ACTIVE_FILE", tmp_path / "active.txt"), \
         patch.object(profiles, "LEGACY_CONFIG", tmp_path / "nope.yaml"):
        yield TestClient(app)


def _saved(tmp_path: Path) -> dict[str, Any]:
    return yaml.safe_load((tmp_path / "rt.yaml").read_text(encoding="utf-8"))


def _upload(client: Any, text: str, *, apply: bool = False) -> Any:
    return client.post(
        "/api/profiles/rt/settings/import",
        files={"file": ("settings.csv", text.encode("utf-8"), "text/csv")},
        data={"apply": str(apply).lower()},
    )


# ── Export ────────────────────────────────────────────────────────────────────

def test_download_returns_a_csv_attachment(client: Any) -> None:
    res = client.get("/api/profiles/rt/settings.csv")
    assert res.status_code == 200
    assert "text/csv" in res.headers["content-type"]
    assert "rt-settings.csv" in res.headers["content-disposition"]
    assert "risk.max_position_usd,50000" in res.text


def test_download_of_a_missing_profile_is_404(client: Any) -> None:
    res = client.get("/api/profiles/nope/settings.csv")
    assert res.status_code == 404


# ── Import ────────────────────────────────────────────────────────────────────

def test_preview_reports_the_diff_without_writing(client: Any, tmp_path: Path) -> None:
    res = _upload(client, "key,value\nrisk.max_open_positions,8\n")
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is False
    assert body["changes"] == [
        {"path": "risk.max_open_positions", "old": 4, "new": 8}
    ]
    assert _saved(tmp_path)["risk"]["max_open_positions"] == 4


def test_apply_writes_the_profile(client: Any, tmp_path: Path) -> None:
    res = _upload(client, "key,value\nrisk.max_open_positions,8\n", apply=True)
    assert res.status_code == 200
    assert res.json()["applied"] is True
    assert _saved(tmp_path)["risk"]["max_open_positions"] == 8


def test_apply_preserves_untouched_settings(client: Any, tmp_path: Path) -> None:
    _upload(client, "key,value\nrisk.max_open_positions,8\n", apply=True)
    saved = _saved(tmp_path)
    assert saved["symbols"] == ["SPY", "AAPL"]
    assert saved["strategy"]["name"] == "orb"
    assert saved["alpaca_api_key"] == "k"
    assert saved["live"] is False


def test_bad_csv_returns_every_error_at_once(client: Any) -> None:
    res = _upload(client, "key,value\nlive,true\nbogus.key,1\n")
    assert res.status_code == 422
    errors = res.json()["detail"]["errors"]
    assert len(errors) == 2
    assert any("live cannot be set" in e for e in errors)


def test_a_rejected_import_writes_nothing(client: Any, tmp_path: Path) -> None:
    _upload(client, "key,value\nrisk.max_open_positions,8\nbogus,1\n", apply=True)
    assert _saved(tmp_path)["risk"]["max_open_positions"] == 4


def test_oversized_upload_is_refused_by_size_not_by_parsing(client: Any) -> None:
    res = _upload(client, "key,value\n" + "# padding\n" * 200_000)
    assert res.status_code == 413


def test_watchlist_upload_sets_symbols(client: Any, tmp_path: Path) -> None:
    res = _upload(client, "symbol\nNVDA\nAMD\n", apply=True)
    assert res.status_code == 200
    assert res.json()["mode"] == "symbols"
    assert _saved(tmp_path)["symbols"] == ["NVDA", "AMD"]


def test_round_trip_through_both_endpoints(client: Any, tmp_path: Path) -> None:
    text = client.get("/api/profiles/rt/settings.csv").text
    res = _upload(client, text, apply=True)
    assert res.status_code == 200
    assert res.json()["changes"] == []
