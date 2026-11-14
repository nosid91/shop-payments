import pybreaker
from flask_openapi import Info, OpenAPI

from shop_payments.config import Settings
from shop_payments.db import create_session_factory
from shop_payments.logs import configure_logging, init_request_id
from shop_payments.provider import MockPaymentProvider
from shop_payments.routes import api


def create_app(*, settings: Settings | None = None, provider: MockPaymentProvider | None = None) -> OpenAPI:
    configure_logging()
    app = OpenAPI(
        import_name=__name__,
        info=Info(title="Shop payments", version="0.1.0"),
        security_schemes={"bearer": {"type": "http", "scheme": "bearer"}},
    )
    settings = settings or Settings()
    app.extensions["session_factory"] = create_session_factory(database_url=settings.database.url)
    app.extensions["payment_provider"] = provider or MockPaymentProvider()
    app.extensions["provider_breaker"] = pybreaker.CircuitBreaker(
        fail_max=settings.breaker.fail_max,
        reset_timeout=settings.breaker.reset_timeout,
        # With the default, the call that trips the circuit raises CircuitBreakerError instead of the original error,
        # and a timeout (unknown outcome) would be recorded as failed.
        throw_new_error_on_trip=False,
    )
    init_request_id(app=app)
    app.register_api(api)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
