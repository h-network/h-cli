"""Log formatters for h-bot.

PlainFormatter: pipe-delimited plain text for app.log / error.log
AuditFormatter: JSON lines for audit.log
"""

import json
import logging
from datetime import datetime, timezone


class PlainFormatter(logging.Formatter):
    """Pipe-delimited plain text: timestamp | level | name | message"""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        msg = record.getMessage()
        base = f"{ts} | {record.levelname:<8} | {record.name} | {msg}"
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            base += "\n" + record.exc_text
        if record.stack_info:
            base += "\n" + record.stack_info
        return base


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
