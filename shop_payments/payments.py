from decimal import Decimal
from enum import StrEnum
from uuid import UUID

import psycopg
import pybreaker
from pydantic import BaseModel
from sqlalchemy import Select, exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from shop_payments.models import (
    UQ_PAYMENTS_CART_ACTIVE,
    UQ_PAYMENTS_USER_IDEMPOTENCY_KEY,
    Cart,
    CartItem,
    CartStatus,
    FailureCode,
    Payment,
    PaymentStatus,
    UserPaymentMethod,
)
from shop_payments.provider import MockPaymentProvider, ProviderTimeoutError, ProviderUnavailableError
from shop_payments.schemas import PaymentResponse, PaymentResult
from shop_payments.totals import CartTotal, MixedCurrenciesError, calculate_cart_total

# The largest value NUMERIC(12, 2) can hold.
MAX_AMOUNT = Decimal("9999999999.99")
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


class _KeyRaceError(Exception): ...


class _Charge(BaseModel):
    pending: PaymentResponse
    token: str


def _find_payment_method(*, session: Session, user_id: UUID, payment_method_id: UUID | None) -> UserPaymentMethod:
    query = select(UserPaymentMethod).where(UserPaymentMethod.user_id == user_id)
    if payment_method_id is None:
        method = session.scalars(
            query.where(UserPaymentMethod.is_default).order_by(UserPaymentMethod.created_at.desc()).limit(1),
        ).first()
        if method is None:
            raise PaymentError(code=ErrorCode.NO_DEFAULT_PAYMENT_METHOD, message="No default payment method")
    else:
        method = session.scalars(query.where(UserPaymentMethod.id == payment_method_id)).first()
        if method is None:
            raise PaymentError(code=ErrorCode.PAYMENT_METHOD_NOT_FOUND, message="Payment method not found")
    return method


def _calculate_payable_total(*, cart: Cart) -> CartTotal:
    try:
        total = calculate_cart_total(items=cart.items)
    except MixedCurrenciesError as error:
        raise PaymentError(code=ErrorCode.MIXED_CURRENCIES, message="Cart items have different currencies") from error
    if not 0 < total.amount <= MAX_AMOUNT:
        raise PaymentError(code=ErrorCode.INVALID_AMOUNT, message="Cart total is outside the payable range")
    return total


def _reject_pending_payment(*, session: Session, cart_id: UUID) -> None:
    pending_exists = exists().where(Payment.cart_id == cart_id, Payment.status == PaymentStatus.PENDING)
    if session.scalar(select(pending_exists)):
        raise PaymentError(code=ErrorCode.PAYMENT_IN_PROGRESS, message=PAYMENT_IN_PROGRESS_MESSAGE)


def _record_outcome(
    *,
    session: Session,
    payment_id: UUID,
    cart_id: UUID,
    charge_id: str | None,
    failure_code: FailureCode | None,
) -> Payment:
    payment = session.get_one(entity=Payment, ident=payment_id)
    if failure_code is None:
        payment.status = PaymentStatus.SUCCEEDED
        payment.provider_charge_id = charge_id
        cart = session.get_one(entity=Cart, ident=cart_id)
        cart.status = CartStatus.CHECKED_OUT
        cart.updated_at = func.now()
    else:
        payment.status = PaymentStatus.FAILED
        payment.failure_code = failure_code
    return payment


def _replay(*, payment: Payment, cart_id: UUID, payment_method_id: UUID | None) -> PaymentResult:
    if payment.cart_id != cart_id or payment_method_id not in {None, payment.payment_method_id}:
        raise PaymentError(
            code=ErrorCode.IDEMPOTENCY_KEY_REUSED,
            message="The idempotency key was used for a different request",
        )
    return PaymentResult(payment=PaymentResponse.model_validate(obj=payment), replayed=True)


def _payment_by_key(*, user_id: UUID, idempotency_key: str) -> Select[tuple[Payment]]:
    return select(Payment).where(Payment.user_id == user_id, Payment.idempotency_key == idempotency_key)


