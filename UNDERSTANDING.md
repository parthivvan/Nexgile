# UNDERSTANDING

> **Project Status & Epistemic Notice**: This is a **simulated project being constructed from scratch** (currently at the end of Stage 1). Only repository scaffolding (`__init__.py` placeholders, `.gitkeep` markers, `pyproject.toml`), architectural/API documentation, and SQLite DDL schemas (`db/schemas/ledger.sql`, `db/schemas/provider.sql`) exist today. **No application code, synthetic datasets, worker logs, or test suites have been implemented or executed yet.** Accordingly, no actual customer transactions have been investigated, no financial cause has been verified, and all incident figures referenced in the design docs (such as the `$41,212.87` October discrepancy and `37` duplicate-refund customers) are **design targets**, not investigation results. All failure behaviors described below are **planned baseline failure behaviors** documented for Stage 2–3 construction, not bugs observed in a running application.

---

## 1. SYSTEM UNDERSTANDING

### 1.1 Existing Scaffolding vs. Planned Functionality

- **Existing today (Stage 1)**:
  - Package directories with empty/docstring-only `__init__.py` files: `checkout/`, `worker/`, `provider_sim/`, `ledger/`, `loadgen/`, `scripts/`, `tests/`.
  - Placeholder directories for runtime artifacts and UI: `data/`, `data/settlements/`, `logs/worker/`, `frontend/`.
  - SQLite DDL schemas: `db/schemas/ledger.sql` (internal database `data/ledger.db`) and `db/schemas/provider.sql` (external provider database `data/provider.db`), both enforcing `TEXT` decimal representation and `is_simulated` flags.
  - Project documentation and configuration: `README.md`, `RUNBOOK.md`, `IMPLEMENTATION_PLAN.md`, `pyproject.toml`, `docs/architecture.md`, `docs/api_contracts.md`, and `docs/incident_scenarios.md`.
- **Planned (Stages 2–4, not yet implemented)**:
  - FastAPI services (`checkout/`, `provider_sim/`, `ledger/`), the 4-process `worker/` pool, `loadgen/` fault-injection harness, deterministic August–October seed generator (`scripts/seed_data.py`), on-demand streaming worker log generator (`scripts/generate_worker_logs.py`), read-only `scripts/clawback.py` stub, `pytest` suite, and the React + Vite `frontend/`.

### 1.2 Component Responsibilities

1. **Checkout API (`checkout/` — planned FastAPI on `:8001`)**
   - **Planned responsibility**: Edge HTTP service that receives refund requests (`POST /api/v1/refunds`) from web and mobile clients in `USD` or `EUR`, validates inputs (parsing `amount` with `Decimal`), persists a `refund_requests` row, and enqueues a `refund_jobs` row in `data/ledger.db` inside a `sqlite3` transaction.
   - **Planned baseline flaw**: `client_idempotency_key` in `refund_requests` is nullable and has no `UNIQUE` constraint in `db/schemas/ledger.sql`, so duplicate client submissions (double-taps or network retries) will be allowed to create distinct `refund_request_id` and `refund_jobs` records.

2. **Refund Worker (`worker/` — planned 4 parallel OS processes)**
   - **Planned responsibility**: Four concurrent worker processes that poll `refund_jobs` in `data/ledger.db` (planned 100ms interval), claim queued jobs, call the payment provider over HTTP (`POST /provider/v1/refunds` with a planned `2.0s` timeout), handle retries/failures, and record completed refunds into `ledger_entries`.
   - **Planned baseline flaws**: `refund_jobs` has `claimed_by` and `claimed_at` columns without a lease token or atomic claim contract. The planned baseline implementation will intentionally use a non-atomic `SELECT ... WHERE status = 'QUEUED'` followed by a separate `UPDATE`, blind retries on timeout without an idempotency key, and a non-transactional gap between receiving provider `200 OK` and committing `ledger_entries`.

