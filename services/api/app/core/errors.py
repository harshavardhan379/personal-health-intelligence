import logging

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from packages.observability.context import HEADER, get_request_id
from services.api.app.core.config import settings

logger = logging.getLogger("app.errors")


def _request_id(request: Request) -> str:
    """The id of this request (set by the request middleware); empty if it never ran."""
    return getattr(request.state, "request_id", None) or get_request_id() or ""


def _error_body(request: Request, code: int, message: str, **extra) -> dict:
    # request_id lets a user quote a reference that matches the server's log lines.
    return {"error": {"code": code, "message": message, "request_id": _request_id(request),
                      **extra}}


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(request, exc.status_code, exc.detail),
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_error_body(request, 422, "Invalid request", details=exc.errors()),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Only the method, the route and the error CLASS: an exception's text may hold health data.
    logger.error(
        "unhandled_error",
        extra={"method": request.method, "path": request.url.path,
               "error_type": type(exc).__name__},
        exc_info=True,
    )
    headers = {HEADER: _request_id(request)}
    # This handler runs outside the CORS middleware, so without these a browser would hide the
    # 500 and the page could only say "the server could not be reached".
    origin = request.headers.get("origin")
    if origin and origin in settings.cors_origins:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Expose-Headers"] = HEADER
        headers["Vary"] = "Origin"
    return JSONResponse(
        status_code=500,
        content=_error_body(request, 500, "Internal server error"),
        headers=headers,
    )
