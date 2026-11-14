from collections.abc import Callable
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from shop_payments.models import Cart, Payment, PaymentStatus
from shop_payments.provider import ChargeResult, MockPaymentProvider


@pytest.fixture
def payment_count(session_factory: sessionmaker[Session]) -> Callable[[], int]:
    def payment_count() -> int:
        with session_factory() as session:
            return session.execute(select(func.count()).select_from(Payment)).scalar_one()

    return payment_count


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
    payment_count: Callable[[], int],
) -> None:
    user_id = add_user()
    cart_id = add_cart(user_id=user_id, items=[(Decimal("10.00"), 1, "USD")])
    add_payment_method(user_id=user_id)
    client.post(f"/carts/{cart_id}/payments", headers=auth_headers(user_id=user_id))

    response = client.post(f"/carts/{cart_id}/payments", headers=auth_headers(user_id=user_id))

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "cart_not_payable"
    assert payment_count() == 1


def test_openapi_document_describes_endpoint(client: FlaskClient) -> None:
    response = client.get("/openapi/openapi.json")

    assert response.status_code == 200
    document = response.get_json()
    assert "post" in document["paths"]["/carts/{cart_id}/payments"]
    assert "bearer" in document["components"]["securitySchemes"]


def test_missing_authorization_is_unauthorized(client: FlaskClient, payment_count: Callable[[], int]) -> None:
    response = client.post(f"/carts/{uuid4()}/payments", headers={"Idempotency-Key": "key-1"})

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "unauthorized"
    assert payment_count() == 0


@pytest.mark.parametrize("authorization", ["Basic abc", "Bearer", "Bearer ", "Bearer not-a-uuid", f"Bearer {uuid4()}"])
def test_malformed_authorization_is_unauthorized(
    client: FlaskClient,
    authorization: str,
    payment_count: Callable[[], int],
) -> None:
    response = client.post(
        f"/carts/{uuid4()}/payments",
        headers={"Authorization": authorization, "Idempotency-Key": "key-1"},
    )

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "unauthorized"
    assert payment_count() == 0


def test_bad_token_wins_over_bad_request(client: FlaskClient) -> None:
    response = client.post("/carts/not-a-uuid/payments", headers={"Authorization": "Bearer not-a-uuid"})

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "unauthorized"


def test_unknown_cart_is_not_found(
    client: FlaskClient,
    add_user: Callable[[], UUID],
    add_payment_method: Callable[..., UUID],
    auth_headers: Callable[..., dict[str, str]],
    payment_count: Callable[[], int],
) -> None:
    user_id = add_user()
    add_payment_method(user_id=user_id)

    response = client.post(f"/carts/{uuid4()}/payments", headers=auth_headers(user_id=user_id))

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "cart_not_found"
    assert payment_count() == 0


def test_foreign_cart_is_not_found(
    client: FlaskClient,
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
    auth_headers: Callable[..., dict[str, str]],
    payment_count: Callable[[], int],
) -> None:
    owner_id = add_user()
    user_id = add_user()
    cart_id = add_cart(user_id=owner_id, items=[(Decimal("10.00"), 1, "USD")])
    add_payment_method(user_id=user_id)

    response = client.post(f"/carts/{cart_id}/payments", headers=auth_headers(user_id=user_id))

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "cart_not_found"
    assert payment_count() == 0


def test_non_uuid_cart_id_is_rejected(
    client: FlaskClient,
    add_user: Callable[[], UUID],
    auth_headers: Callable[..., dict[str, str]],
    payment_count: Callable[[], int],
) -> None:
    response = client.post("/carts/not-a-uuid/payments", headers=auth_headers(user_id=add_user()))

    assert response.status_code == 422
    assert payment_count() == 0


@pytest.mark.parametrize("status", ["checked_out", "abandoned"])
def test_inactive_cart_is_not_payable(
    client: FlaskClient,
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
    auth_headers: Callable[..., dict[str, str]],
    payment_count: Callable[[], int],
    status: str,
) -> None:
    user_id = add_user()
    cart_id = add_cart(user_id=user_id, items=[(Decimal("10.00"), 1, "USD")], status=status)
    add_payment_method(user_id=user_id)

    response = client.post(f"/carts/{cart_id}/payments", headers=auth_headers(user_id=user_id))

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "cart_not_payable"
    assert payment_count() == 0


