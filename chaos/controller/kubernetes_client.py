"""
ResilienceLab — Kubernetes Operations & Safety Enforcement Client
Executes guarded Kubernetes operations with blast radius controls.
"""

import json
import logging
import subprocess
from typing import Any, Dict, List, Optional

logger = logging.getLogger("chaos.k8s")

ALLOWED_NAMESPACE = "resiliencelab"
FORBIDDEN_NAMESPACES = {"kube-system", "monitoring", "chaos-mesh", "default"}


class KubernetesSafetyViolation(Exception):
    """Raised when an operation violates chaos safety policy."""
    pass


class KubernetesClient:
    """Client for Kubernetes cluster interactions with built-in safety controls."""

    def __init__(self, target_namespace: str = ALLOWED_NAMESPACE):
        if target_namespace in FORBIDDEN_NAMESPACES or target_namespace != ALLOWED_NAMESPACE:
            raise KubernetesSafetyViolation(
                f"Target namespace '{target_namespace}' violates safety policy. Only '{ALLOWED_NAMESPACE}' is permitted."
            )
        self.namespace = target_namespace

    def _run_cmd(self, cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
        """Run a shell command via subprocess."""
        cmd_str = " ".join(cmd)
        logger.debug(f"Executing: {cmd_str}")
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=check)
            return res
        except subprocess.CalledProcessError as e:
            logger.error(f"Command '{cmd_str}' failed with code {e.returncode}: {e.stderr.strip()}")
            raise

    def get_pods(self, label_selector: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get pods in target namespace matching label selector."""
        cmd = ["kubectl", "get", "pods", "-n", self.namespace, "-o", "json"]
        if label_selector:
            cmd.extend(["-l", label_selector])
        
        res = self._run_cmd(cmd)
        data = json.loads(res.stdout)
        pods = []
        for item in data.get("items", []):
            metadata = item.get("metadata", {})
            status = item.get("status", {})
            container_statuses = status.get("containerStatuses", [])
            ready = any(cs.get("ready", False) for cs in container_statuses) if container_statuses else False
            restart_count = sum(cs.get("restartCount", 0) for cs in container_statuses)
            pods.append({
                "name": metadata.get("name"),
                "phase": status.get("phase"),
                "ready": ready,
                "restarts": restart_count,
                "labels": metadata.get("labels", {}),
                "start_time": status.get("startTime"),
            })
        return pods

    def get_deployment_status(self, name: str) -> Dict[str, Any]:
        """Get deployment replica counts and status."""
        cmd = ["kubectl", "get", "deployment", name, "-n", self.namespace, "-o", "json"]
        res = self._run_cmd(cmd)
        data = json.loads(res.stdout)
        status = data.get("status", {})
        spec = data.get("spec", {})
        return {
            "name": name,
            "desired_replicas": spec.get("replicas", 0),
            "replicas": status.get("replicas", 0),
            "ready_replicas": status.get("readyReplicas", 0),
            "updated_replicas": status.get("updatedReplicas", 0),
            "available_replicas": status.get("availableReplicas", 0),
        }

    def verify_workload_chaos_enabled(self, label_selector: str) -> bool:
        """Verify that target workload pods have chaos-enabled: 'true'."""
        pods = self.get_pods(label_selector)
        if not pods:
            logger.warning(f"No pods found matching '{label_selector}'")
            return False
        for pod in pods:
            labels = pod.get("labels", {})
            if labels.get("chaos-enabled") != "true":
                logger.error(f"Pod {pod['name']} lacks 'chaos-enabled: true' label! Refusing injection.")
                return False
        return True

    def apply_manifest(self, manifest_path: str) -> bool:
        """Apply a chaos experiment manifest with safety checks."""
        # Pre-validate manifest contents
        with open(manifest_path, "r", encoding="utf-8") as f:
            content = f.read()
            for forbidden in FORBIDDEN_NAMESPACES:
                if f"namespace: {forbidden}" in content:
                    raise KubernetesSafetyViolation(f"Manifest targets forbidden namespace '{forbidden}'!")
        
        cmd = ["kubectl", "apply", "-f", manifest_path]
        res = self._run_cmd(cmd, check=False)
        return res.returncode == 0

    def delete_manifest(self, manifest_path: str) -> bool:
        """Delete an applied chaos experiment manifest."""
        cmd = ["kubectl", "delete", "-f", manifest_path, "--ignore-not-found=true", "--timeout=30s"]
        res = self._run_cmd(cmd, check=False)
        return res.returncode == 0

    def delete_all_active_chaos(self) -> bool:
        """Emergency abort: remove all active Chaos Mesh resources in target namespace."""
        cmd = [
            "kubectl", "delete",
            "podchaos,networkchaos,stresschaos,iochaos,httpchaos,dnschaos",
            "--all", "-n", self.namespace,
            "--ignore-not-found=true",
            "--timeout=15s",
        ]
        res = self._run_cmd(cmd, check=False)
        return res.returncode == 0

    def scale_deployment(self, name: str, replicas: int) -> bool:
        """Scale deployment replicas with guardrails."""
        if replicas < 0 or replicas > 5:
            raise KubernetesSafetyViolation(f"Replica count {replicas} exceeds safe bounds (0-5).")
        cmd = ["kubectl", "scale", "deployment", name, "-n", self.namespace, f"--replicas={replicas}"]
        res = self._run_cmd(cmd, check=False)
        return res.returncode == 0

    def wait_for_deployment_ready(self, name: str, timeout: int = 60) -> bool:
        """Wait for deployment to reach complete rollout."""
        cmd = ["kubectl", "rollout", "status", f"deployment/{name}", "-n", self.namespace, f"--timeout={timeout}s"]
        res = self._run_cmd(cmd, check=False)
        return res.returncode == 0
