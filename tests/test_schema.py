from collections.abc import Callable
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker


def test_base_tables_exist(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        tables = set(
            session.scalars(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")),
        )

    assert {"users", "products", "carts", "cart_items", "user_payment_methods"} <= tables


def test_cart_status_is_checked(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        user_id = session.scalar(text("INSERT INTO users (email, name) VALUES ('a@example.com', 'A') RETURNING id"))

    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.execute(
            statement=text("INSERT INTO carts (user_id, status) VALUES (:user_id, 'paid')"),
            params={"user_id": user_id},
        )


class PaymentContext(BaseModel):
    user_id: UUID
    cart_id: UUID
    payment_method_id: UUID


@pytest.fixture
def payment_context(
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
) -> PaymentContext:
    user_id = add_user()
    return PaymentContext(
        user_id=user_id,
        cart_id=add_cart(user_id=user_id, items=[(Decimal("10.00"), 1, "USD")]),
        payment_method_id=add_payment_method(user_id=user_id),
    )


def insert_payment(
    *,
    session: Session,
    context: PaymentContext,
    status: str = "pending",
    idempotency_key: str | None = None,
    amount: Decimal = Decimal("10.00"),
) -> None:
    session.execute(
        statement=text(
            "INSERT INTO payments (cart_id, user_id, payment_method_id, amount, currency, status, failure_code,"
            " provider_charge_id, idempotency_key)"
            " VALUES (:cart_id, :user_id, :payment_method_id, :amount, 'USD', :status, :failure_code,"
            " :provider_charge_id, :idempotency_key)",
        ),
        params={
            "cart_id": context.cart_id,
            "user_id": context.user_id,
            "payment_method_id": context.payment_method_id,
            "amount": amount,
            "status": status,
            "failure_code": "card_declined" if status == "failed" else None,
            "provider_charge_id": "ch_1" if status == "succeeded" else None,
            "idempotency_key": idempotency_key or str(uuid4()),
        },
    )


def test_second_pending_payment_for_cart_is_rejected(
    session_factory: sessionmaker[Session],
    payment_context: PaymentContext,
) -> None:
    with session_factory.begin() as session:
        insert_payment(session=session, context=payment_context)

    with pytest.raises(IntegrityError, match="uq_payments_cart_active"), session_factory.begin() as session:
        insert_payment(session=session, context=payment_context)


def test_pending_after_succeeded_for_cart_is_rejected(
    session_factory: sessionmaker[Session],
    payment_context: PaymentContext,
) -> None:
    with session_factory.begin() as session:
        insert_payment(session=session, context=payment_context, status="succeeded")

    with pytest.raises(IntegrityError, match="uq_payments_cart_active"), session_factory.begin() as session:
        insert_payment(session=session, context=payment_context)


def test_failed_payment_requires_failure_code(
    session_factory: sessionmaker[Session],
    payment_context: PaymentContext,
) -> None:
    with session_factory.begin() as session:
        insert_payment(session=session, context=payment_context)

    with pytest.raises(IntegrityError, match="ck_payments_failure_code"), session_factory.begin() as session:
        session.execute(statement=text("UPDATE payments SET status = 'failed'"))


def test_succeeded_payment_requires_provider_charge_id(
    session_factory: sessionmaker[Session],
    payment_context: PaymentContext,
) -> None:
    with session_factory.begin() as session:
        insert_payment(session=session, context=payment_context)

    with pytest.raises(IntegrityError, match="ck_payments_provider_charge_id"), session_factory.begin() as session:
        session.execute(statement=text("UPDATE payments SET status = 'succeeded'"))


def test_non_positive_amount_is_rejected(
    session_factory: sessionmaker[Session],
    payment_context: PaymentContext,
) -> None:
    with pytest.raises(IntegrityError, match="ck_payments_amount_positive"), session_factory.begin() as session:
        insert_payment(session=session, context=payment_context, amount=Decimal("0.00"))


def test_idempotency_key_is_unique_per_user(
    session_factory: sessionmaker[Session],
    payment_context: PaymentContext,
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
) -> None:
    other_user_id = add_user()
    other_context = PaymentContext(
        user_id=other_user_id,
        cart_id=add_cart(user_id=other_user_id, items=[(Decimal("10.00"), 1, "USD")]),
        payment_method_id=add_payment_method(user_id=other_user_id),
    )
    with session_factory.begin() as session:
        insert_payment(
            session=session,
            context=payment_context,
            status="failed",
            idempotency_key="key-1",
        )
        insert_payment(session=session, context=other_context, idempotency_key="key-1")

    with pytest.raises(IntegrityError, match="uq_payments_user_idempotency_key"), session_factory.begin() as session:
        insert_payment(session=session, context=payment_context, idempotency_key="key-1")


def test_many_failed_payments_for_cart_are_allowed(
    session_factory: sessionmaker[Session],
    payment_context: PaymentContext,
) -> None:
    with session_factory.begin() as session:
        for _ in range(3):
            insert_payment(session=session, context=payment_context, status="failed")

    with session_factory() as session:
        assert session.scalar(text("SELECT count(*) FROM payments")) == 3
