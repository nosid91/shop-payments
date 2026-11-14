.PHONY: run test lint

run:
	poetry run flask --app shop_payments run

test:
	poetry run pytest

lint:
	poetry run ruff check . && poetry run ruff format --check .
