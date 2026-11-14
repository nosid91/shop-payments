import os
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker
from werkzeug.test import TestResponse

from shop_payments import create_app
from shop_payments.config import BreakerSettings, DatabaseSettings, Settings
from shop_payments.db import create_session_factory
from shop_payments.models import Cart, CartItem, Payment, Product, User, UserPaymentMethod
from shop_payments.provider import ChargeResult, MockPaymentProvider

DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://shop:shop@localhost:5432/shop_payments_test"
ALEMBIC_INI = Path(__file__).parent.parent / "alembic.ini"
# Distinctive on purpose: test_logs asserts the card token never shows up in logs.
CARD_APPROVED = "tok_approved_card"


class RecordingProvider(MockPaymentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, object]] = []

    def charge(self, *, token: str, amount: Decimal, currency: str, idempotency_key: str) -> ChargeResult:
        self.calls.append({"token": token, "amount": amount, "currency": currency, "idempotency_key": idempotency_key})
        return super().charge(token=token, amount=amount, currency=currency, idempotency_key=idempotency_key)


@pytest.fixture(scope="session")
def database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


@pytest.fixture(scope="session")
def session_factory(database_url: str) -> Iterator[sessionmaker[Session]]:
    config = Config(file_=str(ALEMBIC_INI))
    config.set_main_option(name="sqlalchemy.url", value=database_url)
    command.upgrade(config=config, revision="head")
    factory = create_session_factory(database_url=database_url)
    yield factory
    with factory() as session:
        session.get_bind().dispose()


@pytest.fixture(autouse=True)
def _clean_tables(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        session.execute(
            statement=text("TRUNCATE users, products, carts, cart_items, user_payment_methods, payments CASCADE"),
        )


@pytest.fixture
def payment_count(session_factory: sessionmaker[Session]) -> Callable[[], int]:
    def payment_count() -> int:
        with session_factory() as session:
            return session.execute(select(func.count()).select_from(Payment)).scalar_one()

    return payment_count


@pytest.fixture
def add_user(session_factory: sessionmaker[Session]) -> Callable[[], UUID]:
    def add_user() -> UUID:
        with session_factory.begin() as session:
            user = User(email=f"{uuid4()}@example.com", name="Test User")
            session.add(user)
            session.flush()
            return user.id

    return add_user


@pytest.fixture
def add_cart(session_factory: sessionmaker[Session]) -> Callable[..., UUID]:
    def add_cart(*, user_id: UUID, items: list[tuple[Decimal, int, str]], status: str = "active") -> UUID:
        with session_factory.begin() as session:
            cart = Cart(
                user_id=user_id,
                status=status,
                items=[
                    CartItem(
                        quantity=quantity,
                        unit_price=unit_price,
                        product=Product(name="Product", price=unit_price, currency=currency),
                    )
                    for unit_price, quantity, currency in items
                ],
            )
            session.add(cart)
            session.flush()
            return cart.id

    return add_cart


@pytest.fixture
def add_payment_method(session_factory: sessionmaker[Session]) -> Callable[..., UUID]:
    def add_payment_method(*, user_id: UUID, token: str = CARD_APPROVED, is_default: bool = True) -> UUID:
        with session_factory.begin() as session:
            method = UserPaymentMethod(user_id=user_id, provider_token=token, is_default=is_default)
            session.add(method)
            session.flush()
            return method.id

    return add_payment_method


@pytest.fixture
def make_app(database_url: str) -> Iterator[Callable[..., Flask]]:
    apps: list[Flask] = []

    def make_app(*, provider: MockPaymentProvider | None = None, breaker_fail_max: int = 5) -> Flask:
        settings = Settings(
            database=DatabaseSettings(url=database_url),
            breaker=BreakerSettings(fail_max=breaker_fail_max, reset_timeout=60),
        )
        app = create_app(settings=settings, provider=provider)
        apps.append(app)
        return app

    yield make_app
    # Each app owns an engine; release its pooled connections so a long suite does not exhaust Postgres.
    for app in apps:
        with app.extensions["session_factory"]() as session:
            session.get_bind().dispose()


@pytest.fixture
def app(make_app: Callable[..., Flask]) -> Flask:
    return make_app()


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


@pytest.fixture
def auth_headers() -> Callable[..., dict[str, str]]:
    def auth_headers(*, user_id: UUID, key: str | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {user_id}", "Idempotency-Key": key or str(uuid4())}

    return auth_headers


@pytest.fixture
def pay(app: Flask, auth_headers: Callable[..., dict[str, str]]) -> Callable[..., TestResponse]:
    # Every call gets its own client, so concurrent tests never share one.
    def pay(
        *,
        user_id: UUID,
        cart_id: UUID,
        key: str | None = None,
        payment_method_id: UUID | None = None,
        request_id: str | None = None,
    ) -> TestResponse:
        headers = auth_headers(user_id=user_id, key=key)
        if request_id is not None:
            headers["X-Request-ID"] = request_id
        body = None if payment_method_id is None else {"payment_method_id": str(payment_method_id)}
        return app.test_client().post(f"/carts/{cart_id}/payments", headers=headers, json=body)

    return pay
