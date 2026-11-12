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
    ALERT_AUTO_CREATE_THRESHOLD: int = 70

    # Redis & Caching
    REDIS_URL: str = "redis://127.0.0.1:6379/0"
    REDIS_ENABLED: bool = True

    # Initial Admin (CLI use)
    ADMIN_EMAIL: str | None = None
    ADMIN_PASSWORD: str | None = None

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        extra="ignore",
    )


INSECURE_JWT_PLACEHOLDERS = {
    "sentinelx-insecure-secret-key-change-in-production-12345",
    "secret",
    "changeme",
    "password",
    "your-secret-key",
    "jwt-secret-key",
    "default-secret-key",
}


def validate_jwt_secret(settings_obj: Settings | None = None) -> None:
    """
    Validate JWT_SECRET_KEY safety.
    When DEBUG=False (production mode), raise ValueError if:
      - secret is missing
      - length < 32 characters
      - matches a known insecure placeholder
    """
    cfg = settings_obj or settings
    secret = cfg.JWT_SECRET_KEY

    if not cfg.DEBUG:
        if not secret:
            raise ValueError("JWT_SECRET_KEY must be configured in production (DEBUG=False).")
        if len(secret) < 32:
            raise ValueError(
                f"JWT_SECRET_KEY is insecure: length ({len(secret)}) is under 32 characters."
            )
        if (
            secret.lower() in INSECURE_JWT_PLACEHOLDERS
            or "change-in-production" in secret.lower()
        ):
            raise ValueError(
                "JWT_SECRET_KEY is set to a known default placeholder. A cryptographically secure secret is required in production."
            )


settings = Settings()
