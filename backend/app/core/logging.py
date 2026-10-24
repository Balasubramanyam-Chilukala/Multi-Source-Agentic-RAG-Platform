"""Structured logging with structlog.

All log events pass through ``redact_sensitive`` before rendering, so API keys, auth headers,
passwords, and connection strings are masked even if a caller logs them by mistake.
Request-scoped values (``request_id``) are bound via structlog contextvars by the middleware.
"""

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

REDACTED = "[REDACTED]"

_SENSITIVE_KEY = re.compile(
    r"(api[_-]?key|authorization|password|passwd|secret|token|cookie|database_url|dsn)",
    re.IGNORECASE,
)
# Values that look like credentials regardless of the key they are logged under.
_SENSITIVE_VALUE = re.compile(
    r"(gsk_[A-Za-z0-9]{20,}|nvapi-[A-Za-z0-9_\-]{20,}|lsv2_[A-Za-z0-9_]{20,}"
    r"|Bearer\s+[A-Za-z0-9._\-]+|://[^/\s:@]+:[^@\s]+@"
    r"|eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]+"  # bare JWTs
    r"|sk-[A-Za-z0-9_\-]{20,})"
)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _SENSITIVE_VALUE.sub(REDACTED, value)
    if isinstance(value, Mapping):
        return {
            k: REDACTED if _SENSITIVE_KEY.search(str(k)) else _redact_value(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(_redact_value(v) for v in value)
    return value


def redact_sensitive(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        if _SENSITIVE_KEY.search(key):
            event_dict[key] = REDACTED
        else:
            event_dict[key] = _redact_value(event_dict[key])
    return event_dict


def configure_logging(level: str = "INFO", *, json_logs: bool = True) -> None:
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
        redact_sensitive,
    ]
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Route stdlib loggers (uvicorn, sqlalchemy, alembic) through the same pipeline.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # Uvicorn's access log is replaced by our request middleware, which includes request_id.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
