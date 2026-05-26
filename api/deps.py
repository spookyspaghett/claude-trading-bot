from __future__ import annotations

from pathlib import Path

from alpaca.trading.client import TradingClient

# Project root is one level up from this file.
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

_client: TradingClient | None = None


def get_trading_client() -> TradingClient:
    global _client
    if _client is None:
        import sys
        sys.path.insert(0, str(PROJECT_ROOT))
        from config_loader import load_config
        cfg = load_config(CONFIG_PATH)
        _client = TradingClient(
            api_key=cfg.alpaca_api_key,
            secret_key=cfg.alpaca_secret_key,
            paper=not cfg.live,
        )
    return _client


def reset_client() -> None:
    """Call after the config changes so the next request rebuilds the client."""
    global _client
    _client = None
