import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import UUID

import pytest
from flask import Flask
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker
from werkzeug.test import TestResponse

from shop_payments.models import Cart, Payment, PaymentStatus
from shop_payments.provider import TOKEN_CARD_DECLINED, TOKEN_PROVIDER_TIMEOUT, ChargeResult, MockPaymentProvider

pytestmark = pytest.mark.usefixtures("default_method_id")

WAIT_TIMEOUT = 10
LOCK_POLL_INTERVAL = 0.02


class GatedProvider(MockPaymentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.calls = 0

    def charge(self, *, token: str, amount: Decimal, currency: str, idempotency_key: str) -> ChargeResult:
        self.calls += 1
        self.entered.set()
        assert self.release.wait(timeout=WAIT_TIMEOUT)
        return super().charge(token=token, amount=amount, currency=currency, idempotency_key=idempotency_key)


@pytest.fixture
def provider() -> GatedProvider:
    return GatedProvider()


@pytest.fixture
def app(make_app: Callable[..., Flask], provider: GatedProvider) -> Flask:
    return make_app(provider=provider)


@pytest.fixture
def user_id(add_user: Callable[[], UUID]) -> UUID:
    return add_user()


@pytest.fixture
def add_user_method(user_id: UUID, add_payment_method: Callable[..., UUID]) -> Callable[..., UUID]:
    def add_user_method(*, token: str = "tok_ok", is_default: bool = True) -> UUID:
        return add_payment_method(user_id=user_id, token=token, is_default=is_default)

    return add_user_method


@pytest.fixture
def default_method_id(add_user_method: Callable[..., UUID]) -> UUID:
    return add_user_method()


@pytest.fixture
def new_cart(user_id: UUID, add_cart: Callable[..., UUID]) -> Callable[[], UUID]:
    def new_cart() -> UUID:
        return add_cart(user_id=user_id, items=[(Decimal("10.00"), 1, "USD")])

    return new_cart


@pytest.fixture
def pay(
    app: Flask,
    user_id: UUID,
    auth_headers: Callable[..., dict[str, str]],
) -> Callable[..., TestResponse]:
    # Every call gets its own client, so threads never share one.
    def pay(*, cart_id: UUID, key: str, payment_method_id: UUID | None = None) -> TestResponse:
        body = {} if payment_method_id is None else {"payment_method_id": str(payment_method_id)}
        return app.test_client().post(
            f"/carts/{cart_id}/payments",
            headers=auth_headers(user_id=user_id, key=key),
            json=body,
        )

    return pay


def pay_while_original_in_flight(
    *,
    provider: GatedProvider,
    original: Callable[[], TestResponse],
    other: Callable[[], TestResponse],
) -> tuple[TestResponse, TestResponse]:
    # The cart lock is released before the provider is called, so the blocked original already has its pending payment.
    provider.release.clear()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(original)
        try:
            assert provider.entered.wait(timeout=WAIT_TIMEOUT)
            second = other()
        finally:
            provider.release.set()
        return future.result(timeout=WAIT_TIMEOUT), second


def test_replay_returns_same_payment_without_second_charge(
    pay: Callable[..., TestResponse],
    new_cart: Callable[[], UUID],
    provider: GatedProvider,
    payment_count: Callable[[], int],
) -> None:
    cart_id = new_cart()

    first = pay(cart_id=cart_id, key="key-1")
    second = pay(cart_id=cart_id, key="key-1")

    assert (first.status_code, second.status_code) == (201, 201)
    assert first.get_json()["id"] == second.get_json()["id"]
    assert provider.calls == 1
    assert payment_count() == 1


def test_replay_of_declined_payment_returns_same_outcome(
    pay: Callable[..., TestResponse],
    new_cart: Callable[[], UUID],
    add_user_method: Callable[..., UUID],
    provider: GatedProvider,
    payment_count: Callable[[], int],
) -> None:
    add_user_method(token=TOKEN_CARD_DECLINED)
    cart_id = new_cart()

    first = pay(cart_id=cart_id, key="key-1")
    second = pay(cart_id=cart_id, key="key-1")

    assert (first.status_code, second.status_code) == (402, 402)
    assert first.get_json() == second.get_json()
    assert provider.calls == 1
    assert payment_count() == 1


def test_replay_of_unresolved_payment_is_conflict(
    pay: Callable[..., TestResponse],
    new_cart: Callable[[], UUID],
    add_user_method: Callable[..., UUID],
    provider: GatedProvider,
    payment_count: Callable[[], int],
) -> None:
    add_user_method(token=TOKEN_PROVIDER_TIMEOUT)
    cart_id = new_cart()

    first = pay(cart_id=cart_id, key="key-1")
    second = pay(cart_id=cart_id, key="key-1")

    assert (first.status_code, second.status_code) == (504, 409)
    body = second.get_json()
    assert body["id"] == first.get_json()["id"]
    assert body["status"] == "pending"
    assert "error" not in body
    assert provider.calls == 1
    assert payment_count() == 1


def test_key_reused_for_another_cart_is_rejected(
    pay: Callable[..., TestResponse],
    new_cart: Callable[[], UUID],
    provider: GatedProvider,
    payment_count: Callable[[], int],
) -> None:
    first = pay(cart_id=new_cart(), key="key-1")
    second = pay(cart_id=new_cart(), key="key-1")

    assert first.status_code == 201
    assert second.status_code == 422
    assert second.get_json()["error"]["code"] == "idempotency_key_reused"
    assert provider.calls == 1
    assert payment_count() == 1


def test_key_reused_with_another_payment_method_is_rejected(
    pay: Callable[..., TestResponse],
    new_cart: Callable[[], UUID],
    add_user_method: Callable[..., UUID],
    provider: GatedProvider,
    payment_count: Callable[[], int],
) -> None:
    other_method_id = add_user_method(is_default=False)
    cart_id = new_cart()

    first = pay(cart_id=cart_id, key="key-1")
    second = pay(cart_id=cart_id, key="key-1", payment_method_id=other_method_id)

    assert first.status_code == 201
    assert second.status_code == 422
    assert second.get_json()["error"]["code"] == "idempotency_key_reused"
    assert provider.calls == 1
    assert payment_count() == 1


def test_concurrent_requests_charge_cart_once(
    pay: Callable[..., TestResponse],
    new_cart: Callable[[], UUID],
    provider: GatedProvider,
    payment_count: Callable[[], int],
) -> None:
    cart_id = new_cart()

    original, other = pay_while_original_in_flight(
        provider=provider,
        original=lambda: pay(cart_id=cart_id, key="key-1"),
        other=lambda: pay(cart_id=cart_id, key="key-2"),
    )

    assert (original.status_code, other.status_code) == (201, 409)
    assert other.get_json()["error"]["code"] == "payment_in_progress"
    assert provider.calls == 1
    assert payment_count() == 1


def test_replay_during_original_request_gets_payment_response(
    pay: Callable[..., TestResponse],
    new_cart: Callable[[], UUID],
    provider: GatedProvider,
    payment_count: Callable[[], int],
) -> None:
    cart_id = new_cart()

    original, replay = pay_while_original_in_flight(
        provider=provider,
        original=lambda: pay(cart_id=cart_id, key="key-1"),
        other=lambda: pay(cart_id=cart_id, key="key-1"),
    )

    assert (original.status_code, replay.status_code) == (201, 409)
    body = replay.get_json()
    assert body["id"] == original.get_json()["id"]
    assert body["status"] == "pending"
    assert "error" not in body
    assert provider.calls == 1
    assert payment_count() == 1


def test_same_key_for_two_carts_concurrently(
    pay: Callable[..., TestResponse],
    new_cart: Callable[[], UUID],
    provider: GatedProvider,
    payment_count: Callable[[], int],
) -> None:
    first_cart_id = new_cart()
    second_cart_id = new_cart()

    original, other = pay_while_original_in_flight(
        provider=provider,
        original=lambda: pay(cart_id=first_cart_id, key="key-1"),
        other=lambda: pay(cart_id=second_cart_id, key="key-1"),
    )

    assert (original.status_code, other.status_code) == (201, 422)
    assert other.get_json()["error"]["code"] == "idempotency_key_reused"
    assert provider.calls == 1
    assert payment_count() == 1


def wait_for_blocked_backend(*, session_factory: sessionmaker[Session]) -> None:
    blocked = text(
        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND datname = current_database())",
    )
    deadline = time.monotonic() + WAIT_TIMEOUT
    while time.monotonic() < deadline:
        with session_factory() as session:
            if session.scalar(blocked):
                return
        time.sleep(LOCK_POLL_INTERVAL)
    pytest.fail("no backend is waiting on a lock")


