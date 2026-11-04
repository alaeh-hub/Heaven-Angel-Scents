"""Log formatting: readable colour text for development, JSON lines for production.

Every record is stamped with the current request's ID so a log search (or a
support report quoting the X-Request-ID header) can find all of one request's lines.
"""
import json
import logging
import re

from flask import g, has_request_context

# A client-supplied X-Request-ID is only reused when it looks sane.
REQUEST_ID_OK = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class _ColorFormatter(logging.Formatter):
    _COLORS = {
        logging.DEBUG: "\x1b[36m",
        logging.INFO: "\x1b[32m",
        logging.WARNING: "\x1b[33m",
        logging.ERROR: "\x1b[31m",
        logging.CRITICAL: "\x1b[1;31m",
    }
    _RESET = "\x1b[0m"

    def __init__(self, stream):
        super().__init__(
            "[%(asctime)s] %(levelname)s in %(module)s: %(message)s")
        self._use_color = stream.isatty()

    def format(self, record):
        message = super().format(record)
        if not self._use_color:
            return message
        color = self._COLORS.get(record.levelno, self._RESET)
        return f"{color}{message}{self._RESET}"


class _RequestIdFilter(logging.Filter):
    """Stamps every record with the current request's ID ("-" outside a request)."""

    def filter(self, record):
        record.request_id = g.get("request_id", "-") if has_request_context() else "-"
        return True


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, so a log search can filter by level or request_id."""

    def format(self, record):
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "module": record.module,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(app, json_logs):
    for logger_name in (app.logger.name, "werkzeug"):
        logger = logging.getLogger(logger_name)
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.addFilter(_RequestIdFilter())
                handler.setFormatter(
                    _JsonFormatter() if json_logs else _ColorFormatter(handler.stream))
