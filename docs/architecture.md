# System Architecture (Initial Broken Baseline)

> **SIMULATED SYSTEM**: This architecture documents the **intentional broken baseline** designed to reproduce realistic production financial reconciliation failures. Remediation must **not** be applied in Stage 1.

## 1. High-Level Topology

```
Web / Mobile Clients
        │
        ▼ (POST /api/v1/refunds — no idempotency enforcement in baseline)
┌───────────────────┐
│  1. Checkout API  │  (checkout/)
│  FastAPI :8001    │
└────────┬──────────┘
         │ writes refund_requests + refund_jobs (SQLite: data/ledger.db)
         ▼
┌───────────────────────────────────────────────────────────┐
│  2. Refund Worker Pool (4 parallel processes)             │  (worker/)
│  [Worker-1] [Worker-2] [Worker-3] [Worker-4]              │
│  (unsafe SELECT-then-UPDATE claiming + naive retries)     │
└────────┬────────────────────────────────┬─────────────────┘
         │ HTTP POST /provider/refunds    │ writes ledger_entries
         │ (no idempotency key; timeout)  │ (crash window before commit)
         ▼                                ▼
┌─────────────────────────┐      ┌──────────────────────────┐
│  3. Payment Provider    │      │  4. Financial Ledger     │
│     Simulator           │      │     & Reconciliation     │
│  (provider_sim/ :8002)  │      │  (ledger/ :8003)         │
│  SQLite:                │      │  SQLite:                 │
│  data/provider.db       │      │  data/ledger.db          │
│  Daily CSV settlements: │      │  FX table:               │
│  data/settlements/      │      │  data/fx_rates.csv       │
└─────────────────────────┘      └──────────────────────────┘
```

## 2. Component Responsibilities

### 2.1 Checkout API (`checkout/`)
- **Responsibility**: Edge HTTP service receiving refund requests from web and mobile clients.
- **Storage**: Writes `refund_requests` and enqueues `refund_jobs` into `data/ledger.db` using `sqlite3` transactions.
- **Baseline Flaw**: Does not require or enforce a client `idempotency_key` (or unique constraint on `(order_id, request_hash)`), allowing double-taps, mobile network retries, and duplicate submissions to create multiple distinct `refund_requests` and `refund_jobs`.

### 2.2 Refund Worker (`worker/`)
- **Responsibility**: Runs 4 parallel OS processes polling `refund_jobs` in `data/ledger.db`, calling `provider_sim/` over HTTP, handling retries, and recording outcomes in `ledger_entries`.
- **Baseline Flaws**:
  1. **Unsafe concurrent job claiming**: Uses non-atomic `SELECT ... WHERE status = 'QUEUED' LIMIT 1` followed by a separate `UPDATE refund_jobs SET status = 'PROCESSING'`, allowing two workers to claim and execute the same job concurrently.
  2. **Blind retries on timeout**: When `provider_sim/` times out after committing a refund, the worker marks the job for retry and submits a brand-new provider refund call without a provider idempotency key.
  3. **Crash window**: If a worker crashes (`SIGKILL` / `os._exit`) after receiving `200 OK` from `provider_sim/` but before committing to `ledger_entries`, the provider has disbursed funds that the internal ledger never records (and another worker may re-pick the stale job).

### 2.3 Payment Provider Simulator (`provider_sim/`)
- **Responsibility**: Simulates an external payment processor with completely separate persistence (`data/provider.db`).
- **Capabilities**:
  - Executes simulated money refunds in `USD` and `EUR`.
  - Configurable fault injection: post-commit timeouts, artificial latency, and transient errors.
  - Generates daily settlement CSV files in `data/settlements/settlement_YYYY-MM-DD.csv` based on provider settlement timestamps (`settled_at`).
- **Baseline Flaw**: Accepts duplicate refund requests for the same `refund_request_id` unless an explicit `provider_idempotency_key` is enforced (which the baseline worker omits).

### 2.4 Financial Ledger (`ledger/`)
- **Responsibility**: Authoritative internal accounting system in `data/ledger.db`.
- **Capabilities**:
  - Stores `USD` and `EUR` ledger entries using `Decimal` arithmetic (stored as exact `TEXT` decimal strings in SQLite, never `REAL`/`float`).
  - Maintains auditable references (`refund_request_id`, `provider_tx_id`, `settlement_batch_id`).
  - Performs month-end close and provider-vs-ledger reconciliation.
- **Baseline Flaws**:
  1. **Incorrect EUR-to-USD conversion**: Applies an inverted rate (`amount_eur / rate` instead of `amount_eur * rate`), stale/wrong-date FX lookup, or inconsistent rounding during ledger posting vs. provider settlement.
  2. **Month-end cutoff mismatch**: Ledger posts entries using `created_at` / `posted_at` in local/UTC boundaries while provider settlement files group by `settled_at` cutoff (e.g., late Oct 31 UTC transactions settling on Nov 1 or Oct 31 provider-only).

## 3. Data Isolation & Monetary Safety Rules

- **Separate Persistence**: `data/ledger.db` (Checkout + Worker Queue + Ledger) and `data/provider.db` (Payment Provider Simulator) are strictly separate SQLite databases.
- **No Floating-Point Math**: All monetary values use Python `decimal.Decimal` in memory and exact `TEXT` decimal strings (with explicit currency codes `'USD'` / `'EUR'`) in SQLite.
- **Traceability**: Every discrepancy in reconciliation must be computed dynamically from individual rows in `ledger_entries`, `provider_transactions`, and `data/settlements/*.csv` — never hardcoded.