3. **Payment Provider Simulator (`provider_sim/` — planned FastAPI on `:8002`)**
   - **Planned responsibility**: Simulates an external payment processor with strictly separate persistence in `data/provider.db` (`provider_transactions` and `settlement_batches`). It will execute simulated `USD` and `EUR` refunds, record `usd_settled_amount` and `fx_rate_applied`, expose runtime fault-injection controls (`POST /provider/v1/faults`), and export daily settlement CSVs (`data/settlements/settlement_YYYY-MM-DD.csv`) grouped by `settlement_date`.
   - **Planned baseline flaw**: `provider_transactions.idempotency_key` is nullable and non-unique when `NULL`, so repeated calls for the same `merchant_refund_ref` without an idempotency key will create new `provider_tx_id` rows.

4. **Financial Ledger & Reconciliation (`ledger/` — planned library & FastAPI on `:8003`)**
   - **Planned responsibility**: Authoritative internal accounting component in `data/ledger.db`. It will store `ledger_entries` (`original_amount`, `original_currency`, `fx_rate_used`, `usd_amount`, `accounting_month`, `posted_at`), maintain `fx_rates`, record `monthly_closes`, and run dynamic provider-vs-ledger reconciliation (`reconciliation_runs`, `reconciliation_items`) comparing `ledger_entries` against `provider_transactions` / daily settlement CSVs.

### 1.3 Planned Refund Lifecycle & Inter-Component Communication

1. **Client Submission**: A web or mobile client sends `POST /api/v1/refunds` to `checkout/` with `order_id`, `customer_id`, `amount`, `currency`, `channel`, `reason`, and an optional `idempotency_key`.
2. **Request & Job Enqueue**: `checkout/` writes to `refund_requests` and inserts a `QUEUED` row into `refund_jobs` in `data/ledger.db`, returning `202 Accepted` with `refund_request_id` and `job_id`.
3. **Job Claiming**: One of the 4 `worker/` processes polls `refund_jobs` in `data/ledger.db`, transitions a job to `PROCESSING`, and increments `attempt_count`.
4. **Provider Execution**: The worker sends `POST /provider/v1/refunds` to `provider_sim/` with `merchant_refund_ref` (`refund_request_id`), `order_id`, `customer_id`, `amount`, `currency`, and (in the planned baseline) `idempotency_key: null`. `provider_sim/` persists a `SETTLED` row in `data/provider.db` (`provider_transactions`) and returns `provider_tx_id`, `usd_settled_amount`, `fx_rate_applied`, and `settled_at`.
5. **Ledger Posting & Job Completion**: Upon receiving `200 OK`, the worker inserts a `REFUND` row into `ledger_entries` (converting `EUR` to `USD` via `fx_rates` if applicable, and assigning `accounting_month` and `posted_at`) and updates `refund_jobs.status = 'SUCCEEDED'`.
6. **Settlement & Reconciliation**: Periodically/daily, `provider_sim/` generates `data/settlements/settlement_YYYY-MM-DD.csv` from `provider_transactions`. At month-end, `ledger/` compares `SUM(usd_settled_amount)` from provider settlements against `SUM(usd_amount)` in `ledger_entries` for the target `accounting_month` to produce `reconciliation_runs` and `reconciliation_items`.

---

## 2. RANKED HYPOTHESES

All hypotheses below are **unverified** and ranked by expected financial impact and plausibility given the planned baseline design. None has been proven because no application code, runtime data, or logs exist yet.

### Rank 1: Provider Timeout and Retry Behaviour (Duplicate Provider Refunds)
- **Why it is plausible**: In `docs/api_contracts.md`, `provider_sim/` is designed to commit a `provider_transactions` row before sleeping past the worker's `2.0s` HTTP read timeout, and the planned baseline worker sends `idempotency_key: null` on retries. Because `provider_transactions.idempotency_key` is nullable and non-unique, every retry after a timeout can create an additional `SETTLED` provider transaction while the worker only posts one `ledger_entries` row upon the eventual `200 OK`.
- **Evidence that would confirm it**: Multiple `provider_transactions` rows sharing the same `merchant_refund_ref` with distinct `provider_tx_id`s and timestamps separated by ~2+ seconds, paired with only one `ledger_entries` row for that `refund_request_id`, and worker logs showing `ReadTimeout` followed by a retry and `200 OK`.
- **Evidence that would rule it out**: `COUNT(provider_tx_id) == 1` per `merchant_refund_ref` across all timed-out jobs, or worker logs showing zero timeout-triggered retries during October.

