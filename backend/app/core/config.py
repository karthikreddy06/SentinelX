from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    APP_NAME: str = "SentinelX"
    DEBUG: bool = True
    DATABASE_URL: str

    # Auth & JWT Security
    JWT_SECRET_KEY: str = "sentinelx-insecure-secret-key-change-in-production-12345"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS
    CORS_ORIGINS: list[str] = ["http://127.0.0.1:8000", "http://127.0.0.1:3000"]

    # Rate Limiting
    RATE_LIMIT_LOGIN_MAX_ATTEMPTS: int = 5
    RATE_LIMIT_LOGIN_WINDOW_SECONDS: int = 300  # 5 minutes
    RATE_LIMIT_LOGIN_LOCKOUT_SECONDS: int = 900  # 15 minutes lockout

    # Fraud Engine
    FRAUD_SCORING_ENABLED: bool = True

    # Initial Admin (CLI use)
    ADMIN_EMAIL: str | None = None
    ADMIN_PASSWORD: str | None = None

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        extra="ignore",
    )


settings = Settings()
