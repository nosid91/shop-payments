from collections.abc import Callable
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from shop_payments.models import Cart, CartItem, UserPaymentMethod


def test_models_round_trip(
    session_factory: sessionmaker[Session],
    add_user: Callable[[], UUID],
    add_cart: Callable[..., UUID],
    add_payment_method: Callable[..., UUID],
) -> None:
    user_id = add_user()
    cart_id = add_cart(
        user_id=user_id,
        items=[(Decimal("45.00"), 1, "USD"), (Decimal("12.50"), 2, "USD")],
    )
    method_id = add_payment_method(user_id=user_id)

    with session_factory() as session:
        cart = session.scalars(select(Cart).options(selectinload(Cart.items).selectinload(CartItem.product))).one()
        method = session.get(entity=UserPaymentMethod, ident=method_id)

    assert cart.id == cart_id
    assert cart.user_id == user_id
    assert cart.status == "active"
    assert len(cart.items) == 2
    assert {(item.unit_price, item.quantity) for item in cart.items} == {(Decimal("45.00"), 1), (Decimal("12.50"), 2)}
    assert all(isinstance(item.unit_price, Decimal) for item in cart.items)
    assert all(item.product.currency == "USD" for item in cart.items)
    assert method is not None
    assert method.is_default is True
