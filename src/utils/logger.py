import logging
import sys
from pathlib import Path
from typing import Optional
from src.utils.paths import resolve_path, ensure_dir

_LOGGER_INITIALIZED = False


def setup_logger(
    level: str = "INFO",
    log_dir: Optional[str | Path] = None,
    log_file_name: str = "supervisor_bridge.log",
    to_console: bool = True,
) -> logging.Logger:
    """
    Configures and returns the root logger for SupervisorBridge.
    Does NOT mutate disk state on module import.
    """
    global _LOGGER_INITIALIZED
    logger = logging.getLogger("SupervisorBridge")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates
    if logger.hasHandlers():
        logger.handlers.clear()

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] [%(filename)s:%(lineno)d] - %(message)s"
    )

    if to_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    if log_dir:
        resolved_log_dir = ensure_dir(log_dir)
        file_handler = logging.FileHandler(
            resolved_log_dir / log_file_name, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    _LOGGER_INITIALIZED = True
    return logger


def get_logger() -> logging.Logger:
    """Returns the logger instance. Default fallback if not setup."""
    logger = logging.getLogger("SupervisorBridge")
    if not logger.handlers:
        # Fallback console logger without creating files
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] - %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
