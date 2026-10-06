import json
import logging

import pytest

from scrobbler.logsetup import JsonFormatter, configure_logging


@pytest.fixture(autouse=True)
def restore_root_logger():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def _record(msg="hello %s", args=("world",), **extra):
    record = logging.LogRecord("test.logger", logging.INFO, __file__, 1, msg, args, None)
    record.__dict__.update(extra)
    return record


def test_json_formatter_emits_fields_and_extras():
    entry = json.loads(JsonFormatter().format(_record(task="art", duration_ms=12.5)))
    assert entry["message"] == "hello world"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "test.logger"
    assert entry["task"] == "art"
    assert entry["duration_ms"] == 12.5
    assert "time" in entry


def test_json_formatter_includes_exceptions():
    try:
        raise ValueError("boom")
    except ValueError:
        record = _record()
        import sys

        record.exc_info = sys.exc_info()
    assert "ValueError: boom" in json.loads(JsonFormatter().format(record))["exception"]


def test_configure_logging_is_idempotent_and_sets_level():
    root = logging.getLogger()
    configure_logging("DEBUG", "json")
    configure_logging("warning", "text")
    ours = [h for h in root.handlers if getattr(h, "_scrobbler_handler", False)]
    assert len(ours) == 1
    assert root.level == logging.WARNING


def test_unknown_level_falls_back_to_info():
    configure_logging("LOUD", "text")
    assert logging.getLogger().level == logging.INFO