### Rank 2: Concurrent Worker Processing (Unsafe Job Claiming)
- **Why it is plausible**: Four worker processes poll `refund_jobs` concurrently, and `db/schemas/ledger.sql` has no atomic lease/lock column or `UNIQUE(refund_request_id)` on `ledger_entries`. Under non-atomic `SELECT` followed by `UPDATE`, two workers can claim the same `job_id` and both call `provider_sim/`, producing two `provider_transactions` rows. Importantly, **duplicate provider operations do not automatically create a provider-versus-ledger financial discrepancy if both operations are also correctly recorded in `ledger_entries`** (in that case, the customer is refunded twice, but `provider_total_usd - ledger_total_usd == 0`). A provider-versus-ledger discrepancy arises only when the second provider payout is **not** matched by a corresponding ledger entry—for example, if one worker fails/crashes before ledger commit, or if the ledger rejects/skips the second posting.
- **Evidence that would confirm it**: Worker logs showing two distinct worker PIDs/IDs claiming and calling `POST /provider/v1/refunds` for the same `job_id` / `refund_request_id` within milliseconds of each other, alongside two `provider_transactions` rows for one `refund_jobs` row and only one matching `ledger_entries` row.
- **Evidence that would rule it out**: Job-claim logs showing strictly disjoint `job_id` sets across all 4 worker processes, or (where duplicate claims occurred) every duplicate `provider_tx_id` having a matching `ledger_entries` row so the net book-vs-provider delta is `$0.00`.

### Rank 3: Missing Ledger Postings (Worker Crashes Between Provider Success and Ledger Commit)
- **Why it is plausible**: `data/provider.db` and `data/ledger.db` are separate SQLite databases, so a single local SQLite transaction cannot span both. If a worker process terminates after `provider_sim/` commits and returns `200 OK`—but before the worker commits to `ledger_entries`—the provider records a disbursed refund with zero corresponding `ledger_entries` row.
- **Evidence that would confirm it**: `provider_transactions` rows with `status = 'SETTLED'` whose `provider_tx_id` and `merchant_refund_ref` have zero matching rows in `ledger_entries`, correlated with `refund_jobs` stuck in `PROCESSING` (or retried) and worker logs terminating abruptly after provider `200 OK`.
- **Evidence that would rule it out**: Every `SETTLED` `provider_tx_id` in October having at least one matching `ledger_entries` row.

### Rank 4: FX Conversion Errors (EUR-to-USD Miscalculation)
- **Why it is plausible**: `ledger_entries` stores `original_amount`, `original_currency`, `fx_rate_used`, and `usd_amount` separately from `provider_transactions.usd_settled_amount`. If the planned worker or ledger applies an inverted rate (`amount_eur / rate` instead of `amount_eur * rate`), uses a stale `rate_date`, or applies inconsistent `Decimal` rounding compared to `provider_sim/`, every affected `EUR` refund introduces a systematic per-transaction USD delta.
- **Evidence that would confirm it**: `EUR` transactions where `provider_transactions.provider_tx_id == ledger_entries.provider_tx_id` (1-to-1 match, no duplicates), `original_amount` and `original_currency` match, yet `ledger_entries.usd_amount != provider_transactions.usd_settled_amount` and `ledger_entries.usd_amount` equals `original_amount / fx_rate_used` (or uses a mismatched `fx_rates` date).
- **Evidence that would rule it out**: Exact equality `ledger_entries.usd_amount == provider_transactions.usd_settled_amount` across all 1-to-1 matched `EUR` transactions in October.

