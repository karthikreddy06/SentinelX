from fastapi import FastAPI
from sqlalchemy import text

from app.core.config import settings
from app.database.connection import engine

app = FastAPI(
    title=settings.APP_NAME,
    description="Real-Time Fraud Detection & Intelligence Platform",
    version="1.0.0"
)


@app.get("/")
def root():
    return {
        "message": "Welcome to SentinelX",
        "status": "running"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "application": settings.APP_NAME
    }


@app.get("/health/database")
def database_health():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    return {
        "database": "connected",
        "status": "healthy"
    }