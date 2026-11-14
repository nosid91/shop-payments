import os

import pytest
from flask import Flask
from flask.testing import FlaskClient

from shop_payments import create_app
from shop_payments.config import DatabaseSettings, Settings

DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://shop:shop@localhost:5432/shop_payments_test"


@pytest.fixture
def app() -> Flask:
    database_url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    return create_app(settings=Settings(database=DatabaseSettings(url=database_url)))


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()
