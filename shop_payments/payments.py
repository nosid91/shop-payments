from enum import StrEnum
from uuid import UUID

from psycopg.errors import UniqueViolation
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from shop_payments.models import Cart, CartItem, CartStatus, Payment, PaymentStatus, UserPaymentMethod
from shop_payments.provider import MockPaymentProvider
from shop_payments.schemas import PaymentResponse, PaymentResult
from shop_payments.totals import calculate_cart_total

PAYMENT_IN_PROGRESS_MESSAGE = "The cart already has a payment in progress or paid"


class ErrorCode(StrEnum):
    CART_NOT_FOUND = "cart_not_found"
    CART_NOT_PAYABLE = "cart_not_payable"
    PAYMENT_IN_PROGRESS = "payment_in_progress"
    CART_EMPTY = "cart_empty"
    MIXED_CURRENCIES = "mixed_currencies"
    INVALID_AMOUNT = "invalid_amount"
    PAYMENT_METHOD_NOT_FOUND = "payment_method_not_found"
    NO_DEFAULT_PAYMENT_METHOD = "no_default_payment_method"
    IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"


class PaymentError(Exception):
    def __init__(self, *, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _find_payment_method(*, session: Session, user_id: UUID, payment_method_id: UUID | None) -> UserPaymentMethod:
    query = select(UserPaymentMethod).where(UserPaymentMethod.user_id == user_id)
    if payment_method_id is None:
        query = query.where(UserPaymentMethod.is_default).order_by(UserPaymentMethod.created_at.desc()).limit(1)
    else:
        query = query.where(UserPaymentMethod.id == payment_method_id)
    return session.scalars(query).one()


def start_payment(
    *,
    session_factory: sessionmaker[Session],
    provider: MockPaymentProvider,
    user_id: UUID,
    cart_id: UUID,
    idempotency_key: str,
    payment_method_id: UUID | None,
) -> PaymentResult:
    with session_factory.begin() as session:
        cart = session.scalars(
            select(Cart)
            .where(Cart.id == cart_id, Cart.user_id == user_id)
            .options(selectinload(Cart.items).selectinload(CartItem.product))
            .with_for_update(),
        ).one()
        method = _find_payment_method(session=session, user_id=user_id, payment_method_id=payment_method_id)
        total = calculate_cart_total(items=cart.items)
        payment = Payment(
            user_id=user_id,
            cart_id=cart_id,
            payment_method_id=method.id,
            amount=total.amount,
            currency=total.currency,
            idempotency_key=idempotency_key,
        )
        session.add(payment)
        try:
            session.flush()
        except IntegrityError as error:
            if not isinstance(error.orig, UniqueViolation):
                raise
            raise PaymentError(code=ErrorCode.PAYMENT_IN_PROGRESS, message=PAYMENT_IN_PROGRESS_MESSAGE) from error
        payment_id = payment.id
        token = method.provider_token

    result = provider.charge(
        token=token,
        amount=total.amount,
        currency=total.currency,
        idempotency_key=str(payment_id),
    )

    with session_factory.begin() as session:
        payment = session.get_one(entity=Payment, ident=payment_id)
        if result.approved:
            payment.status = PaymentStatus.SUCCEEDED
            payment.provider_charge_id = result.charge_id
            cart = session.get_one(entity=Cart, ident=cart_id)
            cart.status = CartStatus.CHECKED_OUT
            cart.updated_at = func.now()
        return PaymentResult(payment=PaymentResponse.model_validate(obj=payment), replayed=False)