def hold_pending_payment(*, session: Session, user_id: UUID, cart_id: UUID, payment_method_id: UUID) -> UUID:
    payment = Payment(
        cart_id=cart_id,
        user_id=user_id,
        payment_method_id=payment_method_id,
        amount=Decimal("10.00"),
        currency="USD",
        idempotency_key="key-1",
    )
    session.add(payment)
    session.flush()
    return payment.id


def test_replay_waiting_for_cart_lock_gets_payment_response(
    pay: Callable[..., TestResponse],
    user_id: UUID,
    new_cart: Callable[[], UUID],
    default_method_id: UUID,
    session_factory: sessionmaker[Session],
    provider: GatedProvider,
) -> None:
    cart_id = new_cart()

    with ThreadPoolExecutor(max_workers=1) as executor, session_factory.begin() as session:
        session.execute(statement=select(Cart.id).where(Cart.id == cart_id).with_for_update())
        payment_id = hold_pending_payment(
            session=session,
            user_id=user_id,
            cart_id=cart_id,
            payment_method_id=default_method_id,
        )
        future = executor.submit(pay, cart_id=cart_id, key="key-1")
        wait_for_blocked_backend(session_factory=session_factory)

    response = future.result(timeout=WAIT_TIMEOUT)
    body = response.get_json()
    assert response.status_code == 409
    assert "error" not in body
    assert body["id"] == str(payment_id)
    assert body["status"] == "pending"
    assert provider.calls == 0


def test_key_race_on_insert_is_reported_as_reuse(
    pay: Callable[..., TestResponse],
    user_id: UUID,
    new_cart: Callable[[], UUID],
    default_method_id: UUID,
    session_factory: sessionmaker[Session],
    provider: GatedProvider,
) -> None:
    first_cart_id = new_cart()
    second_cart_id = new_cart()

    with ThreadPoolExecutor(max_workers=1) as executor, session_factory.begin() as session:
        hold_pending_payment(
            session=session,
            user_id=user_id,
            cart_id=first_cart_id,
            payment_method_id=default_method_id,
        )
        future = executor.submit(pay, cart_id=second_cart_id, key="key-1")
        wait_for_blocked_backend(session_factory=session_factory)

    response = future.result(timeout=WAIT_TIMEOUT)
    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "idempotency_key_reused"
    assert provider.calls == 0
    with session_factory() as session:
        payments = session.scalars(select(Payment)).all()
        assert [(payment.cart_id, payment.status) for payment in payments] == [(first_cart_id, PaymentStatus.PENDING)]
