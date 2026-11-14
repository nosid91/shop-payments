from flask_openapi import Info, OpenAPI

from shop_payments.config import Settings


def create_app(*, settings: Settings | None = None) -> OpenAPI:
    app = OpenAPI(__name__, info=Info(title="Shop payments", version="0.1.0"))
    app.config["SETTINGS"] = settings or Settings()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
