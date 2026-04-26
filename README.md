# Order Service

An event-driven order processing service built with **Python + FastAPI**, backed by **PostgreSQL**, **Redis**, and **ARQ** (async Redis queue). Integrates with [payment-provider](../payment-provider) as its upstream payment processor — demonstrating a realistic microservice architecture where two services communicate via REST and webhooks.

## Architecture

```
Client
  │  POST /orders
  ▼
┌──────────────────────┐
│   Orders Router      │  ← validates request, creates order (PENDING)
└──────────┬───────────┘
           │  POST /payments  (httpx, idempotency key = order-{id})
           ▼
┌──────────────────────┐
│  payment-provider    │  ← ghcr.io/rostyk7/payment-provider:latest
│  (external service)  │    async charge, webhooks back on every transition
└──────────┬───────────┘
           │  webhook: payment.settled / payment.failed / payment.refunded
           ▼
┌──────────────────────┐
│  Webhooks Router     │  ← idempotent — absorbs duplicate deliveries
└──────────┬───────────┘
           │  enqueue_job("send_notification", ...)
           ▼  Redis / ARQ
┌──────────────────────┐
│  Notification Worker │  ← separate process, retries up to 3x on failure
│  (ARQ)               │    mock email/SMS delivery, persists result
└──────────────────────┘
```

### Service isolation

Each service owns its infrastructure completely — no shared databases or queues:

```
order-service  →  postgres-orders  +  redis-orders
payment-provider  →  postgres-payment  +  redis-payment
```

They communicate only via HTTP (order-service calls payment-provider) and webhooks (payment-provider calls back order-service).

## Order State Machine

```
                 ┌─────────┐
                 │ PENDING │ ─────────────────────────────────────┐
                 └────┬────┘                                      │
                      │ payment submitted                         │
                      ▼                                           │
          ┌───────────────────────┐                               │
          │   AWAITING_PAYMENT    │ ──────────────────────┐       │
          └──────────┬────────────┘                       │       │
          ┌──────────┴──────────┐                         │       │
 settled  │                     │ failed                  │       │ cancelled
          ▼                     ▼                         │       │
       ┌──────┐         ┌────────────────┐                │       │
       │ PAID │         │ PAYMENT_FAILED │ ─────────┐     │       │
       └──┬───┘         └───────┬────────┘          │     │       │
          │                     │ retry              │     │       │
          │                     └──────────────────► │     │       │
          │                                          ▼     ▼       ▼
          │                                     ┌───────────────────┐
          │ refund                               │    CANCELLED      │ ◄── terminal
          ▼                                     └───────────────────┘
    ┌──────────┐
    │ REFUNDED │ ◄── terminal
    └──────────┘
          ▲
          │ fulfilled
    ┌───────────┐
    │ FULFILLED │ ◄── terminal  (cannot be refunded — goods already shipped)
    └───────────┘
```

### Valid transitions at a glance

| From | Allowed next states |
|---|---|
| `PENDING` | `AWAITING_PAYMENT`, `CANCELLED` |
| `AWAITING_PAYMENT` | `PAID`, `PAYMENT_FAILED` |
| `PAYMENT_FAILED` | `AWAITING_PAYMENT` (retry), `CANCELLED` |
| `PAID` | `FULFILLED`, `REFUNDED`, `CANCELLED` |
| `FULFILLED` | — terminal |
| `CANCELLED` | — terminal |
| `REFUNDED` | — terminal |

Any attempt to make an invalid transition returns `409 Conflict`.

## Running Locally

**Prerequisites:** Docker + Docker Compose

```bash
# 1. Copy env
cp .env.example .env

# 2. Boot order-service only (payment-provider not started)
docker compose up

# 3. Or boot the full stack including payment-provider
docker compose --profile full up
```

### What starts

| Mode | Services |
|---|---|
| `docker compose up` | `postgres-orders`, `redis-orders`, `api`, `worker` |
| `--profile full` | + `postgres-payment`, `redis-payment`, `payment-provider` |

### Host ports

| Service | Host port |
|---|---|
| order-service API | `http://localhost:8000` |
| payment-provider API | `http://localhost:3000` (full profile only) |
| postgres-orders | `localhost:5433` |
| postgres-payment | `localhost:5434` (full profile only) |
| redis-orders | `localhost:6380` |
| redis-payment | `localhost:6381` (full profile only) |

The ARQ worker runs as a separate container alongside the API.

## API Reference

### Create Order

```bash
POST /orders
Content-Type: application/json

{
  "customer_email": "buyer@example.com",
  "amount": 9900,
  "currency": "USD",
  "card_token": "tok_success",
  "metadata": { "productId": "prod_123" }
}
```

Response `202 Accepted` — order is immediately `AWAITING_PAYMENT`. Payment processing is async; the final status (`PAID` or `PAYMENT_FAILED`) arrives via webhook from payment-provider.

