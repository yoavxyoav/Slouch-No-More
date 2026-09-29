"""JSON-lines logger writing to <project>/logs/ and stderr."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# ~/.posture-guard so logs survive app-bundle rebuilds and are easy to find
LOG_DIR = Path.home() / ".posture-guard" / "logs"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("posture_guard")
    if logger.handlers:
        return logger
    logger.setLevel(level)

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(stream)

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(LOG_DIR / "posture-guard.log")
        file_handler.setFormatter(JsonFormatter())
        logger.addHandler(file_handler)
    except OSError:
        logger.warning("could not open log file; logging to stderr only")
    return logger
