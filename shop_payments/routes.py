from http import HTTPStatus

from flask import current_app, g, request
from flask_openapi import APIBlueprint

from shop_payments.auth import authenticate
from shop_payments.models import PaymentStatus
from shop_payments.payments import ErrorCode, PaymentError, start_payment
from shop_payments.schemas import (
    ErrorBody,
    ErrorResponse,
    PaymentBody,
    PaymentHeaders,
    PaymentPath,
    PaymentResponse,
    PaymentResult,
)

api = APIBlueprint(name="payments", import_name=__name__)

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
}


def response_status(*, result: PaymentResult) -> int:
    return PAYMENT_STATUS_CODES[result.payment.status]


@api.before_request
def authenticate_request() -> None:
    g.user_id = authenticate(
        session_factory=current_app.extensions["session_factory"],
        authorization=request.headers.get("Authorization"),
    )


@api.errorhandler(PaymentError)
def handle_payment_error(error: PaymentError) -> tuple[dict, int]:
    body = ErrorResponse(error=ErrorBody(code=error.code, message=error.message))
    return body.model_dump(mode="json"), ERROR_STATUS_CODES[error.code]


@api.post(
    "/carts/<cart_id>/payments",
    responses={201: PaymentResponse, 409: ErrorResponse},
    security=[{"bearer": []}],
)
def create_payment(*, path: PaymentPath, header: PaymentHeaders, body: PaymentBody) -> tuple[dict, int]:
    result = start_payment(
        session_factory=current_app.extensions["session_factory"],
        provider=current_app.extensions["payment_provider"],
        user_id=g.user_id,
        cart_id=path.cart_id,
        idempotency_key=header.idempotency_key,
        payment_method_id=body.payment_method_id,
    )
    return result.payment.model_dump(mode="json"), response_status(result=result)
