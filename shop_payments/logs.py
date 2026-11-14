import inspect
import logging
import sys
from uuid import uuid4

from flask import Flask, Response, g, has_request_context, request
from loguru import logger

REQUEST_ID_HEADER = "X-Request-ID"
NO_REQUEST_ID = "-"
LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[request_id]} | {message}"


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        level: str | int
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def _add_request_id(record: dict) -> None:
    record["extra"]["request_id"] = (
        g.get("request_id", default=NO_REQUEST_ID) if has_request_context() else NO_REQUEST_ID
    )


def configure_logging() -> None:
    # diagnose prints the values of local variables in tracebacks, and a charge in progress holds the card token.
    logger.configure(
        handlers=[{"sink": sys.stderr, "format": LOG_FORMAT, "diagnose": False}],
        patcher=_add_request_id,
    )
    logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO, force=True)
    # At INFO the engine logs every statement with its parameters, card tokens included.
    logging.getLogger(name="sqlalchemy.engine").setLevel(logging.WARNING)


def init_request_id(*, app: Flask) -> None:
    @app.before_request
    def assign_request_id() -> None:
        g.request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid4())

    @app.after_request
    def return_request_id(response: Response) -> Response:
        response.headers[REQUEST_ID_HEADER] = g.request_id
        return response
