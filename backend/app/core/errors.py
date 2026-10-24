"""Application errors and the consistent JSON error envelope.

Every error response has the shape::

    {"error": {"code": "...", "message": "...", "request_id": "...", "details": [...]?}}

Unexpected exceptions are logged server-side with their traceback; clients only ever receive a
generic message plus the request ID they can quote when reporting a problem.
"""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger
from app.core.middleware import REQUEST_ID_HEADER

logger = get_logger("atlas.errors")


class AppError(Exception):
    status_code: int = 500
    code: str = "internal_error"
    retry_after: int | None = None

    def __init__(self, message: str, *, details: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"


class AuthenticationError(AppError):
    status_code = 401
    code = "unauthenticated"


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"


class UnprocessableError(AppError):
    status_code = 422
    code = "unprocessable"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "permission_denied"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    body: dict[str, Any] = {"code": code, "message": message, "request_id": request_id}
    if details:
        body["details"] = details
    headers = dict(headers or {})
    if request_id:
        headers[REQUEST_ID_HEADER] = request_id
    return JSONResponse(status_code=status_code, content={"error": body}, headers=headers)


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):
        return await _handle_unexpected(request, exc)
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after is not None else None
    return error_response(request, exc.status_code, exc.code, exc.message, exc.details, headers)


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        return await _handle_unexpected(request, exc)
    try:
        phrase = HTTPStatus(exc.status_code).phrase
    except ValueError:
        phrase = "HTTP error"
    code = phrase.lower().replace(" ", "_")
    message = exc.detail if isinstance(exc.detail, str) else phrase
    return error_response(request, exc.status_code, code, message)


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        return await _handle_unexpected(request, exc)
    # Drop the submitted input from the details so request bodies are not echoed back.
    details = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]
    return error_response(request, 422, "validation_error", "Request validation failed", details)


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled_exception",
        request_id=_request_id(request),
        path=request.url.path,
        error_type=type(exc).__name__,
        exc_info=exc,
    )
    return error_response(request, 500, "internal_error", "An unexpected error occurred")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected)
