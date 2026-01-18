"""Rotating file handler factories for h-cli logging."""

import os
from logging.handlers import RotatingFileHandler

from .formatters import AppFormatter, AuditFormatter

# 10 MB per file, 5 backups
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5


def _make_handler(
    path: str, formatter: object, level: int
) -> RotatingFileHandler:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handler = RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT
    )
    handler.setFormatter(formatter)
    handler.setLevel(level)
    return handler


def app_handler(log_dir: str, service: str) -> RotatingFileHandler:
    """Handler for app.log (INFO+ plain text)."""
    import logging

    return _make_handler(
        os.path.join(log_dir, service, "app.log"),
        AppFormatter(),
        logging.DEBUG,
    )


def error_handler(log_dir: str, service: str) -> RotatingFileHandler:
    """Handler for error.log (WARNING+ plain text)."""
    import logging

    return _make_handler(
        os.path.join(log_dir, service, "error.log"),
        AppFormatter(),
        logging.WARNING,
    )


def audit_handler(log_dir: str, service: str) -> RotatingFileHandler:
    """Handler for audit.log (all levels, JSON lines)."""
    import logging

    return _make_handler(
        os.path.join(log_dir, service, "audit.log"),
        AuditFormatter(),
        logging.DEBUG,
    )
