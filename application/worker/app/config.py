"""Configuration settings for the ResilienceLab Worker service."""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    """Worker configuration using Pydantic BaseSettings following 12-factor principles."""

    model_config = SettingsConfigDict(
        env_prefix="RESILIENCELAB_WORKER_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+psycopg://resiliencelab:resiliencelab@postgres:5432/resiliencelab",
        validation_alias=AliasChoices(
            "RESILIENCELAB_WORKER_DATABASE_URL",
            "RESILIENCELAB_DATABASE_URL",
            "DATABASE_URL",
        ),
        description="PostgreSQL connection string for the worker",
    )

    poll_interval_seconds: float = Field(
        default=5.0,
        validation_alias=AliasChoices(
            "RESILIENCELAB_WORKER_POLL_INTERVAL_SECONDS",
            "POLL_INTERVAL_SECONDS",
        ),
        description="Interval in seconds between database polling cycles",
        ge=0.1,
    )

    batch_size: int = Field(
        default=10,
        validation_alias=AliasChoices(
            "RESILIENCELAB_WORKER_BATCH_SIZE",
            "BATCH_SIZE",
        ),
        description="Maximum number of pending orders to process per cycle",
        gt=0,
    )

    processing_delay_seconds: float = Field(
        default=2.0,
        validation_alias=AliasChoices(
            "RESILIENCELAB_WORKER_PROCESSING_DELAY_SECONDS",
            "PROCESSING_DELAY_SECONDS",
        ),
        description="Simulated work delay in seconds per batch",
        ge=0.0,
    )

    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices(
            "RESILIENCELAB_WORKER_LOG_LEVEL",
            "LOG_LEVEL",
        ),
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )

    queue_backend: Literal["postgres", "redis"] = Field(
        default="postgres",
        validation_alias=AliasChoices(
            "RESILIENCELAB_WORKER_QUEUE_BACKEND",
            "QUEUE_BACKEND",
        ),
        description="Queue backend mechanism: 'postgres' or 'redis'",
    )

    metrics_port: int = Field(
        default=8001,
        validation_alias=AliasChoices(
            "RESILIENCELAB_WORKER_METRICS_PORT",
            "METRICS_PORT",
        ),
        description="HTTP port for exposing Prometheus metrics",
        gt=0,
        lt=65536,
    )

    # Database connection pool settings for production-quality operation
    db_pool_size: int = Field(
        default=5,
        validation_alias=AliasChoices(
            "RESILIENCELAB_WORKER_DB_POOL_SIZE",
            "DB_POOL_SIZE",
        ),
        description="Number of connections to keep in the connection pool",
    )

    db_max_overflow: int = Field(
        default=5,
        validation_alias=AliasChoices(
            "RESILIENCELAB_WORKER_DB_MAX_OVERFLOW",
            "DB_MAX_OVERFLOW",
        ),
        description="Max overflow connections beyond pool_size",
    )

    db_pool_timeout: int = Field(
        default=30,
        validation_alias=AliasChoices(
            "RESILIENCELAB_WORKER_DB_POOL_TIMEOUT",
            "DB_POOL_TIMEOUT",
        ),
        description="Seconds to wait before giving up on obtaining a connection",
    )

    db_pool_recycle: int = Field(
        default=1800,
        validation_alias=AliasChoices(
            "RESILIENCELAB_WORKER_DB_POOL_RECYCLE",
            "DB_POOL_RECYCLE",
        ),
        description="Recycle connections after this many seconds",
    )


@lru_cache
def get_settings() -> WorkerSettings:
    """Return cached worker settings instance."""
    return WorkerSettings()
