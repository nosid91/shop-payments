from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from shop_payments.models import User

BEARER_PREFIX = "Bearer "


class UnauthorizedError(Exception): ...


def authenticate(*, session_factory: sessionmaker[Session], authorization: str | None) -> UUID:
    if authorization is None or not authorization.startswith(BEARER_PREFIX):
        raise UnauthorizedError
    try:
        user_id = UUID(authorization.removeprefix(BEARER_PREFIX))
    except ValueError:
        raise UnauthorizedError from None
    with session_factory() as session:
        if session.get(entity=User, ident=user_id) is None:
            raise UnauthorizedError
    return user_id
