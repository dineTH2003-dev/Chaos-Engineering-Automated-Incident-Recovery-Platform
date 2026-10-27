"""
ResilienceLab — Incident Response Platform Configuration
12-factor configuration with environment variable overrides and sensible defaults.
"""

import os
from typing import Set
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class IncidentSettings(BaseSettings):
    """Central configuration for incident engine, policies, and integrations."""

    model_config = SettingsConfigDict(
        env_prefix="RESILIENCELAB_INCIDENT_",
        env_file=".env",
        extra="ignore",
    )

    APP_NAME: str = "resiliencelab-incident-engine"
    VERSION: str = "1.0.0"
    LOG_LEVEL: str = Field(default="INFO")

    # Database connection (supports in-cluster and local port-forwarded access)
    DATABASE_URL: str = Field(
        default=os.getenv(
            "RESILIENCELAB_DATABASE_URL",
            "postgresql+psycopg://resiliencelab:resiliencelab@localhost:5432/resiliencelab",
        )
    )
    DB_POOL_SIZE: int = Field(default=5)
    DB_MAX_OVERFLOW: int = Field(default=5)
    DB_POOL_TIMEOUT: int = Field(default=10)

    # Allowed namespaces and target guardrails
    ALLOWED_NAMESPACE: str = Field(default="resiliencelab")
    FORBIDDEN_NAMESPACES: Set[str] = Field(
        default_factory=lambda: {"kube-system", "monitoring", "chaos-mesh", "default"}
    )

    # Integrations
    PROMETHEUS_URL: str = Field(
        default=os.getenv("PROMETHEUS_URL", "http://localhost:9091")
    )
    API_URL: str = Field(
        default=os.getenv("API_URL", "http://localhost:8000")
    )

    # Remediation policy guardrails
    DRY_RUN: bool = Field(default=True)
    AUTO_APPROVE_LOW_RISK: bool = Field(default=True)
    APPROVAL_TIMEOUT_SECONDS: int = Field(default=300)
    REMEDIATION_COOLDOWN_SECONDS: int = Field(default=600)  # 10m cooldown between restarts
    MAX_REMEDIATION_RETRIES: int = Field(default=2)

    # Recovery verification settings
    VERIFICATION_WINDOW_SECONDS: int = Field(default=15)
    VERIFICATION_CONSECUTIVE_SUCCESS: int = Field(default=3)
    VERIFICATION_INTERVAL_SECONDS: float = Field(default=3.0)

    # Reports directories
    INCIDENTS_LOG_DIR: str = Field(default="reports/incidents")
    POSTMORTEMS_DIR: str = Field(default="reports/postmortems")


settings = IncidentSettings()
