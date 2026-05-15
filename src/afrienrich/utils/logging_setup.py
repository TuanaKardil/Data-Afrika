"""structlog configuration: JSON lines to data/logs/run_{timestamp}.jsonl."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import structlog


def setup_logging(log_dir: Path | None = None) -> Path:
    """Configure structlog and standard logging. Return the log file path."""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    if log_dir is None:
        log_dir = Path(__file__).parent.parent.parent.parent / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    log_file = log_dir / f"run_{timestamp}.jsonl"

    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(level)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level)

    logging.basicConfig(
        level=level,
        handlers=[file_handler, console_handler],
        format="%(message)s",
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )

    for handler in logging.root.handlers:
        handler.setFormatter(formatter)

    return log_file
