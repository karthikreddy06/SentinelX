import logging
import sys

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.v1 import api_router
from app.core.config import settings
from app.core.middleware import (
    RedactingJSONFormatter,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
)
from app.database.connection import engine

# Configure Structured JSON Logging
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(RedactingJSONFormatter())
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO if not settings.DEBUG else logging.DEBUG)
# Remove default handlers to avoid duplicate output
root_logger.handlers = [handler]

logger = logging.getLogger("sentinelx.app")

app = FastAPI(
    title=settings.APP_NAME,
    description="Real-Time Fraud Detection & Intelligence Platform",
    version="1.0.0",
)

# Hardening Middlewares
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Unhandled Exception Handler (Never Leaks Internal Stack Traces)
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    logger.error(
        f"Unhandled exception on {request.method} {request.url.path}: {exc}",
        extra={"request_id": request_id},
        exc_info=settings.DEBUG,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal server error occurred. Please contact system support.",
            "request_id": request_id,
        },
    )

app.include_router(api_router, prefix="/api/v1")


@app.get("/")
def root():
    return {
        "message": "Welcome to SentinelX",
        "status": "running",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "application": settings.APP_NAME,
    }


@app.get("/health/database")
def database_health():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    return {
        "database": "connected",
        "status": "healthy",
    }
