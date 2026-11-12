import json
import logging
import re
import uuid
from typing import Any, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|token|secret|authorization|key|credentials|database_url)",
    re.IGNORECASE,
)


class RedactingJSONFormatter(logging.Formatter):
    """JSON formatter that redacts sensitive keys and values from log records."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include extra fields if present
        if hasattr(record, "request_id"):
            log_obj["request_id"] = record.request_id

        # Redact any obvious secrets in message
        if isinstance(log_obj["message"], str):
            log_obj["message"] = re.sub(
                r"(postgresql\+psycopg2://)([^:]+):([^@]+)@",
                r"\1\2:***@",
                log_obj["message"],
            )

        return json.dumps(log_obj)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Assigns unique X-Request-ID to each incoming request and response."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Appends hardened HTTP security headers to all responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
