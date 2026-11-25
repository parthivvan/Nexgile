# DECISIONS

## 2026-11-25 — Stage 2A: Payment Provider Simulator (`provider_sim/`)

### Context Reviewed
- Reviewed the revised [UNDERSTANDING.md](UNDERSTANDING.md), along with `README.md`, `RUNBOOK.md`, `docs/architecture.md`, `docs/api_contracts.md`, and `db/schemas/provider.sql`.
- Key architectural distinction acknowledged: **duplicate provider refunds** (e.g., two payouts to a customer) and **provider-versus-ledger financial discrepancies** are distinct phenomena. A duplicate payout only creates a book-vs-provider gap when the provider and ledger record different totals (e.g., timeout + blind retry, unsafe concurrent claim where only one ledger entry is posted, or post-provider worker crash).

### Decision: Build `provider_sim/` First
- Implementing `provider_sim/` before `checkout/`, `worker/`, and `ledger/` establishes an independent, verifiable external payment boundary backed by its own SQLite database (`data/provider.db`).
- This allows us to test provider idempotency, `Decimal` USD/EUR conversion, post-commit timeout fault injection, read-only operation lookup, and daily settlement CSV export in isolation before wiring the broken client/worker behaviours in later stages.

### Scope & Risks of Stage 2A
- **In Scope**:
  - `data/provider.db` initialization using `db/schemas/provider.sql` (with `WAL` mode, busy timeout, and a partial unique index on `idempotency_key WHERE idempotency_key IS NOT NULL` for concurrent safety without altering table columns).
  - `POST /provider/v1/refunds` with `Decimal` validation, `USD`/`EUR` support, optional `idempotency_key` (enforcing `409 Conflict` on payload mismatch and returning the existing transaction on exact replay, while allowing multiple transactions when `idempotency_key` is omitted).
  - Read-only operation lookup (`GET /provider/v1/refunds/{ref}` / query by `idempotency_key` or `merchant_refund_ref`).
  - Runtime fault injection (`POST /provider/v1/faults`, `GET /provider/v1/faults`, env vars) including post-commit timeout where the transaction is committed to `data/provider.db` before delaying the HTTP response.
  - Deterministic daily settlement CSV generation (`GET /provider/v1/settlements/{date_str}`).
  - Provider-specific `pytest` suite.
- **Out of Scope**: `checkout/`, `worker/`, `ledger/`, `loadgen/`, synthetic 300k-row seeding, and `frontend/`.
- **Risks & Mitigations**:
  - *FX rate availability*: Because `data/fx_rates.csv` is not seeded until Stage 3, `provider_sim/` will read `EUR -> USD` rates from `data/fx_rates.csv` (or an injected/configured rate table) and fail explicitly (HTTP `422`/`503`) if a requested date's FX rate is missing rather than inventing historical rates or using `float`.
  - *SQLite concurrency*: Concurrent requests with the same `idempotency_key` could race in Python; mitigated with `BEGIN IMMEDIATE` transactions, `busy_timeout`, and a partial unique index on `idempotency_key WHERE idempotency_key IS NOT NULL`.
