"""
MERIDIAN Logging Configuration.

Provides structured logging setup for the application.
Explicitly avoids logging secrets, connection strings, or sensitive data.
"""

import logging
import sys
from typing import Optional

from app.core.config import settings


# List of sensitive keywords that should never appear in logs
SENSITIVE_KEYWORDS = [
    "secret",
    "password",
    "api_key",
    "token",
    "auth",
    "credential",
    "ciphertext",
    "private_key",
]


def sanitize_log_message(message: str) -> str:
    """Sanitize log messages to prevent accidental secret leakage."""
    lower_message = message.lower()
    for keyword in SENSITIVE_KEYWORDS:
        if keyword in lower_message:
            return "[REDACTED: Potential sensitive data]"
    return message


class SensitiveDataFilter(logging.Filter):
    """Logging filter that redacts sensitive information."""

    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "msg") and isinstance(record.msg, str):
            record.msg = sanitize_log_message(record.msg)
        return True


def setup_logging(log_level: Optional[str] = None) -> None:
    """
    Configure application-wide logging.

    Args:
        log_level: Override log level. Defaults to settings.LOG_LEVEL.
    """
    level = (log_level or settings.LOG_LEVEL).upper()
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

    if level not in valid_levels:
        level = "INFO"

    # Create root logger for the app
    logger = logging.getLogger("meridian")
    logger.setLevel(getattr(logging, level))

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, level))

    # Formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(formatter)

    # Add sensitive data filter
    console_handler.addFilter(SensitiveDataFilter())

    # Avoid duplicate handlers
    if not logger.handlers:
        logger.addHandler(console_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    logger.info("Logging configured at %s level", level)


def get_logger(name: str) -> logging.Logger:
    """
    Get a named logger instance under the meridian namespace.

    Args:
        name: The logger name (typically __name__).

    Returns:
        A configured logging.Logger instance.
    """
    return logging.getLogger(f"meridian.{name}")
