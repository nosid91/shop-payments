import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from shop_payments import create_app
from shop_payments.config import DatabaseSettings, Settings
from shop_payments.db import create_session_factory

DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://shop:shop@localhost:5432/shop_payments_test"
ALEMBIC_INI = Path(__file__).parent.parent / "alembic.ini"


@pytest.fixture(scope="session")
def database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


@pytest.fixture(scope="session")
def session_factory(database_url: str) -> Iterator[sessionmaker[Session]]:
    config = Config(file_=str(ALEMBIC_INI))
    config.set_main_option(name="sqlalchemy.url", value=database_url)
    command.upgrade(config=config, revision="head")
    factory = create_session_factory(database_url=database_url)
    yield factory
    with factory() as session:
        session.get_bind().dispose()


@pytest.fixture(autouse=True)
def _clean_tables(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        session.execute(text("TRUNCATE users, products, carts, cart_items, user_payment_methods CASCADE"))


@pytest.fixture
def app(database_url: str) -> Iterator[Flask]:
    app = create_app(settings=Settings(database=DatabaseSettings(url=database_url)))
    yield app
    # Each app owns an engine; release its pooled connections so a long suite does not exhaust Postgres.
    with app.extensions["session_factory"]() as session:
        session.get_bind().dispose()


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()
