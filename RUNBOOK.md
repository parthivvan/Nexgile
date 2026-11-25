# Operational Runbook (Simulated Incident Baseline)

> **SIMULATED ENVIRONMENT**: All accounts, transactions, and settlement files are synthetic. Never execute automated customer clawbacks (`scripts/clawback.py`) without explicit finance and legal sign-off.

## System Components

| Component | Role | Default Port / Mode |
| --- | --- | --- |
| `checkout/` | Checkout API (FastAPI) | HTTP `:8001` |
| `provider_sim/` | Payment Provider Simulator (FastAPI + own SQLite) | HTTP `:8002` |
| `worker/` | Refund Worker (4 parallel processes) | Background processes |
| `ledger/` | Financial Ledger & Reconciliation library/API | HTTP `:8003` / CLI |
| `frontend/` | React + Vite Reconciliation UI | HTTP `:5173` |

## Failure Injection Controls

Failure injection is controlled via environment variables or `loadgen/` runtime configuration (to be wired in Stage 2):

- `FAULT_PROVIDER_TIMEOUT_RATE` — Probability `[0.0, 1.0]` that `provider_sim` processes a refund and commits it, then delays response past the worker HTTP timeout.
- `FAULT_PROVIDER_SLOW_MS` — Artificial response latency in milliseconds.
- `FAULT_WORKER_CRASH_AFTER_PROVIDER` — Probability `[0.0, 1.0]` that a worker process terminates (`SIGKILL` / `os._exit`) after receiving provider `200 OK` but before committing the ledger entry.
- `FAULT_DUPLICATE_SUBMIT_RATE` — Probability that `loadgen` sends duplicate client requests (web/mobile retries without idempotency keys).

## Incident Baseline Verification (Planned)

1. Generate deterministic August–October dataset (`scripts/seed_data.py --seed 20261031`).
2. Stream worker logs on demand (`scripts/generate_worker_logs.py --stream`).
3. Run reconciliation for October (`python -m ledger.reconcile --month 2026-10`).
4. Verify computed provider-vs-ledger gap equals **`$41,212.87`** from individual transaction records (never hardcoded).
