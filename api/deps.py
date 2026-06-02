from __future__ import annotations

import sys
from pathlib import Path

from alpaca.trading.client import TradingClient

# Project root is one level up from this file.
PROJECT_ROOT = Path(__file__).parent.parent

# Cache one TradingClient per profile slug. The empty-string key holds the
# client for the active profile (used when no slug is supplied).
_clients: dict[str, TradingClient] = {}


def get_trading_client(slug: str | None = None) -> TradingClient:
    key = slug or ""
    client = _clients.get(key)
    if client is None:
        sys.path.insert(0, str(PROJECT_ROOT))
        from config_loader import Config
        from profiles import load_active_config, load_profile

        if slug:
            data = load_profile(slug)
            cfg = Config(**{k: v for k, v in data.items() if k != "name"})
        else:
            cfg = load_active_config()

        client = TradingClient(
            api_key=cfg.alpaca_api_key,
            secret_key=cfg.alpaca_secret_key,
            paper=not cfg.live,
        )
        _clients[key] = client
    return client


def reset_client(slug: str | None = None) -> None:
    """Drop a cached client so the next request rebuilds it.

    Pass a slug to reset just that profile, or None to clear everything.
    """
    if slug is None:
        _clients.clear()
    else:
        _clients.pop(slug, None)
        _clients.pop("", None)  # the active-profile client may now be stale too
