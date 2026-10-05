from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.api.app.api.v1 import auth, documents, health, review, search, timeline, trends
from services.api.app.core.config import settings
from services.api.app.core.errors import (
    http_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from services.api.app.core.logging import configure_logging

configure_logging(settings.log_level)

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
