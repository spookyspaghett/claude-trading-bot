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

    @field_validator("max_position_usd", "stop_loss_pct", "daily_loss_limit_usd", mode="before")
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


class StrategyConfig(BaseModel):
    name: Literal["orb"]
    orb: OrbConfig


class Config(BaseModel):
    live: bool = False
    symbols: list[str] = Field(min_length=1)
    risk: RiskConfig
    strategy: StrategyConfig
    alpaca_api_key: str = Field(default="")
    alpaca_secret_key: str = Field(default="")

    @model_validator(mode="before")
    @classmethod
    def inject_env_credentials(cls, data: Any) -> Any:
        load_dotenv()
        api_key = os.environ.get("ALPACA_API_KEY", "")
        secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        if not api_key:
            raise ValueError("ALPACA_API_KEY not set in environment or .env file")
        if not secret_key:
            raise ValueError("ALPACA_SECRET_KEY not set in environment or .env file")
        data["alpaca_api_key"] = api_key
        data["alpaca_secret_key"] = secret_key
        return data


def load_config(path: Path = Path("config.yaml")) -> Config:
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Config(**raw)
