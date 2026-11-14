# shop-payments

Payment service for an online shop: SQL schema and an HTTP endpoint that starts a cart payment.

Requires Python 3.13, [Poetry](https://python-poetry.org/) and Docker.

```sh
poetry install           # install dependencies
cp .env.example .env     # local settings
docker compose up -d db  # PostgreSQL with the dev and test databases
make migrate             # create the tables in the dev database
make seed                # demo data (safe to repeat)
make run                 # http://127.0.0.1:5000/health, API docs at /openapi
make test                # tests run against the shop_payments_test database
```

The tests need the `db` container; they migrate the test database themselves.
Set `TEST_DATABASE_URL` to run the tests against another database.
