from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator


class RiskConfig(BaseModel):
    max_position_usd: Decimal
    stop_loss_pct: Decimal
    daily_loss_limit_usd: Decimal
    max_open_positions: int = Field(ge=1)
    trailing_stop_pct: Decimal = Decimal("10")   # trailing stop % (10 = 10%)
    loser_cut_pct: Decimal = Decimal("7")         # cut position if unrealized loss exceeds this %

    @field_validator(
        "max_position_usd", "stop_loss_pct", "daily_loss_limit_usd",
        "trailing_stop_pct", "loser_cut_pct",
        mode="before",
    )
    @classmethod
    def coerce_decimal(cls, v: object) -> Decimal:
        return Decimal(str(v))


class OrbConfig(BaseModel):
    opening_range_minutes: int = Field(ge=1, le=60)
    entry_order_type: Literal["limit", "market"] = "limit"
    eod_exit_time: str = "15:50"

    @field_validator("eod_exit_time")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        parts = v.split(":")
        if len(parts) != 2:
            raise ValueError(f"Expected HH:MM, got: {v!r}")
        hh, mm = parts
        if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
            raise ValueError(f"Time component out of range: {v!r}")
        return v


class EmaConfig(BaseModel):
    fast_period: int = Field(ge=2, le=200, default=9)
    slow_period: int = Field(ge=3, le=500, default=21)
    entry_order_type: Literal["limit", "market"] = "market"
    eod_exit_time: str = "15:50"

    @field_validator("eod_exit_time")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        parts = v.split(":")
        if len(parts) != 2:
            raise ValueError(f"Expected HH:MM, got: {v!r}")
        hh, mm = parts
        if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
            raise ValueError(f"Time component out of range: {v!r}")
        return v


class DonchianConfig(BaseModel):
    lookback_days: int = Field(ge=2, le=200, default=40)
    trend_ma: int = Field(ge=0, le=500, default=200)
    trailing_activation_pct: float = Field(ge=0.0, le=50.0, default=1.0)
    trailing_pct: float = Field(ge=0.0, le=50.0, default=8.0)
    long_only: bool = True


class TrendSRConfig(BaseModel):
    """Trend + Support/Resistance breakout (crypto-oriented, also works on stocks)."""
    # bar_minutes: aggregate the live feed into candles of this timeframe.
    bar_minutes: int = Field(ge=1, le=1440, default=15)
    ma_fast: int = Field(ge=2, le=400, default=21)
    ma_slow: int = Field(ge=3, le=800, default=55)
    # regime_ma: only go long above this long-term MA (0 = off).
    regime_ma: int = Field(ge=0, le=1000, default=200)
    pivot_lookback: int = Field(ge=2, le=200, default=20)
    pivot_strength: int = Field(ge=1, le=20, default=3)
    atr_period: int = Field(ge=2, le=100, default=14)
    atr_mult: float = Field(ge=0.1, le=20.0, default=2.0)
    # breakout_buffer_atr: close must clear resistance by this × ATR to enter.
    breakout_buffer_atr: float = Field(ge=0.0, le=5.0, default=0.25)
    # cooldown_bars: wait N bars after an exit before re-entering.
    cooldown_bars: int = Field(ge=0, le=100, default=4)
    trailing_activation_pct: float = Field(ge=0.0, le=50.0, default=3.0)
    trailing_pct: float = Field(ge=0.0, le=50.0, default=8.0)
    long_only: bool = True


class StrategyConfig(BaseModel):
    name: Literal["orb", "ema", "donchian", "trend_sr"] = "orb"
    orb: OrbConfig = OrbConfig(opening_range_minutes=15)
    ema: EmaConfig = EmaConfig()
    donchian: DonchianConfig = DonchianConfig()
    trend_sr: TrendSRConfig = TrendSRConfig()


class AiConfig(BaseModel):
    enable_research: bool = False       # Perplexity pre-market research
    enable_claude_filter: bool = False  # Claude signal approval


class Config(BaseModel):
    live: bool = False
    asset_class: Literal["stock", "crypto"] = "stock"
    symbols: list[str] = Field(min_length=1)
    risk: RiskConfig
    strategy: StrategyConfig
    ai: AiConfig = Field(default_factory=AiConfig)
    alpaca_api_key: str = Field(default="")
    alpaca_secret_key: str = Field(default="")

    @model_validator(mode="before")
    @classmethod
    def inject_env_credentials(cls, data: Any) -> Any:
        """Resolve Alpaca credentials.

        Keys may be supplied directly (e.g. from a profile). Only fall back to
        the .env / environment when they are absent, and only raise when neither
        source provides them.
        """
        if not isinstance(data, dict):
            return data
        load_dotenv()
        api_key = data.get("alpaca_api_key") or os.environ.get("ALPACA_API_KEY", "")
        secret_key = data.get("alpaca_secret_key") or os.environ.get("ALPACA_SECRET_KEY", "")
        if not api_key:
            raise ValueError("Alpaca API key not set (profile or ALPACA_API_KEY in .env)")
        if not secret_key:
            raise ValueError("Alpaca secret key not set (profile or ALPACA_SECRET_KEY in .env)")
        data["alpaca_api_key"] = api_key
        data["alpaca_secret_key"] = secret_key
        return data


def load_config(path: Path = Path("config.yaml")) -> Config:
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Config(**raw)
