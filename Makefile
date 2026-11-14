.PHONY: run test lint migrate seed

run:
	poetry run flask --app shop_payments run

test:
	poetry run pytest

lint:
	poetry run ruff check . && poetry run ruff format --check .

migrate:
	poetry run alembic upgrade head

seed:
	docker compose exec -T db psql -U shop -d shop_payments -v ON_ERROR_STOP=1 < seed.sql
