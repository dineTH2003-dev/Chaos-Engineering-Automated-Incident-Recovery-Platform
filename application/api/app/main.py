"""FastAPI main application module for ResilienceLab API."""

import logging
import signal
import sys
from contextlib import asynccontextmanager
from typing import List

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import (
    check_db_connection,
    get_db,
    shutdown_db,
    startup_db,
)
from app.metrics import (
    PrometheusMiddleware,
    application_errors_total,
    database_connection_errors_total,
    metrics_endpoint,
)
from app.models import Order, OrderCreate, OrderResponse

# Configure structured application logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger(settings.APP_NAME)


def setup_signal_handlers() -> None:
    """Configure signal handlers to log graceful shutdown on SIGTERM and SIGINT."""
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            prev_handler = signal.getsignal(sig)

            def _signal_handler(signum: int, frame, orig=prev_handler) -> None:
                sig_name = signal.Signals(signum).name
                logger.info(
                    "Received signal %s (%d), initiating graceful shutdown...",
                    sig_name,
                    signum,
                )
                if callable(orig):
                    orig(signum, frame)
                elif orig == signal.SIG_DFL:
                    sys.exit(0)

            signal.signal(sig, _signal_handler)
        except (ValueError, AttributeError) as exc:
            logger.debug("Signal handler registration skipped for %s: %s", sig, exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown lifecycle events."""
    setup_signal_handlers()
    logger.info("Starting %s (log_level=%s)...", settings.APP_NAME, settings.LOG_LEVEL)
    startup_db(max_attempts=3, initial_backoff=1.0)
    yield
    logger.info("Graceful shutdown in progress: disposing database resources...")
    shutdown_db()
    logger.info("%s shutdown complete.", settings.APP_NAME)


app = FastAPI(
    title="ResilienceLab API",
    description="Chaos Engineering and Automated Incident Recovery Platform API",
    version="1.0.0",
    lifespan=lifespan,
)

# Instrument all requests with Prometheus metrics
app.add_middleware(PrometheusMiddleware)


# --- Exception Handlers ---


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(
    request: Request, exc: SQLAlchemyError
) -> JSONResponse:
    """Handle uncaught SQLAlchemy database errors."""
    logger.error(
        "Uncaught database error on %s %s: %s",
        request.method,
        request.url.path,
        exc,
    )
    application_errors_total.labels(error_type="database_error").inc()
    database_connection_errors_total.inc()
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Database error occurred while processing request"},
    )


@app.exception_handler(Exception)
async def general_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Handle unhandled general exceptions."""
    logger.error(
        "Unhandled server error on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=True,
    )
    application_errors_total.labels(error_type=type(exc).__name__).inc()
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal server error occurred"},
    )


# --- Health & Readiness Endpoints ---


@app.get("/", tags=["system"], summary="Service Information")
def root() -> dict[str, str]:
    """Return service identification and running status."""
    return {
        "service": "resiliencelab-api",
        "status": "running",
        "version": "1.0.0",
    }


@app.get("/health", tags=["system"], summary="Liveness Probe")
def health() -> dict[str, str]:
    """Liveness probe. Always returns HTTP 200 if process is alive."""
    return {"status": "healthy"}


@app.get("/ready", tags=["system"], summary="Readiness Probe")
def ready() -> JSONResponse:
    """Readiness probe. Checks database connectivity."""
    if check_db_connection():
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ready", "database": "connected"},
        )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "not_ready", "database": "disconnected"},
    )


# --- Business Endpoints ---


@app.get(
    "/api/orders",
    response_model=List[OrderResponse],
    status_code=status.HTTP_200_OK,
    tags=["orders"],
    summary="List Orders",
)
def list_orders(
    limit: int = Query(
        default=100, ge=1, le=1000, description="Maximum orders to retrieve"
    ),
    offset: int = Query(
        default=0, ge=0, description="Number of orders to skip"
    ),
    db: Session = Depends(get_db),
) -> List[Order]:
    """Retrieve a paginated list of orders from the database."""
    try:
        orders = (
            db.query(Order).order_by(Order.id.asc()).offset(offset).limit(limit).all()
        )
        return orders
    except SQLAlchemyError as exc:
        logger.error("Database error while listing orders: %s", exc)
        application_errors_total.labels(error_type="database_error").inc()
        database_connection_errors_total.inc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error occurred while retrieving orders",
        )


@app.post(
    "/api/orders",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["orders"],
    summary="Create Order",
)
def create_order(
    order_in: OrderCreate, db: Session = Depends(get_db)
) -> Order:
    """Create a new order in the database with default pending status."""
    try:
        order = Order(customer_name=order_in.customer_name)
        db.add(order)
        db.commit()
        db.refresh(order)
        return order
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Database error while creating order: %s", exc)
        application_errors_total.labels(error_type="database_error").inc()
        database_connection_errors_total.inc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error occurred while creating order",
        )


# --- Prometheus Metrics Endpoint ---


@app.get(
    "/metrics",
    tags=["system"],
    summary="Prometheus Metrics",
    include_in_schema=False,
)
def metrics() -> Response:
    """Expose Prometheus metrics in exposition format for scraping."""
    return metrics_endpoint()
