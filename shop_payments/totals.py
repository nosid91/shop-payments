from collections.abc import Sequence
from decimal import Decimal

from pydantic import BaseModel

from shop_payments.models import CartItem


class CartTotal(BaseModel):
    amount: Decimal
    currency: str


class MixedCurrenciesError(Exception): ...


def calculate_cart_total(*, items: Sequence[CartItem]) -> CartTotal:
    currencies = {item.product.currency for item in items}
    if len(currencies) > 1:
        raise MixedCurrenciesError
    amount = sum((item.quantity * item.unit_price for item in items), start=Decimal(0))
    return CartTotal(amount=amount, currency=currencies.pop())
