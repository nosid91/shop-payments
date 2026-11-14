from collections.abc import Callable, Iterator
from decimal import Decimal
from uuid import UUID

import pytest
from flask import Flask
from flask.testing import FlaskClient
from loguru import logger
from werkzeug.test import TestResponse

from shop_payments.provider import (
    TOKEN_CARD_DECLINED,
    TOKEN_PROVIDER_TIMEOUT,
    TOKEN_PROVIDER_UNAVAILABLE,
    ChargeResult,
    MockPaymentProvider,
)

CARD_APPROVED = "tok_visible_marker"


@pytest.fixture
def records() -> Iterator[list[dict]]:
    # Request this fixture after `pay`: creating the app resets the sinks.
    captured: list[dict] = []
    sink_id = logger.add(sink=lambda message: captured.append(message.record), format="{message}")
    yield captured
    logger.remove(sink_id)


@pytest.fixture
def pay(
    app: Flask,
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
    auth_headers: Callable[..., dict[str, str]],
) -> Callable[..., TestResponse]:
    user_id = add_user()

    def pay(*, token: str, request_id: str | None = None) -> TestResponse:
        cart_id = add_cart(user_id=user_id, items=[(Decimal("10.00"), 1, "USD")])
        method_id = add_payment_method(user_id=user_id, token=token, is_default=False)
        headers = auth_headers(user_id=user_id)
        if request_id is not None:
            headers["X-Request-ID"] = request_id
        return app.test_client().post(
            f"/carts/{cart_id}/payments",
            headers=headers,
            json={"payment_method_id": str(method_id)},
        )

    return pay


def test_response_echoes_request_id(client: FlaskClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"


def test_response_gets_generated_request_id(client: FlaskClient) -> None:
    response = client.get("/health")

    assert response.headers["X-Request-ID"]


def test_status_transitions_are_logged(pay: Callable[..., TestResponse], records: list[dict]) -> None:
    response = pay(token=CARD_APPROVED, request_id="abc-123")

    payment_id = response.get_json()["id"]
    transitions = {
        record["extra"]["to_status"]: record["extra"]
        for record in records
        if record["extra"].get("payment_id") == payment_id
    }
    assert set(transitions) == {"pending", "succeeded"}
    assert all(extra["request_id"] == "abc-123" for extra in transitions.values())


def test_card_token_is_never_logged(pay: Callable[..., TestResponse], records: list[dict]) -> None:
    tokens = [CARD_APPROVED, TOKEN_CARD_DECLINED, TOKEN_PROVIDER_UNAVAILABLE, TOKEN_PROVIDER_TIMEOUT]
    for token in tokens:
        pay(token=token)

    assert records
    for record in records:
        for token in tokens:
            assert token not in record["message"]
            assert token not in (str(value) for value in record["extra"].values())


class CrashingProvider(MockPaymentProvider):
    def charge(self, **_: object) -> ChargeResult:
        raise RuntimeError


def test_unhandled_error_traceback_does_not_show_card_token(
    make_app: Callable[..., Flask],
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
    auth_headers: Callable[..., dict[str, str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = make_app(provider=CrashingProvider()).test_client()
    user_id = add_user()
    cart_id = add_cart(user_id=user_id, items=[(Decimal("10.00"), 1, "USD")])
    add_payment_method(user_id=user_id, token=CARD_APPROVED)

    response = client.post(f"/carts/{cart_id}/payments", headers=auth_headers(user_id=user_id))

    logged = capsys.readouterr().err
    assert response.status_code == 500
    assert "RuntimeError" in logged
    assert CARD_APPROVED not in logged
