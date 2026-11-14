from flask_openapi import Info, OpenAPI

from shop_payments.config import Settings
from shop_payments.db import create_session_factory
from shop_payments.provider import MockPaymentProvider
from shop_payments.routes import api


def create_app(*, settings: Settings | None = None, provider: MockPaymentProvider | None = None) -> OpenAPI:
    app = OpenAPI(
        import_name=__name__,
        info=Info(title="Shop payments", version="0.1.0"),
        security_schemes={"bearer": {"type": "http", "scheme": "bearer"}},
    )
    settings = settings or Settings()
    app.config["SETTINGS"] = settings
    app.extensions["session_factory"] = create_session_factory(database_url=settings.database.url)
    app.extensions["payment_provider"] = provider or MockPaymentProvider()
    app.register_api(api)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
