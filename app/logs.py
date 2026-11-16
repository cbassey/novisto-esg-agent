"""Structured JSON logging with bearer redaction.

Every record is one JSON object per line, to stdout and to var/log/app.jsonl
(the eval harness reads the file to assert on tool calls). A redaction filter
runs on every record, so even a careless log call cannot write a token.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import time
from typing import Any

from .auth import known_tokens
from .config import settings

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

_TOKEN_SHAPE = re.compile(r"\btok_[A-Za-z0-9_\-]+")
_BEARER_HDR = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]+")
_OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")
REDACTED = "[REDACTED]"


def redact(text: str) -> str:
    for tok in known_tokens():
        text = text.replace(tok, REDACTED)
    text = _BEARER_HDR.sub("Bearer " + REDACTED, text)
    text = _TOKEN_SHAPE.sub(REDACTED, text)
    return _OPENAI_KEY.sub(REDACTED, text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "request_id": getattr(record, "request_id", None) or request_id_var.get(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str, ensure_ascii=False))


def setup_logging() -> None:
    settings.log_file.parent.mkdir(parents=True, exist_ok=True)
    fmt = JsonFormatter()
    root = logging.getLogger()
    root.handlers.clear()
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(settings.log_file)):
        h.setFormatter(fmt)
        root.addHandler(h)
    root.setLevel(logging.INFO)
    # Third-party loggers: keep them quiet and inside our formatter.
    for name in ("httpx", "httpcore", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True


log = logging.getLogger("app")


def event(name: str, level: int = logging.INFO, **fields: Any) -> None:
    log.log(level, name, extra={"fields": fields})