### Rank 5: Settlement Cutoff Differences (Month-End Timing)
- **Why it is plausible**: `provider_transactions` groups daily settlement CSVs by `settlement_date` (`YYYY-MM-DD`), whereas `ledger_entries` groups by `accounting_month` (`YYYY-MM`) and `posted_at`. Late `2026-10-31` UTC transactions can settle in October's provider CSV while being posted to `2026-11` in `ledger_entries` (or vice versa), creating a timing discrepancy at month-end close.
- **Evidence that would confirm it**: Transactions settled in `data/settlements/settlement_2026-10-31.csv` (with `settlement_date = '2026-10-31'`) whose matching `ledger_entries` row has `accounting_month = '2026-11'` (and `posted_at` in early November), such that the October excess is offset by an equal-and-opposite November difference.
- **Evidence that would rule it out**: All `2026-10-31` `provider_transactions` having matching `ledger_entries` with `accounting_month = '2026-10'`, and zero cross-month boundary pairs.

### Rank 6: Duplicate Provider Refunds via Duplicate API Submissions
- **Why it is plausible**: In the planned baseline, `checkout/` does not enforce `client_idempotency_key` in `refund_requests`, so web/mobile retries create multiple `refund_requests` and `refund_jobs` for the same `order_id` and `customer_id`. While this directly causes customers to be refunded twice by the provider, if both jobs complete normally and post to `ledger_entries`, both sides record the duplicate (`provider_usd == ledger_usd`, `$0.00` book-vs-provider gap) unless one leg fails or is excluded at close.
- **Evidence that would confirm it**: Multiple `refund_requests` rows with identical `(order_id, customer_id, amount, currency)` created seconds apart, each linked to its own `provider_tx_id` and `ledger_entries` row.
- **Evidence that would rule it out**: Strict 1-to-1 cardinality between `(order_id, amount, currency)` and `refund_requests` across all web and mobile submissions.

---

## 3. ALTERNATIVE SOLUTIONS

### Option A: Targeted Fixes to Existing Components
Keep the current 4-component structure and table layout, applying localized patches to each planned bug:
- **Checkout API**: Require a non-null `client_idempotency_key` scoped per `(customer_id, order_id, client_idempotency_key)` with a `UNIQUE` constraint and stored `request_payload_hash` (returning `409 Conflict` if the same key is reused with a different payload, or returning the existing `refund_request_id` if the payload matches).
- **Worker & Ledger**: Replace `SELECT`-then-`UPDATE` with atomic `UPDATE refund_jobs SET status = 'PROCESSING', claimed_by = ?, claimed_at = ? WHERE job_id = (SELECT job_id FROM refund_jobs WHERE status = 'QUEUED' LIMIT 1) RETURNING *`, pass `refund_request_id` as `idempotency_key` to `provider_sim/`, and add both `UNIQUE(provider_tx_id)` and a logical-refund uniqueness constraint (e.g., `UNIQUE(refund_request_id, entry_type)`) on `ledger_entries`.
- **Ledger & Cutoff**: Fix `EUR * rate` `Decimal` multiplication and align `accounting_month` with `settlement_date` (or track in-transit clearing).

### Option B: Coordinated Refund State Machine with Idempotency, Safe Retries, and Reliable Ledger Posting
Redesign the refund execution path around an explicit, durable state machine operating directly over `refund_requests`, `refund_jobs`, and `ledger_entries`:
- **End-to-End Idempotency & Payload Validation**:
  - Require a **non-null** `client_idempotency_key` on `POST /api/v1/refunds`, scoped to `(customer_id, order_id, client_idempotency_key)` (allowing distinct keys for legitimate partial refunds on the same `order_id`, each producing its own `refund_request_id`).
  - Store a canonical `request_payload_hash` (e.g., SHA-256 of `(order_id, customer_id, amount, currency)`) alongside the key: if the same key is repeated with an identical payload, return the existing `refund_request_id` (`200`/`202`); if repeated with a **different payload**, reject with `409 Conflict` without creating or modifying any refund.
  - Note that a `UNIQUE(provider_tx_id)` constraint alone is **insufficient** because it only blocks duplicate posting of the same `provider_tx_id`, not two distinct `provider_tx_id`s created for the same logical refund. Therefore, enforce uniqueness on **both** `provider_tx_id` (`UNIQUE(provider_tx_id)`) and the logical refund posting (`UNIQUE(refund_request_id, entry_type)`), which blocks duplicate postings for one request while still supporting legitimate partial refunds (each partial refund has a distinct `refund_request_id`).
