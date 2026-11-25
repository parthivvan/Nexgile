# Phased Implementation Plan

> **Working Method**: Build in small, verifiable stages. Do **not** implement remediations until the broken incident baseline is complete and verified.

## Stage 1 — Architecture, Schemas & Repository Foundation *(Current Stage)*
- [x] Create repository directory structure (`checkout/`, `worker/`, `provider_sim/`, `ledger/`, `loadgen/`, `scripts/`, `db/schemas/`, `docs/`, `frontend/`, `tests/`, `data/`, `logs/`).
- [x] Document initial broken architecture (`docs/architecture.md`) and API contracts (`docs/api_contracts.md`).
- [x] Define SQLite schemas (`db/schemas/ledger.sql`, `db/schemas/provider.sql`) with `TEXT` Decimal storage and `is_simulated` flags.
- [x] Define traceable incident scenarios and exact `$41,212.87` October gap breakdown (`docs/incident_scenarios.md`).
- [x] Create `README.md`, `RUNBOOK.md`, `pyproject.toml`, `.gitignore`.

## Stage 2 — Broken Baseline Services & Failure Injection
- [ ] Implement `provider_sim/` FastAPI service with `data/provider.db`, configurable latency/post-commit timeouts, and daily settlement CSV export.
- [ ] Implement `checkout/` FastAPI service with `data/ledger.db` persistence and unenforced idempotency.
- [ ] Implement `worker/` 4-process pool with intentional unsafe `SELECT`-then-`UPDATE` claiming, blind retries, crash injection, and buggy EUR-to-USD conversion.
- [ ] Implement `loadgen/` harness to drive web/mobile traffic with toggleable fault injection.
- [ ] Add `pytest` tests verifying that the broken baseline reproduces each failure mode under load.

## Stage 3 — Deterministic Synthetic Data & Reconciliation Engine
- [ ] Implement `scripts/seed_data.py` with reproducible PRNG seed to generate:
  - ~300,000 ledger transactions (Aug–Oct) with `Decimal` precision.
  - 92 daily settlement CSVs in `data/settlements/` and `data/fx_rates.csv`.
  - 37 customers affected by duplicate provider refunds.
  - Exact `$41,212.87` October provider-vs-ledger gap from individual traceable records.
- [ ] Implement `scripts/generate_worker_logs.py` for on-demand streaming/batched generation of ~1.5M worker log lines in `logs/worker/`.
- [ ] Implement `ledger/reconcile.py` and `ledger/` FastAPI endpoints to compute discrepancies dynamically from SQLite + CSVs (never hardcoded).
- [ ] Add `scripts/clawback.py` as a **read-only analysis / dry-run stub** (no automatic customer charges).

## Stage 4 — React + Vite Reconciliation Dashboard
- [ ] Scaffold `frontend/` with React + Vite.
- [ ] Build views for:
  - Monthly reconciliation summary (provider vs. ledger totals, computed gap).
  - Cause category breakdown & traceable transaction drill-down.
  - 37 duplicate-refund customers review queue (read-only recommendations, no auto-clawback).
  - Fault injection & live simulation status panel.

## Stage 5 — Investigation, Remediation & Verification *(Future — After Baseline)*
- [ ] Document `UNDERSTANDING.md` and `DECISIONS.md` before changing broken baseline code.
- [ ] Remediate all 7 root causes (idempotency keys at API & provider, atomic `UPDATE ... RETURNING` job claiming, crash recovery/outbox, exact `Decimal` EUR*rate FX math, UTC month-end cutoff alignment).
- [ ] Verify with `pytest` under concurrent load with failures enabled, and produce `REPORT.md`.
