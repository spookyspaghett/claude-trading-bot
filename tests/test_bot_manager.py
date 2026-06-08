from __future__ import annotations

import pytest

from api import bot_manager as bm


@pytest.fixture(autouse=True)
def _isolate_desired(tmp_path, monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(bm, "_DESIRED_PATH", tmp_path / "running_bots.json")


def test_desired_add_remove() -> None:
    bm._add_desired("alpha")
    bm._add_desired("beta")
    assert bm._load_desired() == {"alpha", "beta"}
    bm._remove_desired("alpha")
    assert bm._load_desired() == {"beta"}


def test_status_map_flags_stopped_unexpectedly(monkeypatch) -> None:  # noqa: ANN001
    bm._save_desired({"alpha"})
    monkeypatch.setattr(bm, "is_running", lambda s: False)
    monkeypatch.setattr(bm, "get_pid", lambda s: None)

    sm = bm.status_map()
    assert sm["alpha"]["running"] is False
    assert sm["alpha"]["desired"] is True
    assert sm["alpha"]["stopped_unexpectedly"] is True


def test_relaunch_persisted_starts_only_stopped_without_killfile(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    bm._save_desired({"alpha", "beta", "gamma"})

    # alpha already running; beta has a KILL file; gamma should be relaunched.
    monkeypatch.setattr(bm, "is_running", lambda s: s == "alpha")
    monkeypatch.setattr(bm, "_kill_path", lambda s: tmp_path / f"{s}.KILL")
    (tmp_path / "beta.KILL").touch()

    started: list[str] = []

    def _fake_start(slug):  # noqa: ANN001, ANN202
        started.append(slug)
        return {"ok": True, "pid": 1234}

    monkeypatch.setattr(bm, "start", _fake_start)

    res = bm.relaunch_persisted()
    assert started == ["gamma"]            # not alpha (running), not beta (KILL)
    assert res["relaunched"] == ["gamma"]
    assert "beta" in res["skipped"]


def test_relaunch_persisted_empty_is_noop(monkeypatch) -> None:  # noqa: ANN001
    started: list[str] = []
    monkeypatch.setattr(bm, "start", lambda s: started.append(s) or {"ok": True})
    res = bm.relaunch_persisted()
    assert started == []
    assert res == {"ok": True, "relaunched": [], "skipped": []}
