from http import HTTPStatus

from flask import current_app, g, request
from flask_openapi import APIBlueprint, ValidationErrorModel
from pydantic import RootModel

from shop_payments.auth import UnauthorizedError, authenticate
from shop_payments.models import FailureCode, PaymentStatus
from shop_payments.payments import ErrorCode, PaymentError, start_payment
from shop_payments.schemas import (
    ConflictResponse,
    ErrorBody,
    ErrorResponse,
    PaymentBody,
    PaymentHeaders,
    PaymentPath,
    PaymentResponse,
    PaymentResult,
)

api = APIBlueprint(name="payments", import_name=__name__)


class UnprocessableResponse(RootModel[ErrorResponse | list[ValidationErrorModel]]): ...


ERROR_STATUS_CODES = {
    ErrorCode.CART_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.CART_NOT_PAYABLE: HTTPStatus.CONFLICT,
    ErrorCode.PAYMENT_IN_PROGRESS: HTTPStatus.CONFLICT,
    ErrorCode.CART_EMPTY: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.MIXED_CURRENCIES: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.INVALID_AMOUNT: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.PAYMENT_METHOD_NOT_FOUND: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.NO_DEFAULT_PAYMENT_METHOD: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.IDEMPOTENCY_KEY_REUSED: HTTPStatus.UNPROCESSABLE_ENTITY,
}

PAYMENT_STATUS_CODES = {
    PaymentStatus.SUCCEEDED: HTTPStatus.CREATED,
    PaymentStatus.PENDING: HTTPStatus.GATEWAY_TIMEOUT,
}

FAILURE_STATUS_CODES = {
    FailureCode.CARD_DECLINED: HTTPStatus.PAYMENT_REQUIRED,
    FailureCode.PROVIDER_UNAVAILABLE: HTTPStatus.BAD_GATEWAY,
    FailureCode.CIRCUIT_OPEN: HTTPStatus.SERVICE_UNAVAILABLE,
}


def response_status(*, result: PaymentResult) -> int:
    if result.replayed and result.payment.status == PaymentStatus.PENDING:
        return HTTPStatus.CONFLICT
    if result.payment.status == PaymentStatus.FAILED:
        return FAILURE_STATUS_CODES[result.payment.failure_code]
    return PAYMENT_STATUS_CODES[result.payment.status]


@api.before_request
def authenticate_request() -> None:
    g.user_id = authenticate(
        session_factory=current_app.extensions["session_factory"],
        authorization=request.headers.get("Authorization"),
    )


# flask-openapi gives a body it cannot read as JSON to the model as None, exactly like a request without a body,
# and the default card would be charged instead of the one the client named.
@api.before_request
def reject_unreadable_body() -> tuple[dict, int] | None:
    if request.get_data() and request.get_json(silent=True) is None:
        body = ErrorResponse(error=ErrorBody(code="invalid_body", message="Request body must be a JSON object"))
        return body.model_dump(mode="json"), HTTPStatus.UNPROCESSABLE_ENTITY
    return None


@api.errorhandler(PaymentError)
def handle_payment_error(error: PaymentError) -> tuple[dict, int]:
    body = ErrorResponse(error=ErrorBody(code=error.code, message=error.message))
    return body.model_dump(mode="json"), ERROR_STATUS_CODES[error.code]


@api.errorhandler(UnauthorizedError)
def handle_unauthorized(_error: UnauthorizedError) -> tuple[dict, int]:
    body = ErrorResponse(error=ErrorBody(code="unauthorized", message="Missing or invalid bearer token"))
    return body.model_dump(mode="json"), HTTPStatus.UNAUTHORIZED


@api.post(
    "/carts/<cart_id>/payments",
    responses={
        201: PaymentResponse,
        401: ErrorResponse,
        402: PaymentResponse,
        404: ErrorResponse,
        409: ConflictResponse,
        422: UnprocessableResponse,
        502: PaymentResponse,
        503: PaymentResponse,
        504: PaymentResponse,
    },
    security=[{"bearer": []}],
)
def create_payment(*, path: PaymentPath, header: PaymentHeaders, body: PaymentBody) -> tuple[dict, int]:
    result = start_payment(
        session_factory=current_app.extensions["session_factory"],
        provider=current_app.extensions["payment_provider"],
        breaker=current_app.extensions["provider_breaker"],
        user_id=g.user_id,
        cart_id=path.cart_id,
        idempotency_key=header.idempotency_key,
        payment_method_id=body.payment_method_id,
    )
    return result.payment.model_dump(mode="json"), response_status(result=result)
