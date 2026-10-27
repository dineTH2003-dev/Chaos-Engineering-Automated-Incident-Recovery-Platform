"""
ResilienceLab — Prometheus Metrics Client
Handles querying Prometheus instant and range metrics for chaos experiment analysis.
"""

import json
import logging
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger("chaos.prometheus")


class PrometheusClient:
    """Client for querying Prometheus metrics server."""

    def __init__(self, base_url: str = "http://localhost:9091", timeout: int = 5):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def query(self, expr: str) -> Optional[List[Dict[str, Any]]]:
        """Execute an instant PromQL query."""
        url = f"{self.base_url}/api/v1/query?{urllib.parse.urlencode({'query': expr})}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ResilienceLab-ChaosController/1.0"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("status") == "success":
                    return data.get("data", {}).get("result", [])
                logger.warning(f"Prometheus query returned status {data.get('status')}: {expr}")
                return None
        except Exception as e:
            logger.warning(f"Failed to query Prometheus at {self.base_url}: {e}")
            return None

    def get_scalar_value(self, expr: str, default: float = 0.0) -> float:
        """Query a single scalar value from a PromQL expression."""
        results = self.query(expr)
        if results and len(results) > 0:
            val = results[0].get("value", [None, None])[1]
            try:
                return float(val) if val is not None else default
            except (ValueError, TypeError):
                return default
        return default

    def get_active_alerts(self, namespace: str = "resiliencelab") -> List[Dict[str, Any]]:
        """Query active firing or pending alerts for a given namespace."""
        expr = f'ALERTS{{namespace="{namespace}"}}'
        results = self.query(expr)
        alerts = []
        if results:
            for item in results:
                metric = item.get("metric", {})
                alerts.append({
                    "alertname": metric.get("alertname"),
                    "alertstate": metric.get("alertstate"),
                    "severity": metric.get("severity"),
                    "pod": metric.get("pod"),
                })
        return alerts

    def get_api_p95_latency_ms(self) -> float:
        """Get 95th percentile HTTP request duration in milliseconds."""
        expr = 'histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{job="api-service"}[1m])) by (le)) * 1000'
        return round(self.get_scalar_value(expr, default=0.0), 2)

    def get_api_error_rate_pct(self) -> float:
        """Get API HTTP 5xx error rate percentage."""
        expr = '(sum(rate(http_requests_total{job="api-service", status=~"5.."}[1m])) or vector(0)) / (sum(rate(http_requests_total{job="api-service"}[1m])) or vector(1)) * 100'
        return round(self.get_scalar_value(expr, default=0.0), 2)

    def get_api_ready_replicas(self) -> int:
        """Get number of ready API replicas."""
        expr = 'sum(kube_pod_status_ready{namespace="resiliencelab", condition="true", pod=~"api.*"})'
        return int(self.get_scalar_value(expr, default=0))

    def get_api_cpu_usage_cores(self) -> float:
        """Get total API CPU consumption in cores."""
        expr = 'sum(node_namespace_pod_container:container_cpu_usage_seconds_total:sum_irate{namespace="resiliencelab", pod=~"api.*"}) or sum(rate(container_cpu_usage_seconds_total{namespace="resiliencelab", container="api"}[1m]))'
        return round(self.get_scalar_value(expr, default=0.0), 4)
