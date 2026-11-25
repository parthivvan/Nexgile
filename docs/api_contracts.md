# API & Internal Contracts

> **SIMULATED SYSTEM**: Contracts below describe the initial baseline interfaces, including fields that are optional or unenforced in the broken baseline.

## 1. Checkout API (`checkout/` — Port `8001`)

### `POST /api/v1/refunds`
Accepts a refund request from web or mobile clients, persists `refund_requests`, and enqueues a `refund_jobs` row.

**Request Body (`application/json`)**:
```json
{
  "order_id": "ORD-20261015-00421",
  "customer_id": "CUST-10492",
  "amount": "149.99",
  "currency": "USD",
  "channel": "web",
  "reason": "customer_return",
  "idempotency_key": null
}
```
- `amount`: String decimal (e.g. `"149.99"`), parsed via `Decimal`.
- `currency`: `"USD"` | `"EUR"`.
- `channel`: `"web"` | `"mobile"`.
- `idempotency_key`: Optional string in baseline (not enforced; duplicate submissions create new `refund_request_id`s).

**Response (`202 Accepted`)**:
```json
{
  "refund_request_id": "REQ-...",
  "job_id": "JOB-...",
  "status": "QUEUED",
  "amount": "149.99",
  "currency": "USD",
  "created_at": "2026-10-15T14:22:01Z"
}
```

### `GET /api/v1/refunds/{refund_request_id}`
Returns current request and job status.

### `GET /health`
Returns `{"status": "ok", "component": "checkout"}`.

---

## 2. Payment Provider Simulator (`provider_sim/` — Port `8002`)

### `POST /provider/v1/refunds`
Simulates external refund execution and writes to `data/provider.db`.

**Request Body (`application/json`)**:
```json
{
  "merchant_refund_ref": "REQ-...",
  "order_id": "ORD-20261015-00421",
  "customer_id": "CUST-10492",
  "amount": "149.99",
  "currency": "USD",
  "idempotency_key": null
}
```
- **Baseline behavior**: If `idempotency_key` is `null` (which the baseline worker sends), every call creates a **new** `provider_tx_id` even for the same `merchant_refund_ref`.
- **Fault injection**: If `FAULT_PROVIDER_TIMEOUT_RATE` triggers, the provider **commits** the transaction to `data/provider.db` first, then sleeps longer than the worker's HTTP client timeout (`2.0s`), causing the worker to see `httpx.ReadTimeout`.

**Response (`200 OK`)**:
```json
{
  "provider_tx_id": "PTX-...",
  "merchant_refund_ref": "REQ-...",
  "status": "SETTLED",
  "amount": "149.99",
  "currency": "USD",
  "usd_settled_amount": "149.99",
  "fx_rate_applied": "1.000000",
  "settled_at": "2026-10-15T14:22:03Z"
}
```

### `GET /provider/v1/settlements/{date_str}`
Generates or retrieves the daily settlement CSV (`YYYY-MM-DD`) from `data/provider.db`.

### `POST /provider/v1/faults`
Runtime fault-injection control endpoint for `loadgen/` and tests:
```json
{
  "timeout_rate": 0.05,
  "slow_ms": 0,
  "slow_rate": 0.0
}
```

---

## 3. Financial Ledger & Reconciliation API (`ledger/` — Port `8003`)

### `GET /api/v1/reconciliation/{month}`
Computes provider-vs-ledger reconciliation for `YYYY-MM` (e.g., `2026-10`) dynamically from `data/ledger.db` and `data/settlements/*.csv`.

**Response (`200 OK`)**:
```json
{
  "month": "2026-10",
  "is_simulated": true,
  "provider_total_usd": "...",
  "ledger_total_usd": "...",
  "discrepancy_usd": "...",
  "categories": [
    {
      "category": "duplicate_provider_refunds",
      "amount_usd": "...",
      "transaction_count": 0,
      "customer_count": 0
    }
  ]
}
```

### `GET /api/v1/reconciliation/{month}/transactions`
Paginated, auditable transaction-level discrepancies with `refund_request_id`, `provider_tx_id`, `ledger_entry_id`, and `cause_category`.

---

## 4. Worker Queue Contract (`refund_jobs` in `data/ledger.db`)

- States: `QUEUED` -> `PROCESSING` -> `SUCCEEDED` | `FAILED` | `RETRY_WAIT`.
- Baseline workers poll `refund_jobs` every `100ms`, claim via non-atomic `SELECT` + `UPDATE`, call `POST /provider/v1/refunds` with `timeout=2.0s`, and on success insert into `ledger_entries` before updating `refund_jobs.status = 'SUCCEEDED'`.
