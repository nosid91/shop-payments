import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker


def test_base_tables_exist(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        tables = set(
            session.scalars(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")),
        )

    assert {"users", "products", "carts", "cart_items", "user_payment_methods"} <= tables


def test_cart_status_is_checked(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        user_id = session.scalar(text("INSERT INTO users (email, name) VALUES ('a@example.com', 'A') RETURNING id"))

    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.execute(
            statement=text("INSERT INTO carts (user_id, status) VALUES (:user_id, 'paid')"),
            params={"user_id": user_id},
        )
