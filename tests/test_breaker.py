from collections.abc import Callable
from decimal import Decimal
from uuid import UUID

import pytest
from conftest import CARD_APPROVED, RecordingProvider
from flask import Flask
from werkzeug.test import TestResponse

from shop_payments.provider import (
    TOKEN_CARD_DECLINED,
    TOKEN_PROVIDER_TIMEOUT,
    TOKEN_PROVIDER_UNAVAILABLE,
)

FAIL_MAX = 2


@pytest.fixture
def provider() -> RecordingProvider:
    return RecordingProvider()


@pytest.fixture
def app(make_app: Callable[..., Flask], provider: RecordingProvider) -> Flask:
    return make_app(provider=provider, breaker_fail_max=FAIL_MAX)


@pytest.fixture
def pay_with(
    pay: Callable[..., TestResponse],
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
) -> Callable[..., TestResponse]:
    user_id = add_user()
    method_ids: dict[str, UUID] = {}

    # One method per token, so a replay sends the same payment_method_id and is not rejected as a reused key.
    def pay_with(*, token: str, key: str | None = None, cart_id: UUID | None = None) -> TestResponse:
        cart_id = cart_id or add_cart(user_id=user_id, items=[(Decimal("10.00"), 1, "USD")])
        if token not in method_ids:
            method_ids[token] = add_payment_method(user_id=user_id, token=token, is_default=False)
        return pay(user_id=user_id, cart_id=cart_id, key=key, payment_method_id=method_ids[token])

    return pay_with


def test_open_circuit_fails_payment_without_calling_provider(
    pay_with: Callable[..., TestResponse],
    provider: RecordingProvider,
) -> None:
    statuses = [pay_with(token=TOKEN_PROVIDER_UNAVAILABLE).status_code for _ in range(FAIL_MAX)]

    response = pay_with(token=CARD_APPROVED)

    assert statuses == [502, 502]
    assert response.status_code == 503
    body = response.get_json()
    assert body["status"] == "failed"
    assert body["failure_code"] == "circuit_open"
    assert len(provider.calls) == FAIL_MAX


def test_declined_cards_do_not_open_circuit(pay_with: Callable[..., TestResponse]) -> None:
    statuses = [pay_with(token=TOKEN_CARD_DECLINED).status_code for _ in range(FAIL_MAX + 1)]

    assert statuses == [402] * (FAIL_MAX + 1)
    assert pay_with(token=CARD_APPROVED).status_code == 201


def test_decline_resets_failure_counter(pay_with: Callable[..., TestResponse]) -> None:
    statuses = [
        pay_with(token=TOKEN_PROVIDER_UNAVAILABLE).status_code,
        pay_with(token=TOKEN_CARD_DECLINED).status_code,
        pay_with(token=TOKEN_PROVIDER_UNAVAILABLE).status_code,
    ]

    assert statuses == [502, 402, 502]
    assert pay_with(token=CARD_APPROVED).status_code == 201


def test_timeout_that_trips_circuit_stays_pending(pay_with: Callable[..., TestResponse], app: Flask) -> None:
    pay_with(token=TOKEN_PROVIDER_UNAVAILABLE)

    response = pay_with(token=TOKEN_PROVIDER_TIMEOUT)

    assert response.status_code == 504
    assert response.get_json()["status"] == "pending"
    assert app.extensions["provider_breaker"].current_state == "open"


def test_half_open_success_closes_circuit(pay_with: Callable[..., TestResponse], app: Flask) -> None:
    for _ in range(FAIL_MAX):
        pay_with(token=TOKEN_PROVIDER_UNAVAILABLE)
    app.extensions["provider_breaker"].half_open()

    statuses = [pay_with(token=CARD_APPROVED).status_code, pay_with(token=CARD_APPROVED).status_code]

    assert statuses == [201, 201]
    assert app.extensions["provider_breaker"].current_state == "closed"


def test_half_open_failure_reopens_circuit(pay_with: Callable[..., TestResponse], app: Flask) -> None:
    for _ in range(FAIL_MAX):
        pay_with(token=TOKEN_PROVIDER_UNAVAILABLE)
    app.extensions["provider_breaker"].half_open()

    statuses = [pay_with(token=TOKEN_PROVIDER_UNAVAILABLE).status_code, pay_with(token=CARD_APPROVED).status_code]

    assert statuses == [502, 503]


def test_replay_of_circuit_open_payment_returns_same_outcome(
    pay_with: Callable[..., TestResponse],
    provider: RecordingProvider,
) -> None:
    for _ in range(FAIL_MAX):
        pay_with(token=TOKEN_PROVIDER_UNAVAILABLE)

    first = pay_with(token=CARD_APPROVED, key="key-1")
    second = pay_with(token=CARD_APPROVED, key="key-1", cart_id=UUID(first.get_json()["cart_id"]))

    assert (first.status_code, second.status_code) == (503, 503)
    assert first.get_json()["id"] == second.get_json()["id"]
    assert len(provider.calls) == FAIL_MAX
