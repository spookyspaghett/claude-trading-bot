from __future__ import annotations

import asyncio
import socket
from typing import Any

import main


def test_await_dns_returns_immediately_when_resolvable(monkeypatch: Any) -> None:
    calls: list[str] = []
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, *a, **k: calls.append(host) or [])
    logged: list[str] = []
    monkeypatch.setattr(main, "log_error", lambda e, **k: logged.append(e))

    assert asyncio.run(main._await_dns(["a.example", "b.example"])) is True
    assert calls == ["a.example", "b.example"]
    assert logged == []          # nothing announced on the happy path


def test_await_dns_waits_then_succeeds(monkeypatch: Any) -> None:
    """The reboot case: resolution fails, then starts working."""
    attempts = {"n": 0}

    def flaky(host: str, *a: Any, **k: Any) -> list[Any]:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise socket.gaierror(-3, "Temporary failure in name resolution")
        return []

    monkeypatch.setattr(socket, "getaddrinfo", flaky)
    monkeypatch.setattr(main.asyncio, "sleep", _no_sleep)
    errors: list[str] = []
    infos: list[str] = []
    monkeypatch.setattr(main, "log_error", lambda e, **k: errors.append(e))
    monkeypatch.setattr(main, "log_info", lambda e, **k: infos.append(e))

    assert asyncio.run(main._await_dns(["alpaca.example"])) is True
    assert errors == ["dns_unavailable"]      # announced exactly once, not per retry
    assert infos == ["dns_ready"]


def test_await_dns_gives_up_and_lets_the_bot_start(monkeypatch: Any) -> None:
    def always_fail(*a: Any, **k: Any) -> list[Any]:
        raise socket.gaierror(-3, "Temporary failure in name resolution")

    monkeypatch.setattr(socket, "getaddrinfo", always_fail)
    monkeypatch.setattr(main.asyncio, "sleep", _no_sleep)
    errors: list[str] = []
    monkeypatch.setattr(main, "log_error", lambda e, **k: errors.append(e))

    assert asyncio.run(main._await_dns(["alpaca.example"], timeout=0.05)) is False
    assert errors == ["dns_unavailable", "dns_preflight_gave_up"]


async def _no_sleep(_seconds: float) -> None:
    """Keep the backoff logic under test without paying its wall-clock cost."""
    return None
