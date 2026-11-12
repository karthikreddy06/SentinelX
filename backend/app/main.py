from contextlib import asynccontextmanager
import logging
from pathlib import Path
import sys

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.v1 import api_router
from app.core.config import settings, validate_jwt_secret
from app.core.middleware import (
    RedactingJSONFormatter,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
)
from app.core import kafka as kafka_core
from app.core import redis as redis_core
from app.database.connection import engine

# Configure Structured JSON Logging
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(RedactingJSONFormatter())
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO if not settings.DEBUG else logging.DEBUG)
# Remove default handlers to avoid duplicate output
root_logger.handlers = [handler]

logger = logging.getLogger("sentinelx.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Enforce JWT Secret safety at startup in production
    validate_jwt_secret(settings)
    yield
    redis_core.close_redis_connection()
    kafka_core.close_kafka_producer()


app = FastAPI(
    title=settings.APP_NAME,
    description="Real-Time Fraud Detection & Intelligence Platform",
    version="1.0.0",
    lifespan=lifespan,
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

# Mount Static Frontend
frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

    @app.get("/dashboard", response_class=FileResponse)
    def dashboard():
        return FileResponse(frontend_dir / "index.html")


@app.get("/")
def root(request: Request):
    accept = request.headers.get("accept", "")
    if "text/html" in accept and frontend_dir.exists():
        return FileResponse(frontend_dir / "index.html")
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


@app.get("/health/redis")
def redis_health():
    status_str, details = redis_core.check_redis_health()
    status_code = (
        status.HTTP_200_OK
        if status_str in ("ok", "disabled")
        else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return JSONResponse(
        status_code=status_code,
        content={
            "redis": status_str,
            "details": details,
        },
    )


@app.get("/health/streaming")
def streaming_health():
    status_str, details = kafka_core.check_kafka_health()
    status_code = (
        status.HTTP_200_OK
        if status_str in ("ok", "disabled")
        else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return JSONResponse(
        status_code=status_code,
        content={
            "streaming": status_str,
            "details": details,
        },
    )

