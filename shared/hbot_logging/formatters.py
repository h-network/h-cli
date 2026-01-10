"""Log formatters for h-cli.

AppFormatter:   JSON lines for app.log / error.log
AuditFormatter: JSON lines for audit.log (captures extra fields)
"""

import json
import logging
import traceback
from datetime import datetime, timezone


class AppFormatter(logging.Formatter):
    """JSON-lines formatter for app.log and error.log.

    Output: {"timestamp", "level", "logger", "message", "traceback"?}
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        payload: dict = {
            "timestamp": ts,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["traceback"] = self.formatException(record.exc_info)
            payload["exception"] = str(record.exc_info[1])
        if record.stack_info:
            payload["stack_info"] = record.stack_info
        return json.dumps(payload, default=str)


class AuditFormatter(logging.Formatter):
    """JSON-lines formatter for audit entries.

    Captures all ``extra`` fields passed to the log call.
    """

    # Fields that belong to the standard LogRecord (skip when serialising extras)
    _BUILTIN = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)))

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        payload: dict = {"timestamp": ts, "level": record.levelname}
        # Collect any extra fields the caller passed
        for key, val in vars(record).items():
            if key not in self._BUILTIN and key != "message":
                payload[key] = val
        return json.dumps(payload, default=str)
