from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shop_payments.models import FailureCode, PaymentStatus


class PaymentPath(BaseModel):
    cart_id: UUID


class PaymentHeaders(BaseModel):
    # 255 is the limit Stripe documents for idempotency keys.
    idempotency_key: str = Field(min_length=1, max_length=255)


class PaymentBody(BaseModel):
    payment_method_id: UUID | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_missing_body(cls, data: object) -> object:
        return {} if data is None else data


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cart_id: UUID
    status: PaymentStatus
    amount: Decimal
    currency: str
    failure_code: FailureCode | None
    created_at: datetime


class PaymentResult(BaseModel):
    payment: PaymentResponse
    replayed: bool


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
