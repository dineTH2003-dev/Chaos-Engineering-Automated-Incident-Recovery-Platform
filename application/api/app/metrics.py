"""Prometheus metrics and request instrumentation middleware for ResilienceLab API."""

import time
from typing import Callable
from fastapi import Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match

# Prometheus Metric Definitions
http_requests_total = Counter(
    "http_requests_total",
    "Total count of HTTP requests processed",
    labelnames=["method", "route", "status_code"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    labelnames=["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

application_errors_total = Counter(
    "application_errors_total",
    "Total count of application errors",
    labelnames=["error_type"],
)

database_connections_active = Gauge(
    "database_connections_active",
    "Current count of active database connections in use",
)

database_connection_errors_total = Counter(
    "database_connection_errors_total",
    "Total count of database connection errors",
)


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Starlette middleware to automatically record Prometheus metrics for all HTTP requests."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Intercept HTTP request, measure execution duration, and record Prometheus metrics.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware or route handler.

        Returns:
            Response: The HTTP response.
        """
        start_time = time.perf_counter()
        method = request.method
        status_code = 500
        exception_raised = False

        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as exc:
            exception_raised = True
            status_code = 500
            error_type = type(exc).__name__
            application_errors_total.labels(error_type=error_type).inc()
            raise exc
        finally:
            duration = max(time.perf_counter() - start_time, 0.0)
            route_path = self._resolve_route_path(request, status_code)

            http_requests_total.labels(
                method=method,
                route=route_path,
                status_code=str(status_code),
            ).inc()

            http_request_duration_seconds.labels(
                method=method,
                route=route_path,
            ).observe(duration)

            if status_code >= 500 and not exception_raised:
                application_errors_total.labels(error_type=f"http_{status_code}").inc()

    def _resolve_route_path(self, request: Request, status_code: int) -> str:
        """Resolve a bounded route template to avoid metric cardinality explosion.

        Args:
            request: The HTTP request.
            status_code: The resolved response HTTP status code.

        Returns:
            str: The route template path, or fallback label if unmatched.
        """
        route = request.scope.get("route")
        if route and hasattr(route, "path"):
            return route.path

        app = getattr(request, "app", None)
        if app and hasattr(app, "routes"):
            for r in app.routes:
                match, _ = r.matches(request.scope)
                if match == Match.FULL:
                    return getattr(r, "path", "unknown")

        if status_code == 404:
            return "not_found"

        return "unknown"


def metrics_endpoint() -> Response:
    """Handler returning Prometheus metrics in text format.

    Returns:
        Response: Plaintext response with Prometheus metrics exposition format.
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
