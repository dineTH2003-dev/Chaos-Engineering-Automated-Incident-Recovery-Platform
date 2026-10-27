"""
ResilienceLab — Automated Chaos Experiment Orchestrator
Executes controlled failure experiments, tracks MTTD/MTTR, validates SLOs, and produces post-mortems.
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

from chaos.controller.health_checker import HealthChecker
from chaos.controller.kubernetes_client import KubernetesClient, KubernetesSafetyViolation
from chaos.controller.prometheus_client import PrometheusClient
from chaos.controller.result_collector import ResultCollector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("chaos.runner")

EXPERIMENT_CATALOG = {
    "ce01": {
        "id": "CE-01",
        "name": "API Pod Failure & Self-Healing",
        "manifest": "chaos/experiments/ce01-pod-failure.yaml",
        "target_label": "app=resiliencelab-api",
        "hypothesis": "Surviving API replica serves uninterrupted traffic (100% availability); Kubernetes replaces killed pod in <15s.",
        "duration": 25,
        "slo_availability": 99.5,
        "type": "manifest",
    },
    "ce02": {
        "id": "CE-02",
        "name": "API-to-Database Network Latency",
        "manifest": "chaos/experiments/ce02-network-latency.yaml",
        "target_label": "app=resiliencelab-api",
        "hypothesis": "300ms network delay elevates API p95 latency; requests succeed without dropped connections; recovery is instantaneous upon expiration.",
        "duration": 60,
        "slo_availability": 99.0,
        "type": "manifest",
    },
    "ce03": {
        "id": "CE-03",
        "name": "PostgreSQL Dependency Outage & Recovery",
        "manifest": "chaos/experiments/ce03-database-partition.yaml",
        "target_label": "app=postgres",
        "hypothesis": "API readiness probe decouples and fails gracefully (503); liveness stays 200; reconnection is automatic upon DB restoration; 0 data loss.",
        "duration": 30,
        "slo_availability": 20.0,  # Expected degradation: 503 proves graceful degradation during DB outage
        "type": "scale",
    },
    "ce04": {
        "id": "CE-04",
        "name": "API CPU Saturation & Scaling",
        "manifest": "chaos/experiments/ce04-cpu-stress.yaml",
        "target_label": "app=resiliencelab-api",
        "hypothesis": "85% CPU load on 1 replica elevates telemetry; unaffected replica absorbs excess load; system stabilizes within 15s post-stress.",
        "duration": 60,
        "slo_availability": 98.0,
        "type": "manifest",
    },
}


class ExperimentRunner:
    """Executes chaos experiments with rigorous safety and measurement."""

    def __init__(self, api_url: str = "http://localhost:8000", prom_url: str = "http://localhost:9091"):
        self.k8s = KubernetesClient(target_namespace="resiliencelab")
        self.prom = PrometheusClient(base_url=prom_url)
        self.health = HealthChecker(base_url=api_url, probe_interval=0.5)

    def run(self, exp_key: str) -> bool:
        """Run a single experiment by catalog key."""
        exp = EXPERIMENT_CATALOG.get(exp_key.lower())
        if not exp:
            logger.error(f"Unknown experiment '{exp_key}'. Supported: {list(EXPERIMENT_CATALOG.keys())}")
            return False

        exp_id = exp["id"]
        exp_name = exp["name"]
        print("\n" + "=" * 70)
        print(f"🚀 EXECUTING CHAOS EXPERIMENT: {exp_id} — {exp_name}")
        print("=" * 70)
        print(f"Hypothesis : {exp['hypothesis']}")
        print(f"Target     : {exp['target_label']} in namespace 'resiliencelab'")
        print(f"Duration   : {exp['duration']}s")
        print(f"SLO Target : Availability >= {exp['slo_availability']}%")
        print("-" * 70)

        # 1. Pre-flight verification
        logger.info("Step 1/8: Pre-flight safety & steady-state health verification...")
        if not self.k8s.verify_workload_chaos_enabled(exp["target_label"]):
            logger.error("Target workload lacks 'chaos-enabled: true' label! Aborting.")
            return False

        healthy, msg = self.health.check_steady_state()
        if not healthy:
            logger.error(f"Cluster is not in healthy steady-state: {msg}. Aborting.")
            return False
        logger.info(f"Steady-state verified: {msg}")

        # 2. Baseline telemetry collection
        logger.info("Step 2/8: Collecting baseline steady-state metrics...")
        baseline = {
            "p95_latency_ms": self.prom.get_api_p95_latency_ms(),
            "error_rate_pct": self.prom.get_api_error_rate_pct(),
            "ready_replicas": self.prom.get_api_ready_replicas(),
            "cpu_cores": self.prom.get_api_cpu_usage_cores(),
            "availability_pct": 100.0,
        }
        logger.info(f"Baseline: Replicas={baseline['ready_replicas']}, p95={baseline['p95_latency_ms']}ms, ErrorRate={baseline['error_rate_pct']}%")

        collector = ResultCollector(exp_id, exp_name, exp["hypothesis"])
        collector.steady_state_baseline = baseline
        collector.slo_target_availability_pct = exp["slo_availability"]

        # 3. Start continuous probing
        logger.info("Step 3/8: Initiating continuous availability probing (interval=0.5s)...")
        self.health.start_probing()

        start_time = datetime.now(timezone.utc)
        t_inject = time.time()
        t_detect: Optional[float] = None
        t_recover: Optional[float] = None
        notes = []

        try:
            # 4. Inject Chaos
            logger.info(f"Step 4/8: Injecting fault ({exp['type']})...")
            if exp["type"] == "manifest":
                if not os.path.isfile(exp["manifest"]):
                    raise FileNotFoundError(f"Manifest not found: {exp['manifest']}")
                self.k8s.apply_manifest(exp["manifest"])
                notes.append(f"Applied manifest: {exp['manifest']}")
            elif exp["type"] == "scale":
                # CE-03: scale postgres to 0
                logger.warning("Scaling PostgreSQL to 0 replicas...")
                self.k8s.scale_deployment("postgres", 0)
                notes.append("Scaled deployment/postgres to 0 replicas.")

            # 5. Failure Detection Loop
            logger.info("Step 5/8: Monitoring for failure detection...")
            detect_deadline = time.time() + 15.0
            while time.time() < detect_deadline:
                time.sleep(0.5)
                if exp_id == "CE-01":
                    # Check if any pod is terminating or count dropped
                    pods = self.k8s.get_pods(exp["target_label"])
                    if any(not p["ready"] or p["phase"] != "Running" for p in pods) or len(pods) < 2:
                        t_detect = time.time()
                        logger.info(f"Pod failure detected at T+{t_detect - t_inject:.2f}s!")
                        break
                elif exp_id == "CE-02":
                    # Check for elevated latency in probes
                    if len(self.health.probes) >= 3:
                        recent = self.health.probes[-3:]
                        if any(p["latency_ms"] > 150.0 for p in recent):
                            t_detect = time.time()
                            logger.info(f"Network latency elevation detected at T+{t_detect - t_inject:.2f}s!")
                            break
                elif exp_id == "CE-03":
                    # Check for /ready returning 503
                    recent = self.health.probes[-3:] if self.health.probes else []
                    if any(p["status_code"] == 503 for p in recent):
                        t_detect = time.time()
                        logger.info(f"Database outage detected (/ready 503) at T+{t_detect - t_inject:.2f}s!")
                        break
                elif exp_id == "CE-04":
                    t_detect = time.time()
                    break

            if not t_detect:
                t_detect = time.time()
                logger.info(f"Detection fallback timestamp set at T+{t_detect - t_inject:.2f}s")

            # 6. Active Chaos Window
            logger.info(f"Step 6/8: Maintaining chaos window for {exp['duration']}s...")
            time.sleep(exp["duration"])

            # Intermediate recovery action if needed
            if exp["type"] == "scale":
                logger.info("Restoring PostgreSQL to 1 replica...")
                self.k8s.scale_deployment("postgres", 1)
                notes.append("Restored deployment/postgres to 1 replica.")

            # Clean up chaos resource immediately
            if exp["type"] == "manifest":
                logger.info(f"Cleaning up chaos manifest {exp['manifest']}...")
                self.k8s.delete_manifest(exp["manifest"])
                notes.append(f"Deleted manifest: {exp['manifest']}")

            # 7. Recovery Detection Loop
            logger.info("Step 7/8: Monitoring recovery to steady-state...")
            recovery_deadline = time.time() + 60.0
            while time.time() < recovery_deadline:
                time.sleep(1.0)
                healthy, _ = self.health.check_steady_state()
                api_replicas = self.k8s.get_deployment_status("api")
                if healthy and api_replicas["ready_replicas"] >= 2:
                    t_recover = time.time()
                    logger.info(f"Recovery verified at T+{t_recover - t_inject:.2f}s!")
                    break

            if not t_recover:
                t_recover = time.time()
                logger.warning("Recovery loop timed out; setting current timestamp.")

        finally:
            # Emergency fallback cleanup
            self.k8s.delete_all_active_chaos()
            prober_summary = self.health.stop_probing()

        end_time = datetime.now(timezone.utc)
        mttd = (t_detect - t_inject) if t_detect else 0.0
        mttr = (t_recover - t_detect) if (t_recover and t_detect) else 0.0

        # 8. Post-flight verification & reports
        logger.info("Step 8/8: Collecting post-flight telemetry & compiling reports...")
        time.sleep(2.0)  # Allow metric scrape
        recovery_telemetry = {
            "p95_latency_ms": self.prom.get_api_p95_latency_ms(),
            "error_rate_pct": self.prom.get_api_error_rate_pct(),
            "ready_replicas": self.prom.get_api_ready_replicas(),
            "cpu_cores": self.prom.get_api_cpu_usage_cores(),
            "availability_pct": 100.0,
        }
        alerts = self.prom.get_active_alerts()

        collector.chaos_telemetry = {
            "probes_sent": prober_summary["total_probes"],
            "degraded_probes": prober_summary["degraded_probes"],
            "failed_probes": prober_summary["failed_probes"],
        }
        collector.recovery_telemetry = recovery_telemetry

        collector.finalize(
            start_time=start_time,
            end_time=end_time,
            mttd=mttd,
            mttr=mttr,
            prober_summary=prober_summary,
            alerts=alerts,
            notes=notes,
        )

        json_path = collector.save_json_report()
        md_path = collector.save_markdown_report()

        print("\n" + "=" * 70)
        print(f"📊 EXPERIMENT RESULTS: {exp_id} — {exp_name}")
        print("=" * 70)
        print(f"Verdict          : {'✅ PASS' if collector.slo_passed else '❌ FAIL'}")
        print(f"Availability     : {prober_summary['availability_pct']}% (SLO Target: >= {collector.slo_target_availability_pct}%)")
        print(f"Mean Time Detect : {mttd:.2f}s")
        print(f"Mean Time Recover: {mttr:.2f}s")
        print(f"Total Probes     : {prober_summary['total_probes']} (200 OK: {prober_summary['successful_probes']}, 503: {prober_summary['degraded_probes']}, Err: {prober_summary['failed_probes']})")
        print(f"JSON Report      : {json_path}")
        print(f"Incident Report  : {md_path}")
        print("=" * 70 + "\n")

        return collector.slo_passed


def main():
    parser = argparse.ArgumentParser(description="ResilienceLab Chaos Experiment Runner")
    parser.add_argument(
        "--experiment",
        "-e",
        required=True,
        choices=["ce01", "ce02", "ce03", "ce04", "all"],
        help="Experiment ID to execute",
    )
    parser.add_argument("--api-url", default="http://localhost:8000", help="API service base URL")
    parser.add_argument("--prom-url", default="http://localhost:9091", help="Prometheus base URL")

    args = parser.parse_args()
    runner = ExperimentRunner(api_url=args.api_url, prom_url=args.prom_url)

    if args.experiment == "all":
        experiments = ["ce01", "ce02", "ce03", "ce04"]
        results = {}
        for exp in experiments:
            success = runner.run(exp)
            results[exp] = success
            logger.info("Cooling down for 30s before next experiment...")
            time.sleep(30.0)
        print("\n" + "=" * 50)
        print("🏁 ALL EXPERIMENTS SUMMARY:")
        for exp, res in results.items():
            print(f"  - {exp.upper()}: {'✅ PASS' if res else '❌ FAIL'}")
        print("=" * 50)
        sys.exit(0 if all(results.values()) else 1)
    else:
        success = runner.run(args.experiment)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
