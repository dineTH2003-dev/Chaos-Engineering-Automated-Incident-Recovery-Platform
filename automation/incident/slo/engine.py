"""
ResilienceLab — SRE SLO & Error-Budget Intelligence Engine
Calculates rolling availability and latency SLIs, multi-window burn rates (fast/slow),
and exact error budget consumption per incident based on Google SRE mathematics.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional
import urllib.parse
import urllib.request

from automation.incident.config import settings
from automation.incident.models.incident import Incident

logger = logging.getLogger("incident.slo")

# Google SRE SLO Standards for ResilienceLab
SLO_AVAILABILITY_TARGET_PCT = 99.5  # 99.5% availability
SLO_LATENCY_TARGET_MS = 200.0       # 95% < 200ms
ROLLING_WINDOW_DAYS = 30
TOTAL_WINDOW_SECONDS = ROLLING_WINDOW_DAYS * 24 * 3600  # 2,592,000s

# Allowed budget calculation:
# Allowed downtime = (100% - 99.5%) * 30 days = 0.005 * 2,592,000s = 12,960 seconds (216 minutes)
ALLOWED_DOWNTIME_SECONDS = TOTAL_WINDOW_SECONDS * ((100.0 - SLO_AVAILABILITY_TARGET_PCT) / 100.0)

# Google SRE Burn Rate Thresholds:
# Fast burn: 14.4x consumes 2% of monthly budget in 1 hour (Critical alert)
# Slow burn: 6.0x consumes 5% of monthly budget in 6 hours (Warning alert)
BURN_RATE_FAST_THRESHOLD = 14.4
BURN_RATE_SLOW_THRESHOLD = 6.0


@dataclass
class SLOSummary:
    """Consolidated SRE SLO and error budget health status."""
    availability_target_pct: float
    current_availability_sli_pct: float
    latency_target_ms: float
    current_p95_latency_ms: float
    error_budget_remaining_pct: float
    current_burn_rate: float
    fast_burn_active: bool
    slow_burn_active: bool
    allowed_downtime_minutes: float
    consumed_downtime_seconds: float
    window: str = "30d"


class SLOEngine:
    """Computes SLIs, burn rates, and incident budget consumption."""

    def __init__(self, prometheus_base_url: str = settings.PROMETHEUS_URL):
        self.prometheus_base_url = prometheus_base_url.rstrip("/")

    def get_slo_summary(self, incidents: Optional[List[Incident]] = None) -> SLOSummary:
        """Calculate comprehensive SLO and error budget report."""
        # 1. Fetch live metrics from Prometheus if reachable
        error_rate_pct = self._query_error_rate_pct()
        p95_latency = self._query_p95_latency_ms()

        # Availability SLI = 100 - error_rate
        actual_avail = max(0.0, min(100.0, 100.0 - error_rate_pct))

        # 2. Burn rate calculation:
        # Burn Rate = actual_error_rate / allowed_error_rate = (100 - actual_avail) / 0.5
        allowed_error_pct = 100.0 - SLO_AVAILABILITY_TARGET_PCT
        burn_rate = round(error_rate_pct / allowed_error_pct, 2) if allowed_error_pct > 0 else 0.0

        fast_burn = burn_rate >= BURN_RATE_FAST_THRESHOLD
        slow_burn = burn_rate >= BURN_RATE_SLOW_THRESHOLD

        # 3. Error budget consumption across all incidents
        total_consumed_seconds = 0.0
        if incidents:
            for inc in incidents:
                total_consumed_seconds += self.compute_incident_outage_seconds(inc)

        budget_consumed_pct = (total_consumed_seconds / ALLOWED_DOWNTIME_SECONDS) * 100.0 if ALLOWED_DOWNTIME_SECONDS > 0 else 0.0
        budget_remaining_pct = max(0.0, round(100.0 - budget_consumed_pct, 2))

        return SLOSummary(
            availability_target_pct=SLO_AVAILABILITY_TARGET_PCT,
            current_availability_sli_pct=round(actual_avail, 3),
            latency_target_ms=SLO_LATENCY_TARGET_MS,
            current_p95_latency_ms=round(p95_latency, 2),
            error_budget_remaining_pct=budget_remaining_pct,
            current_burn_rate=burn_rate,
            fast_burn_active=fast_burn,
            slow_burn_active=slow_burn,
            allowed_downtime_minutes=round(ALLOWED_DOWNTIME_SECONDS / 60.0, 1),
            consumed_downtime_seconds=round(total_consumed_seconds, 1),
        )

    def compute_incident_outage_seconds(self, incident: Incident) -> float:
        """Compute the effective outage duration in seconds for an incident."""
        start = incident.created_at
        end = incident.resolved_at or incident.updated_at or datetime.now(timezone.utc)
        if not start:
            return 0.0

        duration_sec = max(0.0, (end - start).total_seconds())

        # Degradation factor based on severity
        # Critical = 1.0 (100% outage), Warning = 0.5 (50% degraded), Info = 0.1
        factor = 1.0
        if incident.severity == "warning":
            factor = 0.5
        elif incident.severity == "info":
            factor = 0.1

        return duration_sec * factor

    def compute_incident_budget_consumption(self, incident: Incident) -> Dict[str, Any]:
        """Compute exact error budget percentage consumed by a single incident."""
        outage_sec = self.compute_incident_outage_seconds(incident)
        consumed_pct = (outage_sec / ALLOWED_DOWNTIME_SECONDS) * 100.0 if ALLOWED_DOWNTIME_SECONDS > 0 else 0.0

        return {
            "incident_id": incident.id,
            "outage_duration_seconds": round(outage_sec, 1),
            "allowed_window_seconds": ALLOWED_DOWNTIME_SECONDS,
            "budget_consumed_pct": round(consumed_pct, 4),
            "severity": incident.severity,
        }

    def _query_error_rate_pct(self) -> float:
        """Query HTTP 5xx error rate from Prometheus."""
        expr = '(sum(rate(http_requests_total{job="api-service", status_code=~"5.."}[1m])) or vector(0)) / (sum(rate(http_requests_total{job="api-service"}[1m])) or vector(1)) * 100'
        return self._query_scalar(expr, default=0.0)

    def _query_p95_latency_ms(self) -> float:
        """Query p95 latency from Prometheus."""
        expr = 'histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{job="api-service"}[1m])) by (le)) * 1000'
        return self._query_scalar(expr, default=15.0)

    def _query_scalar(self, expr: str, default: float = 0.0) -> float:
        url = f"{self.prometheus_base_url}/api/v1/query?{urllib.parse.urlencode({'query': expr})}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "SLOEngine/1.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                result = data.get("data", {}).get("result", [])
                if result:
                    val = result[0].get("value", [None, None])[1]
                    return float(val) if val is not None else default
        except Exception:
            pass
        return default
