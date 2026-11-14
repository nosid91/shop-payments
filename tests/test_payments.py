from collections.abc import Callable
from decimal import Decimal
from uuid import UUID

from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from shop_payments.models import Cart, Payment, PaymentStatus
from shop_payments.provider import ChargeResult, MockPaymentProvider


class LockProbingProvider(MockPaymentProvider):
    def __init__(self, *, session_factory: sessionmaker[Session], cart_id: UUID) -> None:
        super().__init__()
        self.session_factory = session_factory
        self.cart_id = cart_id
        self.probed = False

    def charge(self, *, token: str, amount: Decimal, currency: str, idempotency_key: str) -> ChargeResult:
        with self.session_factory.begin() as session:
            session.execute(
                statement=text("SELECT id FROM carts WHERE id = :id FOR UPDATE NOWAIT"),
                params={"id": self.cart_id},
            )
        self.probed = True
        return super().charge(token=token, amount=amount, currency=currency, idempotency_key=idempotency_key)


def test_pays_active_cart_with_default_method(
    client: FlaskClient,
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
    auth_headers: Callable[..., dict[str, str]],
    session_factory: sessionmaker[Session],
) -> None:
    user_id = add_user()
    cart_id = add_cart(user_id=user_id, items=[(Decimal("45.00"), 1, "USD"), (Decimal("12.50"), 2, "USD")])
    add_payment_method(user_id=user_id)

    response = client.post(f"/carts/{cart_id}/payments", headers=auth_headers(user_id=user_id, key="key-1"))

    assert response.status_code == 201
    body = response.get_json()
    assert body["status"] == "succeeded"
    assert body["amount"] == "70.00"
    assert body["currency"] == "USD"
    assert body["failure_code"] is None
    with session_factory() as session:
        cart = session.get(entity=Cart, ident=cart_id)
        payment = session.get(entity=Payment, ident=UUID(body["id"]))
        assert cart is not None
        assert cart.status == "checked_out"
        assert payment is not None
        assert payment.status == PaymentStatus.SUCCEEDED
        assert payment.provider_charge_id
        assert payment.amount == Decimal("70.00")
        assert payment.user_id == user_id
        assert payment.cart_id == cart_id
        assert payment.idempotency_key == "key-1"


def test_pays_without_body_or_content_type(
    client: FlaskClient,
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
    auth_headers: Callable[..., dict[str, str]],
) -> None:
    user_id = add_user()
    cart_id = add_cart(user_id=user_id, items=[(Decimal("10.00"), 1, "USD")])
    add_payment_method(user_id=user_id)

    response = client.post(f"/carts/{cart_id}/payments", headers=auth_headers(user_id=user_id))

    assert response.status_code == 201


def test_pays_with_explicit_payment_method(
    client: FlaskClient,
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
    auth_headers: Callable[..., dict[str, str]],
    session_factory: sessionmaker[Session],
) -> None:
    user_id = add_user()
    cart_id = add_cart(user_id=user_id, items=[(Decimal("10.00"), 1, "USD")])
    add_payment_method(user_id=user_id, is_default=True)
    other_method_id = add_payment_method(user_id=user_id, is_default=False)

    response = client.post(
        f"/carts/{cart_id}/payments",
        headers=auth_headers(user_id=user_id),
        json={"payment_method_id": str(other_method_id)},
    )

    assert response.status_code == 201
    with session_factory() as session:
        payment = session.get(entity=Payment, ident=UUID(response.get_json()["id"]))
        assert payment is not None
        assert payment.payment_method_id == other_method_id


def test_pays_cart_in_non_usd_currency(
    client: FlaskClient,
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
    auth_headers: Callable[..., dict[str, str]],
) -> None:
    user_id = add_user()
    cart_id = add_cart(user_id=user_id, items=[(Decimal("10.00"), 3, "EUR")])
    add_payment_method(user_id=user_id)

    response = client.post(f"/carts/{cart_id}/payments", headers=auth_headers(user_id=user_id))

    assert response.status_code == 201
    body = response.get_json()
    assert body["amount"] == "30.00"
    assert body["currency"] == "EUR"


def test_provider_is_called_without_open_transaction(
    make_app: Callable[..., Flask],
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
    auth_headers: Callable[..., dict[str, str]],
    session_factory: sessionmaker[Session],
) -> None:
    user_id = add_user()
    cart_id = add_cart(user_id=user_id, items=[(Decimal("10.00"), 1, "USD")])
    add_payment_method(user_id=user_id)
    provider = LockProbingProvider(session_factory=session_factory, cart_id=cart_id)
    client = make_app(provider=provider).test_client()

    response = client.post(f"/carts/{cart_id}/payments", headers=auth_headers(user_id=user_id))

    assert response.status_code == 201
    assert provider.probed


def test_paid_cart_cannot_be_paid_again(
    client: FlaskClient,
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
    auth_headers: Callable[..., dict[str, str]],
    session_factory: sessionmaker[Session],
) -> None:
    user_id = add_user()
    cart_id = add_cart(user_id=user_id, items=[(Decimal("10.00"), 1, "USD")])
    add_payment_method(user_id=user_id)
    client.post(f"/carts/{cart_id}/payments", headers=auth_headers(user_id=user_id))

    response = client.post(f"/carts/{cart_id}/payments", headers=auth_headers(user_id=user_id))

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "payment_in_progress"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Payment)) == 1


def test_openapi_document_describes_endpoint(client: FlaskClient) -> None:
    response = client.get("/openapi/openapi.json")

    assert response.status_code == 200
    document = response.get_json()
    assert "post" in document["paths"]["/carts/{cart_id}/payments"]
    assert "bearer" in document["components"]["securitySchemes"]
