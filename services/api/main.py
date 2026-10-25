import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from packages.observability.context import HEADER
from packages.observability.middleware import RequestContextMiddleware
from packages.schemas.db import SessionLocal
from services.api.app.api.v1 import (
    account,
    assistant,
    auth,
    documents,
    health,
    metrics,
    review,
    search,
    timeline,
    trends,
)
from services.api.app.core.config import settings
from services.api.app.core.errors import (
    http_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from services.api.app.core.logging import configure_logging
from services.api.app.core.pipeline import recover_stale_documents

configure_logging(settings.log_level)

logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """On start, documents a previous run left `processing` are marked failed (YGH-95). An
    unreachable database must not stop the API from starting: health checks still need to answer."""
    try:
        with SessionLocal() as db:
            recover_stale_documents(db)
    except Exception as exc:  # noqa: BLE001 - startup must survive; the list endpoint retries
        logger.warning("startup_recovery_skipped", extra={"error_type": type(exc).__name__})
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[HEADER],  # so the web app can show the request id of a failed call
)
# Added last so it is outermost: it sees every request, including CORS preflights and the errors.
app.add_middleware(RequestContextMiddleware)

app.add_exception_handler(StarletteHTTPException, http_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

app.include_router(health.router, prefix="/api/v1", tags=["health"])
app.include_router(auth.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(review.router, prefix="/api/v1")
app.include_router(timeline.router, prefix="/api/v1")
app.include_router(trends.router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")
app.include_router(assistant.router, prefix="/api/v1")
app.include_router(account.router, prefix="/api/v1")
app.include_router(metrics.router, prefix="/api/v1")
