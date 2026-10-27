"""
ResilienceLab — Health Checker & Continuous Availability Prober
Monitors API /health and /ready endpoints to compute availability, latency percentiles, and degradation.
"""

import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("chaos.health")


class HealthChecker:
    """Probes service endpoints and records continuous telemetry during chaos events."""

    def __init__(self, base_url: str = "http://localhost:8000", probe_interval: float = 0.5):
        self.base_url = base_url.rstrip("/")
        self.probe_interval = probe_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.probes: List[Dict[str, Any]] = []

    def check_endpoint(self, path: str, timeout: float = 2.0) -> Dict[str, Any]:
        """Send a single HTTP probe to an endpoint."""
        url = f"{self.base_url}{path}"
        start = time.perf_counter()
        probe_record = {
            "timestamp": time.time(),
            "path": path,
            "status_code": 0,
            "latency_ms": 0.0,
            "success": False,
            "error": None,
        }
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "ResilienceLab-HealthProber/1.0"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    elapsed_ms = (time.perf_counter() - start) * 1000.0
                    probe_record["status_code"] = resp.status
                    probe_record["latency_ms"] = round(elapsed_ms, 2)
                    probe_record["success"] = (resp.status == 200)
                    probe_record["error"] = None
                    return probe_record
            except urllib.error.HTTPError as e:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                probe_record["status_code"] = e.code
                probe_record["latency_ms"] = round(elapsed_ms, 2)
                probe_record["error"] = f"HTTP {e.code}"
                return probe_record
            except (urllib.error.URLError, ConnectionResetError, OSError) as e:
                if attempt < 2:
                    time.sleep(0.3)
                    continue
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                probe_record["latency_ms"] = round(elapsed_ms, 2)
                probe_record["error"] = str(e)
                return probe_record
            except Exception as e:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                probe_record["latency_ms"] = round(elapsed_ms, 2)
                probe_record["error"] = str(e)
                return probe_record

        return probe_record

    def check_steady_state(self) -> Tuple[bool, str]:
        """Verify baseline system health prior to injecting chaos."""
        health = self.check_endpoint("/health")
        if not health["success"]:
            return False, f"API /health probe failed: status {health['status_code']}, error: {health['error']}"

        ready = self.check_endpoint("/ready")
        if not ready["success"]:
            return False, f"API /ready probe failed (database check): status {ready['status_code']}, error: {ready['error']}"

        return True, "Steady-state healthy (/health and /ready returned 200 OK)"

    def _probe_worker(self):
        """Worker loop that continuously sends probes until stopped."""
        while not self._stop_event.is_set():
            probe = self.check_endpoint("/ready")
            self.probes.append(probe)
            time.sleep(self.probe_interval)

    def start_probing(self):
        """Start continuous probing in background thread."""
        self.probes = []
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._probe_worker, daemon=True)
        self._thread.start()
        logger.info(f"Started continuous health probing on {self.base_url}/ready (interval: {self.probe_interval}s)")

    def stop_probing(self) -> Dict[str, Any]:
        """Stop background probing and compute summary statistics."""
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            self._thread.join(timeout=3.0)
        logger.info(f"Continuous probing stopped. Total samples recorded: {len(self.probes)}")

        return self.get_summary()

    def get_summary(self) -> Dict[str, Any]:
        """Compute statistical summary of recorded probes."""
        total = len(self.probes)
        if total == 0:
            return {
                "total_probes": 0,
                "successful_probes": 0,
                "degraded_probes": 0,
                "failed_probes": 0,
                "availability_pct": 100.0,
                "p50_latency_ms": 0.0,
                "p95_latency_ms": 0.0,
                "p99_latency_ms": 0.0,
            }

        successful = [p for p in self.probes if p["status_code"] == 200]
        degraded = [p for p in self.probes if p["status_code"] == 503]
        failed = [p for p in self.probes if p["status_code"] not in (200, 503)]

        latencies = sorted(p["latency_ms"] for p in self.probes)
        p50 = latencies[int(len(latencies) * 0.50)] if latencies else 0.0
        p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0.0
        p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0.0

        availability = (len(successful) / total) * 100.0

        return {
            "total_probes": total,
            "successful_probes": len(successful),
            "degraded_probes": len(degraded),
            "failed_probes": len(failed),
            "availability_pct": round(availability, 2),
            "p50_latency_ms": round(p50, 2),
            "p95_latency_ms": round(p95, 2),
            "p99_latency_ms": round(p99, 2),
        }
