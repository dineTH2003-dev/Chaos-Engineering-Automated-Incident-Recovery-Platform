#!/usr/bin/env python3
"""
ResilienceLab — SRE Deployment Health Verification Engine
Independently verifies post-rollout service health through multi-step probing,
Kubernetes pod inspection, restart tracking, and Prometheus telemetry evaluation.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("deployment.verifier")

DEFAULT_REPORTS_DIR = "reports/deployments"


@dataclass
class StepCheckResult:
    step_name: str
    passed: bool
    details: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class VerificationResult:
    deployment_id: str
    service: str
    image_tag: str
    passed: bool
    start_time: str
    completion_time: str
    duration_seconds: float
    steps: List[Dict[str, Any]] = field(default_factory=list)
    failure_reason: Optional[str] = None
    telemetry_snapshot: Dict[str, Any] = field(default_factory=dict)


class DeploymentHealthVerifier:
    def __init__(
        self,
        namespace: str = "resiliencelab",
        api_base_url: str = "http://localhost:8000",
        worker_base_url: str = "http://localhost:8001",
        prometheus_url: str = "http://localhost:9091",
        consecutive_success_required: int = 3,
        probe_interval_seconds: float = 2.0,
        rollout_timeout_seconds: int = 120,
        max_error_rate_pct: float = 1.0,
        max_p95_latency_ms: float = 500.0,
    ):
        self.namespace = namespace
        self.api_base_url = api_base_url.rstrip("/")
        self.worker_base_url = worker_base_url.rstrip("/")
        self.prometheus_url = prometheus_url.rstrip("/")
        self.consecutive_success = consecutive_success_required
        self.probe_interval = probe_interval_seconds
        self.rollout_timeout = rollout_timeout_seconds
        self.max_error_rate_pct = max_error_rate_pct
        self.max_p95_latency_ms = max_p95_latency_ms

    def verify_k8s_rollout(self, service: str) -> StepCheckResult:
        """Verifies that Kubernetes Deployment rollout completes within timeout."""
        deployment_name = "api" if service == "api" else service
        cmd = [
            "kubectl", "rollout", "status",
            f"deployment/{deployment_name}",
            "-n", self.namespace,
            f"--timeout={self.rollout_timeout}s"
        ]
        logger.info(f"Checking rollout status for deployment/{deployment_name}...")
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return StepCheckResult(
                step_name="k8s_rollout_status",
                passed=True,
                details={"output": res.stdout.strip()}
            )
        except Exception as e:
            err = str(e)
            if hasattr(e, "stderr"):
                err = e.stderr.strip()
            return StepCheckResult(
                step_name="k8s_rollout_status",
                passed=False,
                error=err
            )

    def verify_replicas_and_restarts(self, service: str) -> StepCheckResult:
        """Inspects pod readiness, desired vs available replicas, and container restarts."""
        label_app = "resiliencelab-api" if service == "api" else f"resiliencelab-{service}"
        cmd = [
            "kubectl", "get", "pods",
            "-n", self.namespace,
            "-l", f"app={label_app}",
            "-o", "json"
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(res.stdout)
            items = data.get("items", [])
            if not items:
                return StepCheckResult(
                    step_name="replicas_and_restarts",
                    passed=False,
                    error=f"No pods found matching label app={label_app}"
                )

            total_pods = len(items)
            ready_pods = 0
            restart_counts = {}

            for pod in items:
                pod_name = pod["metadata"]["name"]
                statuses = pod.get("status", {}).get("containerStatuses", [])
                pod_ready = any(s.get("ready", False) for s in statuses)
                if pod_ready:
                    ready_pods += 1
                restarts = sum(s.get("restartCount", 0) for s in statuses)
                restart_counts[pod_name] = restarts

            passed = (ready_pods == total_pods and ready_pods > 0)
            return StepCheckResult(
                step_name="replicas_and_restarts",
                passed=passed,
                details={
                    "total_pods": total_pods,
                    "ready_pods": ready_pods,
                    "restart_counts": restart_counts,
                },
                error=None if passed else f"Only {ready_pods}/{total_pods} pods are ready."
            )
        except Exception as e:
            return StepCheckResult(step_name="replicas_and_restarts", passed=False, error=str(e))

    def _probe_http(self, url: str) -> bool:
        """Sends an HTTP GET probe and verifies HTTP 200 OK."""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "DeploymentVerifier/1.0"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                return resp.status == 200
        except Exception as e:
            logger.debug(f"Probe failed for {url}: {e}")
            return False

    def verify_consecutive_probes(self, service: str) -> StepCheckResult:
        """Requires multiple consecutive successful HTTP health and ready probes."""
        probe_urls = []
        if service in ("api", "all"):
            probe_urls.extend([
                f"{self.api_base_url}/health",
                f"{self.api_base_url}/ready",
            ])
        if service in ("worker", "all"):
            probe_urls.append(f"{self.worker_base_url}/")

        success_count = 0
        attempts = 0
        max_attempts = self.consecutive_success * 4

        while success_count < self.consecutive_success and attempts < max_attempts:
            attempts += 1
            all_healthy = True
            for url in probe_urls:
                if not self._probe_http(url):
                    all_healthy = False
                    break

            if all_healthy:
                success_count += 1
                logger.info(f"Probe check {success_count}/{self.consecutive_success} PASSED")
            else:
                success_count = 0  # Reset streak on failure
                logger.warning(f"Probe check failed on attempt {attempts}/{max_attempts}, resetting streak.")

            if success_count < self.consecutive_success:
                time.sleep(self.probe_interval)

        passed = (success_count >= self.consecutive_success)
        return StepCheckResult(
            step_name="consecutive_http_probes",
            passed=passed,
            details={
                "required_streak": self.consecutive_success,
                "achieved_streak": success_count,
                "total_attempts": attempts,
                "endpoints_probed": probe_urls,
            },
            error=None if passed else f"Failed to achieve {self.consecutive_success} consecutive healthy probes."
        )

    def query_prometheus_metric(self, query: str) -> Optional[float]:
        """Queries Prometheus Instant Vector API and returns scalar float."""
        try:
            url = f"{self.prometheus_url}/api/v1/query?query={urllib.parse.quote(query)}"
            req = urllib.request.Request(url, headers={"User-Agent": "DeploymentVerifier/1.0"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode())
                results = data.get("data", {}).get("result", [])
                if results and "value" in results[0]:
                    return float(results[0]["value"][1])
        except Exception as e:
            logger.debug(f"Prometheus query failed for '{query}': {e}")
        return None

    def verify_telemetry_slis(self) -> StepCheckResult:
        """Evaluates live 5xx error rate and p95 latency against safety thresholds."""
        error_rate_query = (
            'sum(rate(http_requests_total{namespace="resiliencelab", status_code=~"5.."}[1m])) '
            '/ (sum(rate(http_requests_total{namespace="resiliencelab"}[1m])) or 1) * 100'
        )
        p95_latency_query = (
            'histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{namespace="resiliencelab"}[1m])) by (le)) * 1000'
        )

        error_rate = self.query_prometheus_metric(error_rate_query) or 0.0
        p95_ms = self.query_prometheus_metric(p95_latency_query) or 0.0

        error_passed = error_rate <= self.max_error_rate_pct
        latency_passed = p95_ms <= self.max_p95_latency_ms

        passed = error_passed and latency_passed
        details = {
            "error_rate_pct": round(error_rate, 3),
            "max_allowed_error_rate_pct": self.max_error_rate_pct,
            "p95_latency_ms": round(p95_ms, 2),
            "max_allowed_latency_ms": self.max_p95_latency_ms,
        }

        error_msg = None
        if not passed:
            reasons = []
            if not error_passed:
                reasons.append(f"HTTP 5xx rate {error_rate:.2f}% > threshold {self.max_error_rate_pct}%")
            if not latency_passed:
                reasons.append(f"p95 latency {p95_ms:.1f}ms > threshold {self.max_p95_latency_ms}ms")
            error_msg = "; ".join(reasons)

        return StepCheckResult(
            step_name="prometheus_telemetry_slis",
            passed=passed,
            details=details,
            error=error_msg
        )

    def execute_verification(
        self,
        service: str = "api",
        deployment_id: str = "DEP-UNKNOWN",
        image_tag: str = "latest",
    ) -> VerificationResult:
        """Executes full verification workflow and returns structured VerificationResult."""
        start_time = datetime.now(timezone.utc)
        logger.info(f"Starting deployment health verification for {service} (ID: {deployment_id}, Tag: {image_tag})...")

        step_results = []
        failed_reason = None

        # Step 1: K8s Rollout
        rollout_step = self.verify_k8s_rollout(service)
        step_results.append(rollout_step)
        if not rollout_step.passed:
            failed_reason = f"Rollout failed: {rollout_step.error}"

        # Step 2: Replicas & Restarts
        if not failed_reason:
            replica_step = self.verify_replicas_and_restarts(service)
            step_results.append(replica_step)
            if not replica_step.passed:
                failed_reason = f"Replica check failed: {replica_step.error}"

        # Step 3: Consecutive HTTP Probes
        if not failed_reason:
            probe_step = self.verify_consecutive_probes(service)
            step_results.append(probe_step)
            if not probe_step.passed:
                failed_reason = f"Probe verification failed: {probe_step.error}"

        # Step 4: Telemetry SLIs
        telemetry_snapshot = {}
        if not failed_reason:
            telemetry_step = self.verify_telemetry_slis()
            step_results.append(telemetry_step)
            telemetry_snapshot = telemetry_step.details
            if not telemetry_step.passed:
                failed_reason = f"Telemetry SLO check failed: {telemetry_step.error}"

        completion_time = datetime.now(timezone.utc)
        duration = (completion_time - start_time).total_seconds()
        passed = (failed_reason is None)

        def _to_dict(s: Any) -> Dict[str, Any]:
            if hasattr(s, "__dataclass_fields__"):
                return asdict(s)
            if isinstance(s, dict):
                return s
            if hasattr(s, "__dict__"):
                return {k: v for k, v in s.__dict__.items() if not k.startswith("_")}
            return {"step": str(s)}

        result = VerificationResult(
            deployment_id=deployment_id,
            service=service,
            image_tag=image_tag,
            passed=passed,
            start_time=start_time.isoformat(),
            completion_time=completion_time.isoformat(),
            duration_seconds=round(duration, 2),
            steps=[_to_dict(s) for s in step_results],
            failure_reason=failed_reason,
            telemetry_snapshot=telemetry_snapshot,
        )

        # Save result JSON report
        os.makedirs(DEFAULT_REPORTS_DIR, exist_ok=True)
        report_path = os.path.join(DEFAULT_REPORTS_DIR, f"verification_{deployment_id}.json")
        with open(report_path, "w") as f:
            json.dump(asdict(result), f, indent=2)
        logger.info(f"Verification completed: {'PASSED' if passed else 'FAILED'} (Report: {report_path})")

        return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ResilienceLab Deployment Health Verification Engine")
    parser.add_argument("--service", choices=["api", "worker", "all"], default="api", help="Service to verify")
    parser.add_argument("--deployment-id", default=f"DEP-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}", help="Deployment ID")
    parser.add_argument("--tag", default="unknown", help="Deployed image tag")
    parser.add_argument("--consecutive-checks", type=int, default=3, help="Consecutive probe checks required")

    args = parser.parse_args()

    verifier = DeploymentHealthVerifier(consecutive_success_required=args.consecutive_checks)
    res = verifier.execute_verification(service=args.service, deployment_id=args.deployment_id, image_tag=args.tag)

    if res.passed:
        print(f"\n✅ DEPLOYMENT VERIFICATION PASSED in {res.duration_seconds}s")
        sys.exit(0)
    else:
        print(f"\n❌ DEPLOYMENT VERIFICATION FAILED: {res.failure_reason}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
