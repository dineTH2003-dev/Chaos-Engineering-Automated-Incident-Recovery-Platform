"""Main entrypoint and lifecycle loop for ResilienceLab Worker service."""

import logging
import signal
import sys
import threading
import time

from prometheus_client import Counter, Gauge, Histogram, start_http_server

from app.config import WorkerSettings, get_settings
from app.processor import OrderProcessor, get_processor

# ---------------------------------------------------------
# Prometheus Metrics Instrumentation
# ---------------------------------------------------------
ORDERS_PROCESSED = Counter(
    "orders_processed_total",
    "Total number of orders successfully processed by the worker",
)

ORDERS_PROCESSING_ERRORS = Counter(
    "orders_processing_errors_total",
    "Total number of errors encountered while processing orders",
)

WORKER_POLL_DURATION = Histogram(
    "worker_poll_duration_seconds",
    "Duration of worker polling and processing cycles in seconds",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)

WORKER_LAST_POLL = Gauge(
    "worker_last_poll_timestamp",
    "Timestamp of the last worker poll cycle in seconds since epoch",
)

logger = logging.getLogger("resiliencelab.worker")


def setup_logging(log_level: str) -> None:
    """Configure structured logging for worker application."""
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def run_worker() -> None:
    """Run the worker main loop with polling, processing, metrics, and graceful shutdown."""
    settings: WorkerSettings = get_settings()
    setup_logging(settings.log_level)

    logger.info("Initializing ResilienceLab Worker service")
    logger.info("Configuration: backend=%s, poll_interval=%.2fs, batch_size=%d, processing_delay=%.2fs",
                settings.queue_backend, settings.poll_interval_seconds,
                settings.batch_size, settings.processing_delay_seconds)

    # 1. Start Prometheus metrics HTTP server
    try:
        start_http_server(port=settings.metrics_port, addr="0.0.0.0")
        logger.info("Prometheus metrics server exposed on port %d (/metrics)", settings.metrics_port)
    except Exception as exc:
        logger.error("Failed to start Prometheus metrics server on port %d: %s", settings.metrics_port, exc)
        sys.exit(1)

    # 2. Instantiate processor based on QUEUE_BACKEND
    try:
        processor: OrderProcessor = get_processor(settings)
    except Exception as exc:
        logger.error("Failed to initialize order processor: %s", exc)
        sys.exit(1)

    # 3. Setup signal handling for graceful shutdown
    shutdown_event = threading.Event()

    def handle_signal(signum: int, frame) -> None:
        try:
            sig_name = signal.Signals(signum).name
        except Exception:
            sig_name = str(signum)

        if shutdown_event.is_set():
            logger.warning("Received repeated signal %s: forcing exit immediately", sig_name)
            sys.exit(1)

        logger.info("Received signal %s: graceful shutdown initiated (will finish current batch)...", sig_name)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    logger.info("Worker loop started. Polling for orders...")

    # 4. Main polling loop
    try:
        while not shutdown_event.is_set():
            poll_start = time.perf_counter()
            try:
                processed_count = processor.process_orders()
                if processed_count > 0:
                    ORDERS_PROCESSED.inc(processed_count)
                    logger.info("Batch processed successfully: %d orders completed", processed_count)
                else:
                    logger.debug("Poll completed: no pending orders")
            except Exception as exc:
                ORDERS_PROCESSING_ERRORS.inc()
                logger.error("Error during order processing cycle: %s", exc)
            finally:
                duration = time.perf_counter() - poll_start
                WORKER_POLL_DURATION.observe(duration)
                WORKER_LAST_POLL.set_to_current_time()

            # Wait for the next poll cycle or until shutdown is signaled
            if not shutdown_event.is_set():
                shutdown_event.wait(timeout=settings.poll_interval_seconds)

    finally:
        logger.info("Shutting down worker: closing connections and releasing resources...")
        try:
            processor.close()
        except Exception as exc:
            logger.warning("Error during processor cleanup: %s", exc)
        logger.info("Worker stopped cleanly.")


if __name__ == "__main__":
    run_worker()
