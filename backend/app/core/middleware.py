"""Request correlation and access logging as a pure ASGI middleware.

A pure ASGI implementation (rather than ``BaseHTTPMiddleware``) keeps streaming responses
such as SSE working and preserves contextvars for the downstream handler.
"""

import json
import re
import time
import uuid
from http import HTTPStatus

import structlog
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import get_logger

REQUEST_ID_HEADER = "X-Request-ID"
# Accept client-supplied IDs only if they are short and benign, to avoid log injection.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")

logger = get_logger("atlas.access")

# Conservative defaults for a JSON API (the SPA is served separately by its own host).
SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cross-Origin-Resource-Policy", "same-site"),
)


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name.lower() == REQUEST_ID_HEADER.lower().encode():
            candidate = value.decode("latin-1")
            return candidate if _VALID_REQUEST_ID.match(candidate) else None
    return None


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status_code = 500
        started = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                for name, value in SECURITY_HEADERS:
                    headers.setdefault(name, value)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            logger.info(
                "http_request",
                method=scope["method"],
                path=scope["path"],
                status_code=status_code,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            structlog.contextvars.clear_contextvars()


class BodySizeLimitMiddleware:
    """Reject request bodies above ``max_bytes`` before they are buffered or spooled to disk.

    Checks ``Content-Length`` up front and also counts streamed bytes, which covers chunked
    uploads that omit the header.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length" and value.isdigit() and int(value) > self.max_bytes:
                await _send_too_large(scope, send)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # An HTTPException passes through FastAPI's body parsing unchanged and is
                    # rendered by the standard error envelope handler.
                    raise HTTPException(413, "Request body is too large")
            return message

        await self.app(scope, limited_receive, send)


async def _send_too_large(scope: Scope, send: Send) -> None:
    request_id = scope.get("state", {}).get("request_id")
    # Same code the error-envelope handler derives from the status phrase.
    code = HTTPStatus(413).phrase.lower().replace(" ", "_")
    body = json.dumps(
        {"error": {"code": code, "message": "Request body is too large", "request_id": request_id}}
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
