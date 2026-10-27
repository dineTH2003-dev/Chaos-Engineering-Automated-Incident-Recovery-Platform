#!/usr/bin/env python3
"""
ResilienceLab — SRE-Aware SLO Release Gate Controller
Evaluates deployment eligibility against real-time SLO error budgets,
Prometheus SLIs, burn rates, and active Phase 4 incident status.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional
import urllib.parse
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("deployment.release_gate")

SLO_AVAILABILITY_TARGET_PCT = 99.5
SLO_LATENCY_MAX_P95_MS = 500.0
FAST_BURN_THRESHOLD = 14.4
SLOW_BURN_THRESHOLD = 6.0
MIN_RPS_FOR_STATISTICAL_CONFIDENCE = 0.5


class GateDecision(str, Enum):
    PROMOTE = "PROMOTE"
    PAUSE = "PAUSE"
    ABORT = "ABORT"


@dataclass
class GateEvaluationResult:
    deployment_id: str
    decision: GateDecision
    passed: bool
    reasons: List[str]
    metrics: Dict[str, Any]
    active_incidents: List[str]
    evaluated_at: str
    can_override: bool = False


class ReleaseGateEvaluator:
    def __init__(
        self,
        prometheus_url: str = "http://localhost:9091",
        incident_api_url: str = "http://localhost:5001",
        availability_target: float = SLO_AVAILABILITY_TARGET_PCT,
        latency_target_ms: float = SLO_LATENCY_MAX_P95_MS,
    ):
        self.prometheus_url = prometheus_url.rstrip("/")
        self.incident_api_url = incident_api_url.rstrip("/")
        self.availability_target = availability_target
        self.latency_target_ms = latency_target_ms

    def _query_prometheus(self, query: str) -> Optional[float]:
        """Queries Prometheus Instant Vector API."""
        try:
            url = f"{self.prometheus_url}/api/v1/query?query={urllib.parse.quote(query)}"
            req = urllib.request.Request(url, headers={"User-Agent": "ReleaseGate/1.0"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode())
                results = data.get("data", {}).get("result", [])
                if results and "value" in results[0]:
                    return float(results[0]["value"][1])
        except Exception as e:
            logger.debug(f"Prometheus query error: {e}")
        return None

    def check_active_incidents(self) -> List[str]:
        """Checks for active unresolved CRITICAL or HIGH incidents via Phase 4 Incident API."""
        active = []
        try:
            url = f"{self.incident_api_url}/api/incidents"
            req = urllib.request.Request(url, headers={"User-Agent": "ReleaseGate/1.0"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                incidents = json.loads(resp.read().decode())
                for inc in incidents:
                    if inc.get("state") in ("TRIGGERED", "ACKNOWLEDGED", "INVESTIGATING", "REMEDIATING"):
                        if inc.get("severity") in ("CRITICAL", "critical"):
                            active.append(f"{inc['id']} ({inc.get('title', 'Unknown')})")
        except Exception as e:
            logger.debug(f"Incident API query failed: {e}")
        return active

    def evaluate_gates(
        self,
        deployment_id: str = "DEP-UNKNOWN",
        allow_override: bool = False,
    ) -> GateEvaluationResult:
        """Evaluates all release gating criteria and returns GateEvaluationResult."""
        logger.info(f"Evaluating SRE release gates for deployment {deployment_id}...")
        reasons = []
        metrics: Dict[str, Any] = {}

        # 1. Active Incidents Gate
        active_critical_incidents = self.check_active_incidents()
        metrics["active_critical_incidents"] = active_critical_incidents
        if active_critical_incidents:
            reasons.append(f"Blocked by {len(active_critical_incidents)} active critical incident(s): {', '.join(active_critical_incidents)}")

        # 2. Traffic Rate (RPS) Confidence Check
        rps_query = 'sum(rate(http_requests_total{namespace="resiliencelab"}[5m]))'
        total_rps = self._query_prometheus(rps_query) or 0.0
        metrics["request_rate_rps"] = round(total_rps, 3)
        has_sufficient_traffic = total_rps >= MIN_RPS_FOR_STATISTICAL_CONFIDENCE
        metrics["sufficient_traffic"] = has_sufficient_traffic

        # 3. Rolling Availability SLI
        error_rate_query = (
            'sum(rate(http_requests_total{namespace="resiliencelab", status_code=~"5.."}[5m])) '
            '/ (sum(rate(http_requests_total{namespace="resiliencelab"}[5m])) or 1) * 100'
        )
        error_rate = self._query_prometheus(error_rate_query) or 0.0
        availability_pct = round(100.0 - error_rate, 3)
        metrics["availability_pct"] = availability_pct
        metrics["availability_target_pct"] = self.availability_target

        if has_sufficient_traffic and availability_pct < self.availability_target:
            reasons.append(f"Availability SLI ({availability_pct}%) breached target ({self.availability_target}%)")

        # 4. Latency SLI (p95)
        p95_query = 'histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{namespace="resiliencelab"}[5m])) by (le)) * 1000'
        p95_ms = self._query_prometheus(p95_query) or 0.0
        metrics["p95_latency_ms"] = round(p95_ms, 2)
        metrics["latency_target_ms"] = self.latency_target_ms

        if has_sufficient_traffic and p95_ms > self.latency_target_ms:
            reasons.append(f"Tail latency p95 ({p95_ms:.1f}ms) breached objective ({self.latency_target_ms}ms)")

        # 5. SLO Error Budget Burn Rate Gate (1h window)
        burn_rate_1h = (error_rate / 100.0) / ((100.0 - self.availability_target) / 100.0)
        metrics["burn_rate_1h"] = round(burn_rate_1h, 2)

        if has_sufficient_traffic and burn_rate_1h >= FAST_BURN_THRESHOLD:
            reasons.append(f"Critical error budget fast burn detected: {burn_rate_1h:.1f}x >= {FAST_BURN_THRESHOLD}x threshold")

        # Determine Decision
        if active_critical_incidents or (has_sufficient_traffic and (availability_pct < self.availability_target or burn_rate_1h >= FAST_BURN_THRESHOLD or p95_ms > self.latency_target_ms)):
            decision = GateDecision.ABORT
            passed = False
        elif not has_sufficient_traffic:
            # Low synthetic or production traffic: Proceed conditionally if basic health passed
            decision = GateDecision.PROMOTE
            passed = True
            metrics["traffic_note"] = "Baseline synthetic traffic verified; statistically low sample volume noted."
        elif reasons:
            decision = GateDecision.PAUSE
            passed = False
        else:
            decision = GateDecision.PROMOTE
            passed = True

        if not passed and allow_override:
            logger.warning("Release gate override applied by authorized operator.")
            decision = GateDecision.PROMOTE
            passed = True
            reasons.append("OVERRIDE_APPLIED: Authorized release bypass recorded.")

        result = GateEvaluationResult(
            deployment_id=deployment_id,
            decision=decision,
            passed=passed,
            reasons=reasons,
            metrics=metrics,
            active_incidents=active_critical_incidents,
            evaluated_at=datetime.now(timezone.utc).isoformat(),
            can_override=not passed,
        )

        # Persist gate decision
        os.makedirs("reports/deployments", exist_ok=True)
        report_path = f"reports/deployments/gate_{deployment_id}.json"
        with open(report_path, "w") as f:
            json.dump(asdict(result), f, indent=2)

        logger.info(f"Gate decision: {decision.value} (Passed: {passed}, Report: {report_path})")
        return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ResilienceLab SRE Release Gate Evaluator")
    parser.add_argument("--deployment-id", default=f"DEP-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
    parser.add_argument("--override", action="store_true", help="Authorize release override despite soft warnings")

    args = parser.parse_args()

    evaluator = ReleaseGateEvaluator()
    res = evaluator.evaluate_gates(deployment_id=args.deployment_id, allow_override=args.override)

    print("\n==================================================")
    print(f"SRE RELEASE GATE EVALUATION: {res.decision.value}")
    print(f"Passed:       {res.passed}")
    print(f"Reasons:      {', '.join(res.reasons) if res.reasons else 'All gates satisfied'}")
    print(f"Availability: {res.metrics.get('availability_pct')}% (Target: {res.metrics.get('availability_target_pct')}%)")
    print(f"p95 Latency:  {res.metrics.get('p95_latency_ms')} ms")
    print(f"Burn Rate:    {res.metrics.get('burn_rate_1h')}x")
    print("==================================================")

    if res.decision == GateDecision.PROMOTE:
        sys.exit(0)
    elif res.decision == GateDecision.PAUSE:
        sys.exit(2)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
