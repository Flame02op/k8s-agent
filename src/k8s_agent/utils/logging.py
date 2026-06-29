"""
Structured logging configuration for the K8s Agent.

Provides both JSON-formatted logs (for production/API mode)
and human-readable logs (for interactive CLI mode).
"""

import logging
import json
import sys
from datetime import datetime, timezone
from typing import Any

from k8s_agent.config import settings


class JSONFormatter(logging.Formatter):
    """Produces structured JSON log lines for production environments."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "extra_data"):
            log_entry["data"] = record.extra_data
        return json.dumps(log_entry, default=str)


class HumanFormatter(logging.Formatter):
    """Produces human-readable log lines for CLI interactive mode."""

    LEVEL_COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelname, self.RESET)
        timestamp = datetime.now().strftime("%H:%M:%S")
        return (
            f"{color}[{timestamp}] {record.levelname:<8}{self.RESET} "
            f"{record.name}: {record.getMessage()}"
        )


def setup_logging(mode: str = "json", level: str | None = None) -> None:
    """
    Configure the root logger for the application.

    Args:
        mode: Either 'json' for structured logs or 'human' for CLI-friendly output.
        level: Log level override. Defaults to settings.log_level.
    """
    log_level = getattr(logging, (level or settings.log_level).upper(), logging.INFO)

    root_logger = logging.getLogger("k8s_agent")
    root_logger.setLevel(log_level)

    # Clear existing handlers
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(log_level)

    if mode == "json":
        console_handler.setFormatter(JSONFormatter())
    else:
        console_handler.setFormatter(HumanFormatter())

    root_logger.addHandler(console_handler)

    # File handler (if configured)
    if settings.log_file:
        file_handler = logging.FileHandler(settings.log_file)
        file_handler.setLevel(log_level)
        file_handler.setFormatter(JSONFormatter())
        root_logger.addHandler(file_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under the k8s_agent namespace."""
    return logging.getLogger(f"k8s_agent.{name}")
