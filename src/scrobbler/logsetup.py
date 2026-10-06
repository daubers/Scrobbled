"""Logging set-up shared by the API and the worker (`LOG_LEVEL`, `LOG_FORMAT`).

`text` is human-readable; `json` is one object per line for log aggregators. Fields passed
as `extra=` to a log call (e.g. `task`, `duration_ms`) appear as keys in the JSON output.
"""

import json
import logging
import sys
from datetime import UTC, datetime

TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

# Attributes every LogRecord has; anything else on a record came from `extra=`.
_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}
_HANDLER_FLAG = "_scrobbler_handler"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        entry.update((k, v) for k, v in record.__dict__.items() if k not in _STANDARD)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Point the root logger at stderr. Safe to call repeatedly (it replaces its own
    handler); an unknown level falls back to INFO."""
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _HANDLER_FLAG, False)]:
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    setattr(handler, _HANDLER_FLAG, True)
    handler.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter(TEXT_FORMAT))
    root.addHandler(handler)
    resolved = logging.getLevelName(str(level).upper())
    root.setLevel(resolved if isinstance(resolved, int) else logging.INFO)
