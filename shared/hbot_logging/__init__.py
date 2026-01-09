"""hbot_logging — shared logging library for h-bot services.

Public API
----------
setup_logging(service, log_dir, level)
    Configure root logger + file handlers for a service.

get_logger(name, service)
    Return a stdlib logger wired to app.log + error.log.

get_audit_logger(service)
    Return a logger that writes JSON lines to audit.log only.
"""

import logging
import os

from .handlers import app_handler, audit_handler, error_handler

__all__ = ["setup_logging", "get_logger", "get_audit_logger"]

_DEFAULT_LOG_DIR = os.environ.get("LOG_DIR", "/var/log/hbot")
_DEFAULT_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

_initialized: dict[str, bool] = {}


def setup_logging(
    service: str,
    log_dir: str | None = None,
    level: str | None = None,
) -> None:
    """Attach file handlers for *service* to the root logger (idempotent)."""
    if service in _initialized:
        return
    log_dir = log_dir or _DEFAULT_LOG_DIR
    level = level or _DEFAULT_LOG_LEVEL

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    root.addHandler(app_handler(log_dir, service))
    root.addHandler(error_handler(log_dir, service))

    _initialized[service] = True


def get_logger(name: str, service: str | None = None) -> logging.Logger:
    """Return a named logger. Calls ``setup_logging`` on first use if *service* is given."""
    if service:
        setup_logging(service)
    return logging.getLogger(name)


def get_audit_logger(service: str) -> logging.Logger:
    """Return a logger that writes *only* to audit.log (propagate=False)."""
    log_dir = _DEFAULT_LOG_DIR
    logger_name = f"hbot.audit.{service}"
    logger = logging.getLogger(logger_name)
    if not logger.handlers:
        setup_logging(service)
        logger.addHandler(audit_handler(log_dir, service))
        logger.propagate = False
        logger.setLevel(logging.DEBUG)
    return logger
