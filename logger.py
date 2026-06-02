from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any, TextIO

import structlog


class _DailyRotatingFile:
    """File-like object that rotates to a new .jsonl file each calendar day."""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir
        self._date: date | None = None
        self._fh: TextIO | None = None

    def _rotate(self) -> None:
        today = date.today()
        if today != self._date:
            if self._fh is not None:
                try:
                    self._fh.close()
                except OSError:
                    pass
            self._fh = open(  # noqa: SIM115
                self._log_dir / f"{today.isoformat()}.jsonl", "a", encoding="utf-8"
            )
            self._date = today

    def write(self, s: str) -> int:
        self._rotate()
        assert self._fh is not None
        return self._fh.write(s)

    def flush(self) -> None:
        if self._fh is not None:
            self._fh.flush()


def setup_logging(log_dir: Path = Path("logs")) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_fh: Any = _DailyRotatingFile(log_dir)

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