**Card tokens** — forwarded to payment-provider's mock bank for deterministic outcomes:

| Token | Payment outcome | Order status |
|---|---|---|
| `tok_success` | settled | `PAID` |
| `tok_insufficient_funds` | failed | `PAYMENT_FAILED` |
| `tok_card_declined` | failed | `PAYMENT_FAILED` |
| `tok_do_not_honor` | failed | `PAYMENT_FAILED` |

Any unrecognised token falls back to random behaviour governed by `MOCK_FAILURE_RATE` on the payment-provider side.

---

### Get Order

```bash
GET /orders/{id}
```

Returns the full order including the immutable event history and all notification records.

```json
{
  "id": "uuid",
  "status": "PAID",
  "customer_email": "buyer@example.com",
  "amount": 9900,
  "currency": "USD",
  "payment_id": "pay-uuid",
  "events": [
    { "event_type": "payment_initiated", "from_status": "PENDING",           "to_status": "AWAITING_PAYMENT" },
    { "event_type": "payment_settled",   "from_status": "AWAITING_PAYMENT",  "to_status": "PAID" }
  ],
  "notifications": [
    { "type": "ORDER_CONFIRMED", "status": "SENT", "recipient": "buyer@example.com" }
  ]
}
```

---

### Cancel Order

```bash
POST /orders/{id}/cancel
```

Valid from `PENDING`, `AWAITING_PAYMENT`, `PAYMENT_FAILED`, `PAID`. Sends `ORDER_CANCELLED` notification.

---

### Fulfill Order

```bash
POST /orders/{id}/fulfill

{ "note": "Shipped via FedEx — tracking: 1Z999AA1..." }
```

Valid from `PAID` only. Sends `ORDER_FULFILLED` notification. Once fulfilled the order **cannot be refunded** — goods are considered delivered.

---

### Refund Order

```bash
POST /orders/{id}/refund

{ "reason": "Customer request" }
```

Valid from `PAID` only. Calls `POST /payments/:id/refund` on payment-provider to reverse the charge, then transitions the order to `REFUNDED`. Sends `ORDER_REFUNDED` notification.

**Cannot refund** a `FULFILLED` order — the state machine blocks this with `409`. Refunds must be requested before fulfillment.

A `payment.refunded` webhook from payment-provider also drives the order to `REFUNDED` independently — covers cases where a refund is initiated directly on the payment-provider side (e.g. by a support team).

---

### Payment Webhook (internal)

```
POST /webhooks/payment
```

Called by payment-provider on every state transition. Not intended for external clients.

| Event | Effect |
|---|---|
| `payment.settled` | order → `PAID`, enqueue `ORDER_CONFIRMED` |
| `payment.failed` | order → `PAYMENT_FAILED`, enqueue `PAYMENT_FAILED` |
| `payment.refunded` | order → `REFUNDED`, enqueue `ORDER_REFUNDED` |
| `payment.processing` | event logged, no state change |

Always returns `200` — duplicate deliveries are absorbed by the state machine (a 409 from an invalid transition is caught and swallowed so payment-provider stops retrying).

---

### Health

```bash
GET /health
→ { "status": "ok" }
```

## Notification Events

Every meaningful state change enqueues a notification job via ARQ. The worker renders a message template and mock-delivers it (logs to stdout — replace with SES / SendGrid / Twilio in production).

| Trigger | Notification type | Template |
|---|---|---|
| Order `PAID` | `ORDER_CONFIRMED` | "Your order {id} has been confirmed. Amount: {amount} {currency}." |
| Order `PAYMENT_FAILED` | `PAYMENT_FAILED` | "Payment failed for order {id}. Please update your payment details." |
| Order `CANCELLED` | `ORDER_CANCELLED` | "Your order {id} has been cancelled." |
| Order `FULFILLED` | `ORDER_FULFILLED` | "Great news! Your order {id} has been fulfilled and is on its way." |
| Order `REFUNDED` | `ORDER_REFUNDED` | "Your refund for order {id} has been processed. Amount: {amount} {currency}." |

All notifications are persisted in the `notifications` table with `PENDING → SENT / FAILED` status. The worker retries up to **3 times** on failure. Delivery is idempotent — if a job is processed twice, the `SENT` check at the top of the task prevents double-sending.

## Running Tests

### Unit tests (no Docker needed)

Uses in-memory SQLite and mocked Redis/payment-provider — fast, no external dependencies.

```bash
pip install -r requirements-dev.txt
pytest tests/ --ignore=tests/e2e -v
```

### Full end-to-end tests (real payment-provider from GHCR + real webhooks)

Boots the entire stack. payment-provider is pulled from `ghcr.io/rostyk7/payment-provider:latest`. Webhooks fire over the docker network from payment-provider → order-service. Tests poll for async state changes.

