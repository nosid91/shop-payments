from decimal import Decimal

import pytest

from shop_payments.provider import (
    TOKEN_CARD_DECLINED,
    TOKEN_PROVIDER_TIMEOUT,
    TOKEN_PROVIDER_UNAVAILABLE,
    ChargeResult,
    MockPaymentProvider,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


def charge(*, provider: MockPaymentProvider, token: str = "tok_regular", idempotency_key: str = "key-1") -> ChargeResult:
    return provider.charge(token=token, amount=Decimal("10.00"), currency="USD", idempotency_key=idempotency_key)


def test_regular_token_is_approved() -> None:
    result = charge(provider=MockPaymentProvider())

    assert result.approved is True
    assert result.charge_id


def test_declined_token_is_declined() -> None:
    result = charge(provider=MockPaymentProvider(), token=TOKEN_CARD_DECLINED)

    assert result.approved is False
    assert result.charge_id is None


def test_unavailable_token_raises_unavailable() -> None:
    with pytest.raises(ProviderUnavailableError):
        charge(provider=MockPaymentProvider(), token=TOKEN_PROVIDER_UNAVAILABLE)


def test_timeout_token_raises_timeout() -> None:
    with pytest.raises(ProviderTimeoutError):
        charge(provider=MockPaymentProvider(), token=TOKEN_PROVIDER_TIMEOUT)


def test_same_key_returns_same_charge() -> None:
    provider = MockPaymentProvider()

    first = charge(provider=provider)
    second = charge(provider=provider)

    assert first == second
    assert first.charge_id is not None


def test_different_keys_get_different_charges() -> None:
    provider = MockPaymentProvider()

    first = charge(provider=provider, idempotency_key="key-1")
    second = charge(provider=provider, idempotency_key="key-2")

    assert first.charge_id != second.charge_id
