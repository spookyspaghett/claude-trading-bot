"""Broker call timeouts.

alpaca-py's RESTClient builds its request options without a `timeout`, so
`requests` blocks until the OS gives up on the socket — effectively never for a
half-open connection. The bot awaits these calls inline on its main loop, so one
stalled connection froze everything: no kill-switch poll, no bar processing, no
shutdown on SIGINT, and nothing in the log to say why.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

import broker as broker_mod


def _broker() -> broker_mod.BrokerClient:
    cfg = SimpleNamespace(live=False, alpaca_api_key="k", alpaca_secret_key="s",
                          asset_class="stock")
    return broker_mod.BrokerClient(cfg)


@pytest.mark.asyncio
async def test_broker_call_times_out_instead_of_hanging(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(broker_mod, "REQUEST_TIMEOUT", 0.1)
    b = _broker()

    def _hangs() -> None:
        time.sleep(5)

    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(broker_mod.BrokerTimeoutError):
        await b._call(_hangs)

    assert loop.time() - started < 1.0, "the event loop was held hostage"


def test_broker_timeout_is_catchable_as_a_normal_broker_error() -> None:
    # Every call site uses `except Exception` — the timeout must flow through
    # them as an ordinary transient failure, not escape as an unhandled type.
    assert issubclass(broker_mod.BrokerTimeoutError, RuntimeError)


@pytest.mark.asyncio
async def test_broker_call_returns_normally_under_the_timeout(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(broker_mod, "REQUEST_TIMEOUT", 5.0)
    b = _broker()
    assert await b._call(lambda: "ok") == "ok"


@pytest.mark.asyncio
async def test_broker_call_passes_arguments_through(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(broker_mod, "REQUEST_TIMEOUT", 5.0)
    b = _broker()
    assert await b._call(lambda a, k=0: (a, k), 1, k=2) == (1, 2)