@pytest.mark.parametrize(
    ("items", "code"),
    [
        pytest.param([], "cart_empty", id="empty"),
        pytest.param(
            [(Decimal("10.00"), 1, "USD"), (Decimal("10.00"), 1, "EUR")], "mixed_currencies", id="mixed_currencies"
        ),
        pytest.param([(Decimal("0.00"), 1, "USD")], "invalid_amount", id="zero_total"),
        pytest.param([(Decimal("6000000000.00"), 2, "USD")], "invalid_amount", id="total_above_column_limit"),
    ],
)
def test_unpayable_cart_content_is_rejected(
    client: FlaskClient,
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
    auth_headers: Callable[..., dict[str, str]],
    payment_count: Callable[[], int],
    items: list[tuple[Decimal, int, str]],
    code: str,
) -> None:
    user_id = add_user()
    cart_id = add_cart(user_id=user_id, items=items)
    add_payment_method(user_id=user_id)

    response = client.post(f"/carts/{cart_id}/payments", headers=auth_headers(user_id=user_id))

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == code
    assert payment_count() == 0


def test_foreign_payment_method_is_rejected(
    client: FlaskClient,
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
    auth_headers: Callable[..., dict[str, str]],
    payment_count: Callable[[], int],
) -> None:
    user_id = add_user()
    cart_id = add_cart(user_id=user_id, items=[(Decimal("10.00"), 1, "USD")])
    add_payment_method(user_id=user_id)
    foreign_method_id = add_payment_method(user_id=add_user())

    response = client.post(
        f"/carts/{cart_id}/payments",
        headers=auth_headers(user_id=user_id),
        json={"payment_method_id": str(foreign_method_id)},
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "payment_method_not_found"
    assert payment_count() == 0


def test_unknown_payment_method_is_rejected(
    client: FlaskClient,
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
    auth_headers: Callable[..., dict[str, str]],
    payment_count: Callable[[], int],
) -> None:
    user_id = add_user()
    cart_id = add_cart(user_id=user_id, items=[(Decimal("10.00"), 1, "USD")])
    add_payment_method(user_id=user_id)

    response = client.post(
        f"/carts/{cart_id}/payments",
        headers=auth_headers(user_id=user_id),
        json={"payment_method_id": str(uuid4())},
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "payment_method_not_found"
    assert payment_count() == 0


def test_no_default_payment_method_is_rejected(
    client: FlaskClient,
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
    auth_headers: Callable[..., dict[str, str]],
    payment_count: Callable[[], int],
) -> None:
    user_id = add_user()
    cart_id = add_cart(user_id=user_id, items=[(Decimal("10.00"), 1, "USD")])
    add_payment_method(user_id=user_id, is_default=False)

    response = client.post(f"/carts/{cart_id}/payments", headers=auth_headers(user_id=user_id))

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "no_default_payment_method"
    assert payment_count() == 0


def test_newest_default_method_wins(
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
    newest_method_id = add_payment_method(user_id=user_id)

    response = client.post(f"/carts/{cart_id}/payments", headers=auth_headers(user_id=user_id))

    assert response.status_code == 201
    with session_factory() as session:
        payment = session.get(entity=Payment, ident=UUID(response.get_json()["id"]))
        assert payment is not None
        assert payment.payment_method_id == newest_method_id


def test_missing_idempotency_key_is_rejected(
    client: FlaskClient,
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
    payment_count: Callable[[], int],
) -> None:
    user_id = add_user()
    cart_id = add_cart(user_id=user_id, items=[(Decimal("10.00"), 1, "USD")])
    add_payment_method(user_id=user_id)

    response = client.post(f"/carts/{cart_id}/payments", headers={"Authorization": f"Bearer {user_id}"})

    assert response.status_code == 422
    assert payment_count() == 0


def test_too_long_idempotency_key_is_rejected(
    client: FlaskClient,
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
    auth_headers: Callable[..., dict[str, str]],
    payment_count: Callable[[], int],
) -> None:
    user_id = add_user()
    cart_id = add_cart(user_id=user_id, items=[(Decimal("10.00"), 1, "USD")])
    add_payment_method(user_id=user_id)

    response = client.post(f"/carts/{cart_id}/payments", headers=auth_headers(user_id=user_id, key="k" * 256))

    assert response.status_code == 422
    assert payment_count() == 0
