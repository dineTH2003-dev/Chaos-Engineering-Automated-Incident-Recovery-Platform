"""
ResilienceLab — Automated Multi-Source Diagnosis Engine
Collects live telemetry, Kubernetes cluster events, pod states, HTTP probes, and Chaos Mesh status
to compute evidence-based root cause analysis with diagnostic confidence ratings.
"""

from dataclasses import dataclass, field
import json
import logging
import subprocess
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

from automation.incident.config import settings
from automation.incident.models.incident import ConfidenceLevel, Incident

logger = logging.getLogger("incident.diagnosis")


@dataclass
class DiagnosticReport:
    """Structured output of automated incident diagnosis."""
    incident_id: str
    suspected_cause: str
    confidence: ConfidenceLevel
    evidence: Dict[str, Any] = field(default_factory=dict)
    recommended_runbook: str = "api-unavailable"
    recommended_actions: List[Dict[str, Any]] = field(default_factory=list)


class AutomatedDiagnosisEngine:
    """Gathers multi-source evidence and performs automated root-cause analysis."""

    def __init__(
        self,
        namespace: str = settings.ALLOWED_NAMESPACE,
        api_base_url: str = settings.API_URL,
        prometheus_base_url: str = settings.PROMETHEUS_URL,
    ):
        self.namespace = namespace
        self.api_base_url = api_base_url.rstrip("/")
        self.prometheus_base_url = prometheus_base_url.rstrip("/")

    def diagnose(self, incident: Incident) -> DiagnosticReport:
        """Run full multi-source diagnostic pipeline on an incident."""
        evidence: Dict[str, Any] = {}
        service = incident.affected_service.lower()

        # Source 1: Kubernetes Workload & Pod States
        pod_states = self._collect_pod_states()
        evidence["pods"] = pod_states

        # Source 2: Deployment Replicas
        deployments = self._collect_deployment_states()
        evidence["deployments"] = deployments

        # Source 3: Recent Kubernetes Warning Events
        events = self._collect_recent_k8s_events()
        evidence["k8s_events"] = events

        # Source 4: Live HTTP Health & Readiness Probes
        probes = self._probe_endpoints()
        evidence["http_probes"] = probes

        # Source 5: Active Chaos Mesh Experiments
        chaos = self._check_chaos_mesh_experiments()
        evidence["active_chaos"] = chaos

        # Synthesize Evidence into Suspected Cause and Recommended Runbook
        return self._synthesize(incident, evidence)

    def _collect_pod_states(self) -> List[Dict[str, Any]]:
        """Query pod statuses via kubectl in the target namespace."""
        try:
            cmd = ["kubectl", "get", "pods", "-n", self.namespace, "-o", "json"]
            res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=5)
            data = json.loads(res.stdout)
            pods = []
            for item in data.get("items", []):
                meta = item.get("metadata", {})
                status = item.get("status", {})
                container_statuses = status.get("containerStatuses", [])

                restarts = sum(cs.get("restartCount", 0) for cs in container_statuses)
                ready = all(cs.get("ready", False) for cs in container_statuses) if container_statuses else False
                waiting_reason = None
                exit_code = None

                for cs in container_statuses:
                    waiting = cs.get("state", {}).get("waiting", {})
                    if waiting:
                        waiting_reason = waiting.get("reason")
                    terminated = cs.get("state", {}).get("terminated", {})
                    if terminated:
                        exit_code = terminated.get("exitCode")

                pods.append({
                    "name": meta.get("name"),
                    "service": meta.get("labels", {}).get("app", "unknown"),
                    "phase": status.get("phase"),
                    "ready": ready,
                    "restarts": restarts,
                    "waiting_reason": waiting_reason,
                    "exit_code": exit_code,
                })
            return pods
        except Exception as exc:
            logger.warning("Could not gather pod states: %s", exc)
            return []

    def _collect_deployment_states(self) -> Dict[str, Dict[str, int]]:
        """Query deployment replica counts."""
        try:
            cmd = ["kubectl", "get", "deployments", "-n", self.namespace, "-o", "json"]
            res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=5)
            data = json.loads(res.stdout)
            result = {}
            for item in data.get("items", []):
                name = item.get("metadata", {}).get("name")
                spec = item.get("spec", {})
                status = item.get("status", {})
                result[name] = {
                    "desired": spec.get("replicas", 0),
                    "ready": status.get("readyReplicas", 0),
                    "available": status.get("availableReplicas", 0),
                }
            return result
        except Exception as exc:
            logger.warning("Could not gather deployment states: %s", exc)
            return {}

    def _collect_recent_k8s_events(self) -> List[str]:
        """Fetch recent Warning events from the namespace."""
        try:
            cmd = [
                "kubectl", "get", "events", "-n", self.namespace,
                "--field-selector", "type=Warning",
                "--sort-by=.metadata.creationTimestamp",
                "-o", "json",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=5)
            data = json.loads(res.stdout)
            events = []
            for item in data.get("items", [])[-5:]:  # Last 5 warnings
                reason = item.get("reason", "")
                message = item.get("message", "")
                involved = item.get("involvedObject", {}).get("name", "")
                events.append(f"{involved} [{reason}]: {message}")
            return events
        except Exception as exc:
            logger.warning("Could not fetch k8s events: %s", exc)
            return []

    def _probe_endpoints(self) -> Dict[str, Any]:
        """Probe live /health and /ready endpoints."""
        results = {}
        for path in ["/health", "/ready"]:
            url = f"{self.api_base_url}{path}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "IncidentDiagnosis/1.0"})
                with urllib.request.urlopen(req, timeout=3) as resp:
                    status_code = resp.getcode()
                    body = json.loads(resp.read().decode("utf-8"))
                    results[path] = {"status_code": status_code, "body": body}
            except urllib.error.HTTPError as err:
                try:
                    body = json.loads(err.read().decode("utf-8"))
                except Exception:
                    body = {"raw": err.reason}
                results[path] = {"status_code": err.code, "body": body}
            except Exception as exc:
                results[path] = {"status_code": 0, "error": str(exc)}
        return results

    def _check_chaos_mesh_experiments(self) -> List[Dict[str, Any]]:
        """Check if any Chaos Mesh experiment CRDs are currently injected."""
        active = []
        for crd in ["podchaos", "networkchaos", "stresschaos"]:
            try:
                cmd = ["kubectl", "get", crd, "-n", self.namespace, "-o", "json"]
                res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=5)
                data = json.loads(res.stdout)
                for item in data.get("items", []):
                    name = item.get("metadata", {}).get("name")
                    status = item.get("status", {})
                    phase = status.get("experiment", {}).get("phase") or "Injected"
                    active.append({"kind": crd, "name": name, "phase": phase})
            except Exception:
                continue
        return active

    def _synthesize(self, incident: Incident, evidence: Dict[str, Any]) -> DiagnosticReport:
        """Analyze gathered evidence and produce root cause diagnosis."""
        service = incident.affected_service.lower()
        alertname = incident.source_alert
        deployments = evidence.get("deployments", {})
        pods = evidence.get("pods", [])
        probes = evidence.get("http_probes", {})
        chaos = evidence.get("active_chaos", [])

        # Diagnostic Hypothesis 1: Database Outage
        postgres_deploy = deployments.get("postgres", {})
        ready_probe = probes.get("/ready", {})
        db_unreachable_in_probe = (
            ready_probe.get("status_code") == 503
            or (ready_probe.get("body", {}).get("database") == "unreachable")
        )

        if (
            alertname in {"DatabaseConnectionFailures", "PostgresDown"}
            or service == "postgres"
            or (postgres_deploy.get("desired") == 0 or postgres_deploy.get("ready") == 0)
            or db_unreachable_in_probe
        ):
            reason_details = []
            if postgres_deploy.get("desired") == 0:
                reason_details.append("postgres deployment scaled to 0 replicas")
            elif postgres_deploy.get("ready") == 0:
                reason_details.append("postgres pod is not ready")
            if db_unreachable_in_probe:
                reason_details.append("API /ready probe reports database unreachable (HTTP 503)")

            suspected = (
                "PostgreSQL database failure: " + (", ".join(reason_details) if reason_details else "Connection refused")
            )
            return DiagnosticReport(
                incident_id=incident.id,
                suspected_cause=suspected,
                confidence=ConfidenceLevel.HIGH,
                evidence=evidence,
                recommended_runbook="database-unavailable",
                recommended_actions=[
                    {
                        "action_type": "scale_deployment",
                        "target_resource": "deploy/postgres",
                        "parameters": {"replicas": 1},
                        "risk_level": "HIGH",
                        "requires_approval": True,
                    }
                ],
            )

        # Diagnostic Hypothesis 2: Pod CrashLoop or OOMKilled
        for pod in pods:
            if pod.get("service") == service:
                if pod.get("waiting_reason") == "CrashLoopBackOff" or pod.get("restarts", 0) > 3:
                    return DiagnosticReport(
                        incident_id=incident.id,
                        suspected_cause=f"Pod {pod['name']} is crashlooping (restarts={pod['restarts']}, exitCode={pod.get('exit_code')}).",
                        confidence=ConfidenceLevel.HIGH,
                        evidence=evidence,
                        recommended_runbook="pod-crash-loop",
                        recommended_actions=[
                            {
                                "action_type": "restart_deployment",
                                "target_resource": f"deploy/{service}",
                                "risk_level": "LOW",
                                "requires_approval": False,
                            }
                        ],
                    )

        # Diagnostic Hypothesis 3: CPU Saturation / High Stress
        if alertname == "HighCPUUtilization" or "cpu" in incident.title.lower():
            chaos_stress = [c for c in chaos if c.get("kind") == "stresschaos"]
            cause = "High CPU saturation impacting response latency."
            if chaos_stress:
                cause += f" Correlated with active StressChaos: {chaos_stress[0]['name']}."

            return DiagnosticReport(
                incident_id=incident.id,
                suspected_cause=cause,
                confidence=ConfidenceLevel.HIGH,
                evidence=evidence,
                recommended_runbook="high-cpu",
                recommended_actions=[
                    {
                        "action_type": "scale_deployment",
                        "target_resource": f"deploy/{service}",
                        "parameters": {"replicas": 3},
                        "risk_level": "MEDIUM",
                        "requires_approval": False,
                    }
                ],
            )

        # Diagnostic Hypothesis 4: High Latency / Network Delay
        if alertname == "HighRequestLatency" or "latency" in incident.title.lower():
            chaos_net = [c for c in chaos if c.get("kind") == "networkchaos"]
            cause = "Elevated HTTP request latency detected."
            if chaos_net:
                cause += f" Correlated with active NetworkChaos: {chaos_net[0]['name']}."

            return DiagnosticReport(
                incident_id=incident.id,
                suspected_cause=cause,
                confidence=ConfidenceLevel.MEDIUM,
                evidence=evidence,
                recommended_runbook="high-latency",
                recommended_actions=[
                    {
                        "action_type": "verify_self_healing",
                        "target_resource": f"deploy/{service}",
                        "risk_level": "LOW",
                        "requires_approval": False,
                    }
                ],
            )

        # Diagnostic Hypothesis 5: General API / Worker Unavailable
        if service == "worker" or alertname == "WorkerProcessingFailures":
            return DiagnosticReport(
                incident_id=incident.id,
                suspected_cause="Worker service failed order processing cycle or polling loop interrupted.",
                confidence=ConfidenceLevel.HIGH,
                evidence=evidence,
                recommended_runbook="worker-failure",
                recommended_actions=[
                    {
                        "action_type": "restart_deployment",
                        "target_resource": "deploy/worker",
                        "risk_level": "LOW",
                        "requires_approval": False,
                    }
                ],
            )

        # Default fallback
        return DiagnosticReport(
            incident_id=incident.id,
            suspected_cause=f"Service '{service}' experiencing degradation indicated by alert {alertname}.",
            confidence=ConfidenceLevel.LOW,
            evidence=evidence,
            recommended_runbook="api-unavailable",
            recommended_actions=[
                {
                    "action_type": "verify_self_healing",
                    "target_resource": f"deploy/{service}",
                    "risk_level": "LOW",
                    "requires_approval": False,
                }
            ],
        )
