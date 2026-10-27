"""
ResilienceLab — SLO Package
"""

from automation.incident.slo.engine import (
    ALLOWED_DOWNTIME_SECONDS,
    BURN_RATE_FAST_THRESHOLD,
    BURN_RATE_SLOW_THRESHOLD,
    SLO_AVAILABILITY_TARGET_PCT,
    SLO_LATENCY_TARGET_MS,
    SLOEngine,
    SLOSummary,
)

__all__ = [
    "ALLOWED_DOWNTIME_SECONDS",
    "BURN_RATE_FAST_THRESHOLD",
    "BURN_RATE_SLOW_THRESHOLD",
    "SLO_AVAILABILITY_TARGET_PCT",
    "SLO_LATENCY_TARGET_MS",
    "SLOEngine",
    "SLOSummary",
]