def _create_pending_payment(
    *,
    session: Session,
    cart: Cart,
    idempotency_key: str,
    payment_method_id: UUID | None,
) -> _Charge:
    if cart.status != CartStatus.ACTIVE:
        raise PaymentError(code=ErrorCode.CART_NOT_PAYABLE, message="Cart is not active")
    _reject_pending_payment(session=session, cart_id=cart.id)
    if not cart.items:
        raise PaymentError(code=ErrorCode.CART_EMPTY, message="Cart has no items")
    method = _find_payment_method(session=session, user_id=cart.user_id, payment_method_id=payment_method_id)
    total = _calculate_payable_total(cart=cart)
    payment = Payment(
        user_id=cart.user_id,
        cart_id=cart.id,
        payment_method_id=method.id,
        amount=total.amount,
        currency=total.currency,
        idempotency_key=idempotency_key,
    )
    session.add(payment)
    try:
        session.flush()
    except IntegrityError as error:
        if not isinstance(error.orig, psycopg.errors.UniqueViolation):
            raise
        constraint = error.orig.diag.constraint_name
        if constraint == UQ_PAYMENTS_CART_ACTIVE:
            raise PaymentError(code=ErrorCode.PAYMENT_IN_PROGRESS, message=PAYMENT_IN_PROGRESS_MESSAGE) from error
        if constraint == UQ_PAYMENTS_USER_IDEMPOTENCY_KEY:
            # Another cart's request holds the same key in a transaction we cannot see, and its cart lock is not ours.
            raise _KeyRaceError from error
        raise
    return _Charge(pending=PaymentResponse.model_validate(obj=payment), token=method.provider_token)


def _open_payment(
    *,
    session_factory: sessionmaker[Session],
    user_id: UUID,
    cart_id: UUID,
    idempotency_key: str,
    payment_method_id: UUID | None,
) -> PaymentResult | _Charge:
    with session_factory.begin() as session:
        cart = session.scalars(
            select(Cart)
            .where(Cart.id == cart_id, Cart.user_id == user_id)
            .options(selectinload(Cart.items).selectinload(CartItem.product))
            .with_for_update(),
        ).one_or_none()
        if cart is None:
            raise PaymentError(code=ErrorCode.CART_NOT_FOUND, message="Cart not found")
        # After the lock: a request that waited for the original sees its payment. Before the cart checks: a replay
        # of a paid cart (checked out) or of an unresolved payment must get that payment back.
        existing = session.scalars(_payment_by_key(user_id=user_id, idempotency_key=idempotency_key)).one_or_none()
        if existing is not None:
            return _replay(payment=existing, cart_id=cart_id, payment_method_id=payment_method_id)
        return _create_pending_payment(
            session=session,
            cart=cart,
            idempotency_key=idempotency_key,
            payment_method_id=payment_method_id,
        )


def start_payment(
    *,
    session_factory: sessionmaker[Session],
    provider: MockPaymentProvider,
    breaker: pybreaker.CircuitBreaker,
    user_id: UUID,
    cart_id: UUID,
    idempotency_key: str,
    payment_method_id: UUID | None,
) -> PaymentResult:
    try:
        opened = _open_payment(
            session_factory=session_factory,
            user_id=user_id,
            cart_id=cart_id,
            idempotency_key=idempotency_key,
            payment_method_id=payment_method_id,
        )
    except _KeyRaceError:
        with session_factory.begin() as session:
            payment = session.scalars(_payment_by_key(user_id=user_id, idempotency_key=idempotency_key)).one()
            return _replay(payment=payment, cart_id=cart_id, payment_method_id=payment_method_id)
    if isinstance(opened, PaymentResult):
        return opened
    pending = opened.pending

    charge_id = None
    failure_code = None
    try:
        result = breaker.call(
            provider.charge,
            token=opened.token,
            amount=pending.amount,
            currency=pending.currency,
            idempotency_key=str(pending.id),
        )
    except pybreaker.CircuitBreakerError:
        failure_code = FailureCode.CIRCUIT_OPEN
    except ProviderUnavailableError:
        failure_code = FailureCode.PROVIDER_UNAVAILABLE
    except ProviderTimeoutError:
        # The money may have been charged: failing the payment would let the cart be paid a second time.
        return PaymentResult(payment=pending, replayed=False)
    else:
        charge_id = result.charge_id
        if not result.approved:
            failure_code = FailureCode.CARD_DECLINED

    with session_factory.begin() as session:
        payment = _record_outcome(
            session=session,
            payment_id=pending.id,
            cart_id=cart_id,
            charge_id=charge_id,
            failure_code=failure_code,
        )
        return PaymentResult(payment=PaymentResponse.model_validate(obj=payment), replayed=False)
