"""Order processor implementations for ResilienceLab Worker.

Supports multiple queue backends via the OrderProcessor abstract base class.
"""

from abc import ABC, abstractmethod
import logging
import time
from typing import List

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    func,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.config import WorkerSettings

logger = logging.getLogger("resiliencelab.worker.processor")

metadata = MetaData()

orders_table = Table(
    "orders",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("customer_name", String(100), nullable=False),
    Column("status", String(30), nullable=False, default="pending"),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
)


class OrderProcessor(ABC):
    """Abstract base class for order processing backends."""

    @abstractmethod
    def process_orders(self) -> int:
        """Process a batch of pending orders.

        Returns:
            int: The count of successfully processed orders.
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """Release underlying connections and resources."""
        pass


class PostgresOrderProcessor(OrderProcessor):
    """PostgreSQL-backed order processor using FOR UPDATE SKIP LOCKED.

    Safe for concurrent workers and stateless restart scenarios.
    """

    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings
        logger.info(
            "Initializing PostgresOrderProcessor with batch_size=%d, processing_delay=%.2fs",
            settings.batch_size,
            settings.processing_delay_seconds,
        )
        self.engine: Engine = create_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            pool_recycle=settings.db_pool_recycle,
            pool_pre_ping=True,  # Discards stale connections during network disruptions
        )

    def process_orders(self) -> int:
        """Query pending orders with FOR UPDATE SKIP LOCKED, process, and complete.

        1. Locks pending orders up to batch_size, skipping already locked rows.
        2. Sets status to 'processing'.
        3. Simulates work delay (time.sleep).
        4. Sets status to 'completed'.
        5. Commits transaction.

        On failure, the transaction is rolled back and rows revert to 'pending'.

        Returns:
            int: Number of orders successfully processed in this batch.
        """
        try:
            with self.engine.connect() as conn:
                with conn.begin():
                    # 1. Fetch pending orders with FOR UPDATE SKIP LOCKED
                    select_stmt = (
                        select(orders_table.c.id, orders_table.c.customer_name)
                        .where(orders_table.c.status == "pending")
                        .order_by(orders_table.c.created_at.asc())
                        .limit(self.settings.batch_size)
                        .with_for_update(skip_locked=True)
                    )
                    rows = conn.execute(select_stmt).fetchall()

                    if not rows:
                        logger.debug("No pending orders found to process")
                        return 0

                    order_ids: List[int] = [row[0] for row in rows]
                    logger.info(
                        "Acquired exclusive lock on %d order(s): IDs %s",
                        len(order_ids),
                        order_ids,
                        extra={"order_count": len(order_ids), "order_ids": order_ids},
                    )

                    # 2. Update status to 'processing'
                    update_processing_stmt = (
                        update(orders_table)
                        .where(orders_table.c.id.in_(order_ids))
                        .values(status="processing")
                    )
                    conn.execute(update_processing_stmt)
                    logger.info(
                        "Transitioned order(s) %s status to 'processing'",
                        order_ids,
                        extra={"order_ids": order_ids, "status": "processing"},
                    )

                    # 3. Simulate processing work
                    if self.settings.processing_delay_seconds > 0:
                        logger.debug(
                            "Simulating work: sleeping for %.2f seconds",
                            self.settings.processing_delay_seconds,
                        )
                        time.sleep(self.settings.processing_delay_seconds)

                    # 4. Update status to 'completed'
                    update_completed_stmt = (
                        update(orders_table)
                        .where(orders_table.c.id.in_(order_ids))
                        .values(status="completed")
                    )
                    conn.execute(update_completed_stmt)
                    logger.info(
                        "Transitioned order(s) %s status to 'completed'",
                        order_ids,
                        extra={"order_ids": order_ids, "status": "completed"},
                    )

                    # conn.begin() context manager commits automatically here
                    return len(order_ids)

        except SQLAlchemyError as exc:
            logger.error(
                "Database error during order processing (transaction automatically rolled back): %s",
                exc,
                exc_info=True,
                extra={"error_type": type(exc).__name__, "error_detail": str(exc)},
            )
            raise
        except Exception as exc:
            logger.error(
                "Unexpected error during order processing: %s",
                exc,
                exc_info=True,
                extra={"error_type": type(exc).__name__, "error_detail": str(exc)},
            )
            raise

    def close(self) -> None:
        """Close and dispose all connections in the database pool."""
        logger.info("Disposing PostgreSQL connection pool")
        self.engine.dispose()


class RedisOrderProcessor(OrderProcessor):
    """Future Redis-backed order processor for queue-based task distribution."""

    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings
        logger.info("Initializing RedisOrderProcessor (backend: redis)")

    def process_orders(self) -> int:
        """Placeholder for Redis queue processing."""
        raise NotImplementedError(
            "RedisOrderProcessor is not yet implemented. Set QUEUE_BACKEND='postgres'."
        )

    def close(self) -> None:
        """Placeholder to close Redis client connections."""
        logger.info("Closing RedisOrderProcessor connections")


def get_processor(settings: WorkerSettings) -> OrderProcessor:
    """Factory function to instantiate the configured OrderProcessor backend.

    Args:
        settings: WorkerSettings containing configuration.

    Returns:
        OrderProcessor: Concrete order processor instance.

    Raises:
        ValueError: If an unknown queue backend is specified.
    """
    backend = settings.queue_backend.lower()
    if backend == "postgres":
        return PostgresOrderProcessor(settings)
    elif backend == "redis":
        return RedisOrderProcessor(settings)
    else:
        raise ValueError(
            f"Unsupported QUEUE_BACKEND: '{settings.queue_backend}'. Supported backends: 'postgres', 'redis'."
        )
