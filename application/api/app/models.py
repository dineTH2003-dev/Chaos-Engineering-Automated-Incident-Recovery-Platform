"""Data models and Pydantic schemas for ResilienceLab orders."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Column, DateTime, Integer, String, func

from app.database import Base


class Order(Base):
    """SQLAlchemy ORM model representing an order entity in PostgreSQL."""

    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    customer_name = Column(String(100), nullable=False)
    status = Column(String(30), default="pending", index=True)
    created_at = Column(DateTime, default=func.now())


class OrderCreate(BaseModel):
    """Pydantic schema for creating a new order."""

    customer_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Name of the customer placing the order",
        examples=["Alice Smith"],
    )


class OrderResponse(BaseModel):
    """Pydantic schema for serialized order response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_name: str
    status: str
    created_at: Optional[datetime] = None
