from collections.abc import Callable
from decimal import Decimal
from uuid import UUID

import pytest
from flask import Flask
from werkzeug.test import TestResponse

from shop_payments.provider import (
    TOKEN_CARD_DECLINED,
    TOKEN_PROVIDER_TIMEOUT,
    TOKEN_PROVIDER_UNAVAILABLE,
    ChargeResult,
    MockPaymentProvider,
)

CARD_APPROVED = "tok_ok"
FAIL_MAX = 2


class CountingProvider(MockPaymentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def charge(self, *, token: str, amount: Decimal, currency: str, idempotency_key: str) -> ChargeResult:
        self.calls += 1
        return super().charge(token=token, amount=amount, currency=currency, idempotency_key=idempotency_key)


@pytest.fixture
def provider() -> CountingProvider:
    return CountingProvider()


@pytest.fixture
def app(make_app: Callable[..., Flask], provider: CountingProvider) -> Flask:
    return make_app(provider=provider, breaker_fail_max=FAIL_MAX)


@pytest.fixture
def pay(
    app: Flask,
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
    auth_headers: Callable[..., dict[str, str]],
) -> Callable[..., TestResponse]:
    user_id = add_user()
    method_ids: dict[str, UUID] = {}

    # One method per token, so a replay sends the same payment_method_id and is not rejected as a reused key.
    def pay(*, token: str, key: str | None = None, cart_id: UUID | None = None) -> TestResponse:
        cart_id = cart_id or add_cart(user_id=user_id, items=[(Decimal("10.00"), 1, "USD")])
        if token not in method_ids:
            method_ids[token] = add_payment_method(user_id=user_id, token=token, is_default=False)
        return app.test_client().post(
            f"/carts/{cart_id}/payments",
            headers=auth_headers(user_id=user_id, key=key),
            json={"payment_method_id": str(method_ids[token])},
        )

    return pay


def test_open_circuit_fails_payment_without_calling_provider(
    pay: Callable[..., TestResponse],
    provider: CountingProvider,
) -> None:
    statuses = [pay(token=TOKEN_PROVIDER_UNAVAILABLE).status_code for _ in range(FAIL_MAX)]

    response = pay(token=CARD_APPROVED)

    assert statuses == [502, 502]
    assert response.status_code == 503
    body = response.get_json()
    assert body["status"] == "failed"
    assert body["failure_code"] == "circuit_open"
    assert provider.calls == FAIL_MAX


def test_declined_cards_do_not_open_circuit(pay: Callable[..., TestResponse]) -> None:
    statuses = [pay(token=TOKEN_CARD_DECLINED).status_code for _ in range(FAIL_MAX + 1)]

    assert statuses == [402] * (FAIL_MAX + 1)
    assert pay(token=CARD_APPROVED).status_code == 201


def test_decline_resets_failure_counter(pay: Callable[..., TestResponse]) -> None:
    statuses = [
        pay(token=TOKEN_PROVIDER_UNAVAILABLE).status_code,
        pay(token=TOKEN_CARD_DECLINED).status_code,
        pay(token=TOKEN_PROVIDER_UNAVAILABLE).status_code,
    ]

    assert statuses == [502, 402, 502]
    assert pay(token=CARD_APPROVED).status_code == 201


def test_timeout_that_trips_circuit_stays_pending(pay: Callable[..., TestResponse], app: Flask) -> None:
    pay(token=TOKEN_PROVIDER_UNAVAILABLE)

    response = pay(token=TOKEN_PROVIDER_TIMEOUT)

    assert response.status_code == 504
    assert response.get_json()["status"] == "pending"
    assert app.extensions["provider_breaker"].current_state == "open"


def test_half_open_success_closes_circuit(pay: Callable[..., TestResponse], app: Flask) -> None:
    for _ in range(FAIL_MAX):
        pay(token=TOKEN_PROVIDER_UNAVAILABLE)
    app.extensions["provider_breaker"].half_open()

    statuses = [pay(token=CARD_APPROVED).status_code, pay(token=CARD_APPROVED).status_code]

    assert statuses == [201, 201]
    assert app.extensions["provider_breaker"].current_state == "closed"


def test_half_open_failure_reopens_circuit(pay: Callable[..., TestResponse], app: Flask) -> None:
    for _ in range(FAIL_MAX):
        pay(token=TOKEN_PROVIDER_UNAVAILABLE)
    app.extensions["provider_breaker"].half_open()

    statuses = [pay(token=TOKEN_PROVIDER_UNAVAILABLE).status_code, pay(token=CARD_APPROVED).status_code]

    assert statuses == [502, 503]


def test_replay_of_circuit_open_payment_returns_same_outcome(
    pay: Callable[..., TestResponse],
    provider: CountingProvider,
) -> None:
    for _ in range(FAIL_MAX):
        pay(token=TOKEN_PROVIDER_UNAVAILABLE)

    first = pay(token=CARD_APPROVED, key="key-1")
    second = pay(token=CARD_APPROVED, key="key-1", cart_id=UUID(first.get_json()["cart_id"]))

    assert (first.status_code, second.status_code) == (503, 503)
    assert first.get_json()["id"] == second.get_json()["id"]
    assert provider.calls == FAIL_MAX
