"""Configuration settings module for the ResilienceLab API service."""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration settings loaded from environment variables.

    Adheres to 12-factor application methodology. All configurations can be
    overridden using environment variables prefixed with `RESILIENCELAB_`.
    """

    model_config = SettingsConfigDict(
        env_prefix="RESILIENCELAB_",
        case_sensitive=False,
        extra="ignore",
    )

    DATABASE_URL: str = (
        "postgresql+psycopg://resiliencelab:resiliencelab@postgres:5432/resiliencelab"
    )
    APP_NAME: str = "resiliencelab-api"
    LOG_LEVEL: str = "INFO"
    WORKER_ENABLED: bool = False
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800
    REDIS_URL: str = ""


@lru_cache
def get_settings() -> Settings:
    """Provide a cached instance of application settings.

    Returns:
        Settings: The application settings instance.
    """
    return Settings()


settings: Settings = get_settings()
