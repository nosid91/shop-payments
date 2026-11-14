from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CHAR, DateTime, ForeignKey, Numeric, Text, false, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Names given by the payments migration; the service tells unique violations apart by them.
UQ_PAYMENTS_USER_IDEMPOTENCY_KEY = "uq_payments_user_idempotency_key"
UQ_PAYMENTS_CART_ACTIVE = "uq_payments_cart_active"


class CartStatus(StrEnum):
    ACTIVE = "active"
    CHECKED_OUT = "checked_out"
    ABANDONED = "abandoned"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class FailureCode(StrEnum):
    CARD_DECLINED = "card_declined"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    CIRCUIT_OPEN = "circuit_open"


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    email: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Product(Base):
    __tablename__ = "products"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(precision=12, scale=2))
    currency: Mapped[str] = mapped_column(CHAR(3), server_default="USD")
    stock_quantity: Mapped[int] = mapped_column(server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CartItem(Base):
    __tablename__ = "cart_items"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    cart_id: Mapped[UUID] = mapped_column(ForeignKey("carts.id", ondelete="CASCADE"))
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int]
    unit_price: Mapped[Decimal] = mapped_column(Numeric(precision=12, scale=2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    product: Mapped[Product] = relationship()


class Cart(Base):
    __tablename__ = "carts"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(Text, server_default=CartStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    items: Mapped[list[CartItem]] = relationship()


class UserPaymentMethod(Base):
    __tablename__ = "user_payment_methods"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    provider_token: Mapped[str] = mapped_column(Text)
    last_four: Mapped[str | None] = mapped_column(CHAR(4))
    is_default: Mapped[bool] = mapped_column(server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    cart_id: Mapped[UUID] = mapped_column(ForeignKey("carts.id"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    payment_method_id: Mapped[UUID] = mapped_column(ForeignKey("user_payment_methods.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=12, scale=2))
    currency: Mapped[str] = mapped_column(CHAR(3))
    status: Mapped[str] = mapped_column(Text, server_default=PaymentStatus.PENDING)
    failure_code: Mapped[str | None] = mapped_column(Text)
    provider_charge_id: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