- **Leased State Machine with Uncertain-Outcome Separation**:
  - Model `refund_jobs` as an explicit state machine: `QUEUED` -> `CLAIMED` (atomic `UPDATE ... RETURNING` with `lease_token` and `lease_expires_at`) -> `PROVIDER_PENDING` -> `PROVIDER_CONFIRMED` -> `SUCCEEDED`, with separate `PROVIDER_UNCERTAIN` and `FAILED` states.
  - Distinguish an **uncertain provider outcome** (`ReadTimeout`, connection reset, `5xx`, or worker crash after sending the request -> `PROVIDER_UNCERTAIN`) from a **confirmed failure** (deterministic `4xx` validation/business decline -> `FAILED`).
  - Recover from `PROVIDER_UNCERTAIN` by querying `provider_sim/`'s operation lookup endpoint (by `idempotency_key` / `merchant_refund_ref`) or retrying with the **exact same stable `provider_idempotency_key`** (derived deterministically from `refund_request_id`). **Never** generate a new `provider_idempotency_key` or create a new provider operation simply because an earlier request timed out.
- **Idempotent Ledger Posting & Recovery**:
  - Persist `provider_tx_id` and settled USD details on `refund_jobs` as soon as the provider confirms, then commit `ledger_entries` idempotently (protected by `UNIQUE(refund_request_id, entry_type)` and `UNIQUE(provider_tx_id)`). If a worker crashes after provider confirmation and before ledger commit, lease expiry allows another worker to resume from `PROVIDER_CONFIRMED` without re-calling the provider's mutation endpoint or double-posting.
- **Canonical FX & Clearing Accounting**:
  - Centralize `Decimal` FX conversion (`EUR * rate`) in `ledger/` using `settlement_date` rates, and introduce an explicit clearing/in-transit distinction for month-end cutoff boundaries.

### Comparison

| Dimension | Option A: Targeted Fixes | Option B: Coordinated State Machine |
| --- | --- | --- |
| **Complexity** | Low — small, localized SQL and Python changes. | Moderate — requires explicit state transitions, lease expiry, payload-hash checks, and provider status lookup on recovery. |
| **Reliability** | Medium — fixes known bugs individually, but leaves crash recovery gaps (e.g., if a worker dies after provider `200 OK` and before `ledger_entries`, a stale `PROCESSING` job still needs ad-hoc recovery). | High — eliminates double payouts, handles crashes and timeouts safely via `PROVIDER_UNCERTAIN` recovery with a stable `provider_idempotency_key`, and makes every transition auditable. |
| **Implementation Risk** | Low short-term risk, higher long-term risk of edge-case regressions under concurrent crashes. | Moderate short-term risk (schema migration + state transition logic), much lower long-term operational risk. |
| **Maintainability** | Harder to reason about implicit states (`PROCESSING` could mean "not called provider yet" or "called provider, crashed before ledger"). | High — explicit states and invariants make debugging, log correlation, and testing straightforward. |

### Recommendation

**Recommend Option B (Coordinated Refund State Machine)**—implemented cleanly in Stage 5 *after* the Stage 2–4 broken baseline is built and verified. Because `data/provider.db` and `data/ledger.db` are separate SQLite databases, localized patches alone (Option A) cannot cleanly recover from a crash or timeout between provider commit and ledger commit without risking either a lost ledger entry or a stuck `PROCESSING` job. A small, explicit state machine with scoped non-null client idempotency keys, payload-hash conflict detection, `PROVIDER_UNCERTAIN` recovery over a stable `provider_idempotency_key`, and dual uniqueness constraints (`provider_tx_id` + logical refund posting) solves all failure modes cohesively while staying within the existing Python 3.12 + `sqlite3` + `Decimal` stack.
