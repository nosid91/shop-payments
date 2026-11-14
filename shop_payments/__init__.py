from flask_openapi import Info, OpenAPI

from shop_payments.config import Settings
from shop_payments.db import create_session_factory


def create_app(*, settings: Settings | None = None) -> OpenAPI:
    app = OpenAPI(import_name=__name__, info=Info(title="Shop payments", version="0.1.0"))
    settings = settings or Settings()
    app.config["SETTINGS"] = settings
    app.extensions["session_factory"] = create_session_factory(database_url=settings.database.url)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
