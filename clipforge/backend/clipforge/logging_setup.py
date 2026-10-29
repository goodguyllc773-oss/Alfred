"""Structured JSON-lines logging to ``<data dir>/logs/clipforge.log`` (rotated)
plus a short human format on the console. Never log API keys or tokens."""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
             "level": record.levelname, "logger": record.name, "msg": record.getMessage(),
             "thread": record.threadName}
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d, ensure_ascii=False)


def setup_logging(logs_dir: Path, level: str = "INFO") -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("clipforge")
    root.setLevel(level)
    if any(getattr(h, "_clipforge", False) for h in root.handlers):
        return
    fh = logging.handlers.RotatingFileHandler(logs_dir / "clipforge.log", maxBytes=5_000_000, backupCount=3,
                                              encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    fh._clipforge = True  # type: ignore[attr-defined]
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    ch._clipforge = True  # type: ignore[attr-defined]
    root.addHandler(fh)
    root.addHandler(ch)
