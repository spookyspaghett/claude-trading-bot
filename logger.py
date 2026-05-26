from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any, TextIO

import structlog


def setup_logging(log_dir: Path = Path("logs")) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{date.today().isoformat()}.jsonl"
    log_fh: TextIO = open(log_file, "a", encoding="utf-8")  # noqa: SIM115

    processors: list[Any] = [
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.ExceptionRenderer(),
        structlog.processors.JSONRenderer(),
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=log_fh),
        cache_logger_on_first_use=True,
    )


_log: structlog.stdlib.BoundLogger = structlog.get_logger()  # type: ignore[assignment]


def log_signal(symbol: str, direction: str, price: str, reason: str) -> None:
    _log.info("signal", symbol=symbol, direction=direction, price=price, reason=reason)


def log_order(
    order_id: str,
    symbol: str,
    side: str,
    qty: str,
    price: str,
    order_type: str,
) -> None:
    _log.info(
        "order_submitted",
        order_id=order_id,
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        order_type=order_type,
    )


def log_fill(order_id: str, symbol: str, filled_qty: str, filled_avg_price: str) -> None:
    _log.info(
        "fill",
        order_id=order_id,
        symbol=symbol,
        filled_qty=filled_qty,
        filled_avg_price=filled_avg_price,
    )


def log_rejection(order_id: str, symbol: str, reason: str) -> None:
    _log.warning("order_rejected", order_id=order_id, symbol=symbol, reason=reason)


def log_error(event: str, **kwargs: Any) -> None:
    _log.error(event, **kwargs)


def log_info(event: str, **kwargs: Any) -> None:
    _log.info(event, **kwargs)
