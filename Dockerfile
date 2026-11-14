FROM python:3.13-slim AS builder

ENV POETRY_VIRTUALENVS_IN_PROJECT=true

RUN pip install --no-cache-dir poetry==2.5.1

WORKDIR /app

COPY pyproject.toml poetry.lock ./
RUN --mount=type=cache,target=/root/.cache/pypoetry poetry install --only main


FROM python:3.13-slim

ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /app/.venv .venv
COPY alembic.ini ./
COPY migrations migrations
COPY shop_payments shop_payments

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && exec gunicorn --bind 0.0.0.0:8000 'shop_payments:create_app()'"]