```bash
docker compose -f docker-compose.e2e.yml up \
  --build --abort-on-container-exit --exit-code-from e2e

# tear down
docker compose -f docker-compose.e2e.yml down --volumes
```

**Run a single e2e test:**
```bash
docker compose -f docker-compose.e2e.yml run --rm e2e \
  pytest tests/e2e/test_full_flow.py::test_tok_success_full_flow -v
```

**Faster iteration — run e2e tests against an already-running stack:**
```bash
# Terminal 1 — start the stack
docker compose -f docker-compose.e2e.yml up --build \
  api worker payment-provider postgres-orders redis-orders postgres-payment redis-payment

# Terminal 2 — run tests directly
API_BASE_URL=http://localhost:8000 pytest tests/e2e/ -v
```

### E2E test coverage

| Test | Scenario |
|---|---|
| `test_tok_success_full_flow` | create → webhook → `PAID` → `FULFILLED` |
| `test_tok_success_refund_flow` | create → webhook → `PAID` → `REFUNDED` |
| `test_tok_insufficient_funds` | create → webhook → `PAYMENT_FAILED` |
| `test_tok_card_declined` | create → webhook → `PAYMENT_FAILED` |
| `test_tok_do_not_honor` | create → webhook → `PAYMENT_FAILED` |
| `test_cancel_before_payment` | cancel while `AWAITING_PAYMENT` |
| `test_cancel_after_payment_failed` | cancel after failure |
| `test_cannot_refund_unfulfilled_order` | `FULFILLED` → refund → `409` |
| `test_cannot_fulfill_before_paid` | `AWAITING_PAYMENT` → fulfill → `409` |
| `test_duplicate_cancel_rejected` | second cancel → `409` |
| `test_get_nonexistent_order_returns_404` | unknown ID → `404` |
| `test_event_log_records_full_history` | all events present, ordered chronologically |

## CI/CD Pipeline

```
push / PR
    │
    ▼
1. Unit Tests          — pytest, in-memory SQLite, no Docker
    │
    ▼
2. Full E2E Tests      — docker-compose.e2e.yml (real payment-provider from GHCR, real webhooks)
    │
    ▼ (main branch only)
3. Publish             — build + push to ghcr.io/rostyk7/order-service:latest
```

After a successful publish the image is available at:
```bash
docker pull ghcr.io/rostyk7/order-service:latest
docker pull ghcr.io/rostyk7/order-service:sha-<commit>
```

## Key Design Decisions

**Idempotent webhook handler** — the webhook endpoint catches 409s from the state machine and still returns 200. payment-provider retries on non-2xx; swallowing the conflict stops infinite retries on duplicate deliveries.

**Immutable event log** — every state transition appends an `OrderEvent` row with `from_status`, `to_status`, and a JSON payload. The `status` column on `orders` is a projection of the latest event. Full history is always preserved and auditable.

**Persist notification before enqueue** — the `Notification` row is committed to the database before the ARQ job is enqueued. If the process crashes between commit and enqueue, the `PENDING` row survives and can be requeued. Enqueuing first would risk a job pointing to a row that doesn't exist yet.

**Payment client idempotency** — every `POST /payments` call uses `order-{id}` as the idempotency key. Network retries cannot double-charge because payment-provider deduplicates on this key.

**Refund must precede fulfillment** — once an order is `FULFILLED` the state machine blocks refunds. This is intentional: fulfillment means goods have shipped. A post-fulfillment refund would be a manual customer service operation handled outside this service.

**payment-provider as a true black box** — order-service calls payment-provider via HTTP and receives results via webhooks. They share no database, no Redis, and no code. Either service can be deployed, scaled, or replaced independently.

## Tech Stack

| | |
|---|---|
| **FastAPI** | Async Python web framework |
| **SQLAlchemy 2.0 async + asyncpg** | Typed async ORM with connection pooling |
| **Alembic** | Schema migrations (`alembic upgrade head` at container start) |
| **ARQ** | Async Redis queue for notification delivery |
| **httpx** | Async HTTP client for payment-provider calls |
| **pydantic-settings** | Typed configuration from environment variables |
| **pytest + pytest-asyncio** | Async test suite (unit: in-memory SQLite, e2e: real stack) |

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | — | Async PostgreSQL DSN (`postgresql+asyncpg://...`) |
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |
| `PAYMENT_PROVIDER_URL` | `http://localhost:3000` | payment-provider base URL |
| `SELF_BASE_URL` | `http://localhost:8000` | This service's public URL — used as the webhook callback URL when submitting charges to payment-provider. Must be reachable by payment-provider (set to `http://api:8000` in docker environments) |
| `MERCHANT_ID` | `order-service` | Merchant identifier sent to payment-provider |
