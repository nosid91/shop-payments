from decimal import Decimal
from uuid import uuid4

from pydantic import BaseModel

TOKEN_CARD_DECLINED = "tok_card_declined"
TOKEN_PROVIDER_UNAVAILABLE = "tok_provider_unavailable"
TOKEN_PROVIDER_TIMEOUT = "tok_provider_timeout"


class ChargeResult(BaseModel):
    approved: bool
    charge_id: str | None


# The request never reached the provider: no money moved.
class ProviderUnavailableError(Exception): ...


# No answer in time: the money may or may not have been charged.
class ProviderTimeoutError(Exception): ...


class MockPaymentProvider:
    def __init__(self) -> None:
        self._results: dict[str, ChargeResult] = {}

    # amount and currency belong to the provider contract; the mock decides by the token alone.
    def charge(self, *, token: str, amount: Decimal, currency: str, idempotency_key: str) -> ChargeResult:  # noqa: ARG002
        if idempotency_key in self._results:
            return self._results[idempotency_key]
        if token == TOKEN_PROVIDER_UNAVAILABLE:
            raise ProviderUnavailableError
        if token == TOKEN_PROVIDER_TIMEOUT:
            raise ProviderTimeoutError
        if token == TOKEN_CARD_DECLINED:
            result = ChargeResult(approved=False, charge_id=None)
        else:
            result = ChargeResult(approved=True, charge_id=f"ch_{uuid4().hex}")
        self._results[idempotency_key] = result
        return result
