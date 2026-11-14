# shop-payments

The payment part of an online shop: a `payments` table on top of the shop's base schema, and one endpoint that
pays for a cart with a saved card. The payment provider is a mock; the card token decides its outcome.

Python 3.13, Flask with flask-openapi, SQLAlchemy 2, PostgreSQL, Alembic.

## Run

With Docker (needs Docker and `make`):

```sh
docker compose up --build --wait  # PostgreSQL and the service on :8000; migrations run on start
make seed                         # demo users, carts and cards (safe to repeat)
```

Locally (needs Python 3.13, [Poetry](https://python-poetry.org/), `make`, and Docker for PostgreSQL):

```sh
poetry install
cp .env.example .env
docker compose up -d db
make migrate
make seed
make run                 # http://127.0.0.1:5000
```

Swagger UI is at `/openapi/swagger`. Both ways use the same development database; `docker compose down -v`
empties it.

## Tests

```sh
make test  # pytest against a real PostgreSQL (the db container), schema built by the migrations
make lint  # ruff check and ruff format --check
```

`poetry run pre-commit install` adds the ruff hooks.

## API

`POST /carts/{cart_id}/payments` with the headers `Authorization: Bearer <user id>` and `Idempotency-Key`.
The JSON body is optional: `{"payment_method_id": "<uuid>"}` picks a saved card, otherwise the user's default
card is charged. Whenever a payment exists, the response body is that payment.

| Outcome | Payment | HTTP |
|---|---|---|
| Provider approved the charge | `succeeded`, cart becomes `checked_out` | 201 |
| Card declined | `failed`, `card_declined` | 402 |
| Provider unreachable, nothing was charged | `failed`, `provider_unavailable` | 502 |
| Circuit breaker is open, provider not called | `failed`, `circuit_open` | 503 |
| Provider timed out, outcome unknown | stays `pending` | 504 |

Requests rejected before a payment exists answer with `{"error": {"code": "…", "message": "…"}}`: 401
`unauthorized`; 404 `cart_not_found`; 409 `cart_not_payable`, `payment_in_progress`; 422 `cart_empty`,
`mixed_currencies`, `invalid_amount`, `payment_method_not_found`, `no_default_payment_method`,
`idempotency_key_reused`, and flask-openapi's own validation errors.

Repeating an `Idempotency-Key` for the same cart returns the existing payment and never charges again: 201 for
`succeeded`, the original 402, 502 or 503 for `failed`, 409 for `pending`. After a failure the client may retry
with a new key; that creates a new payment and the failed one stays as history.

## Try it

After `make seed`, Alice has a 70.00 USD cart and a default card. Bob has an 89.99 USD cart and three cards, one
for each provider failure. The examples use port 8000 (Docker); use 5000 for `make run`.

```sh
ALICE='Authorization: Bearer 11111111-1111-1111-1111-111111111111'
BOB='Authorization: Bearer 22222222-2222-2222-2222-222222222222'
ALICE_CART=http://localhost:8000/carts/c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1/payments
BOB_CART=http://localhost:8000/carts/c2c2c2c2-c2c2-c2c2-c2c2-c2c2c2c2c2c2/payments
JSON='Content-Type: application/json'

# 201: approved. Run it twice: the second call returns the same payment.
curl -i -X POST "$ALICE_CART" -H "$ALICE" -H 'Idempotency-Key: alice-1'

# 402: card declined
curl -i -X POST "$BOB_CART" -H "$BOB" -H "$JSON" -H 'Idempotency-Key: bob-declined' \
  -d '{"payment_method_id": "22222222-2222-3333-4444-555555555551"}'

# 502: provider unreachable. Five failures in a row open the circuit breaker.
for i in 1 2 3 4 5; do
  curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BOB_CART" -H "$BOB" -H "$JSON" \
    -H "Idempotency-Key: bob-unavailable-$i" -d '{"payment_method_id": "22222222-2222-3333-4444-555555555552"}'
done

# 503: the circuit is open
curl -i -X POST "$BOB_CART" -H "$BOB" -H "$JSON" -H 'Idempotency-Key: bob-circuit-open' \
  -d '{"payment_method_id": "22222222-2222-3333-4444-555555555552"}'

# The breaker allows a trial call after 60 seconds; restarting the service resets it at once.
docker compose restart app && docker compose up --wait

# 504: provider timed out, the payment stays pending and blocks Bob's cart
curl -i -X POST "$BOB_CART" -H "$BOB" -H "$JSON" -H 'Idempotency-Key: bob-timeout' \
  -d '{"payment_method_id": "22222222-2222-3333-4444-555555555553"}'
```

## Decisions

- Two transactions with the provider call between them. The first locks the cart row, checks it and commits a
  `pending` payment; the second writes the outcome. One transaction around the whole request would lose the
  record of a charge if the process died after it, and would hold a lock while the provider answers.
- A timeout leaves the payment `pending`. The card may have been charged, and a `failed` payment would let the
  cart be paid a second time.
- Two guards against double payment. A partial unique index allows one `pending` or `succeeded` payment per
  cart, whatever the code does. The idempotency key lets a client repeat a request safely.
- Circuit breaker with pybreaker's defaults (5 failures, 60 seconds). Its state lives in one process; the
  container runs a single gunicorn worker.
- No repository layer and no dependency injection container. With one use case and one replaceable
  dependency (the provider, passed to `create_app`) they would add code without removing any. They pay off when
  several services share queries or the object graph gets deep.

## Assumptions

- Authentication and the cart total are stand-ins for existing parts of the shop: the bearer token is the user's
  id, and the total is the sum of `quantity * unit_price`.
- A cart is paid in one currency, without conversion. `cart_items` has no currency column, so the currency is the
  product's at the moment of payment; mixed currencies are rejected.
- With several default cards the newest wins; the base schema does not forbid several.
- Another user's cart is a 404, so the endpoint does not reveal which carts exist.
- A missing `Idempotency-Key` is a 422, like any other invalid part of the request. The 255 character limit is
  the one Stripe documents for its keys.
- A successful payment checks the cart out, as the base schema defines `checked_out`. Stock is not changed.
- The cart is not edited while it is being paid: the lock covers the first transaction only.
- The sample rows of the base schema live in `seed.sql`, not in the migration.

## Not built

- Reconciliation of `pending` payments, which also remain when the process dies between the two transactions.
  Until one is settled, its cart stays blocked.
- Provider webhooks and retries: the provider is called once per request, a retry is the client's decision.
- A rule in the cart code that refuses to change a cart with a `pending` payment.
- Shared circuit breaker state for more than one worker, and a timeout on the real provider call.
- Migrations as a deployment step instead of on container start.
- Real authentication, refunds, stock reservation, currency conversion.
