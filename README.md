# Refund Management & Financial Reconciliation System (Simulated Incident Baseline)

> **Note**: All data, transactions, settlement files, FX rates, and worker logs in this repository are **SIMULATED** for incident reproduction and architectural remediation exercises.

## Overview

This repository houses a production-style Refund Management and Financial Reconciliation System built with **Python 3.12**, **FastAPI**, **SQLite (`sqlite3`)**, **`Decimal`** financial arithmetic, and a **React + Vite** frontend.

The initial baseline intentionally reproduces realistic distributed-systems and financial-accounting failures that result in an **October provider-versus-ledger discrepancy of exactly `$41,212.87`** across ~300,000 ledger transactions (August–October), ~1.5M on-demand worker log lines, daily provider settlement files, and 37 customers affected by duplicate provider refunds.

## Repository Structure

- `checkout/` — **Checkout API**: FastAPI service accepting refund requests from web/mobile clients, persisting requests, and enqueuing refund jobs.
- `worker/` — **Refund Worker**: 4 parallel worker processes that claim queued jobs, call the payment provider, handle retries/crashes, and post to the internal ledger.
- `provider_sim/` — **Payment Provider Simulator**: Independent FastAPI service with its own SQLite database simulating external card/bank refunds, configurable latency/timeouts, and daily settlement file generation.
- `ledger/` — **Financial Ledger & Reconciliation**: Internal accounting module managing USD/EUR entries, FX conversion, month-end close, and provider-vs-ledger reconciliation.
- `loadgen/` — **Load & Failure Injection Generator**: Configurable traffic and fault-injection harness (timeouts, slow responses, worker crashes).
- `scripts/` — **Operational & Seed Scripts**: Deterministic August–October synthetic data generator, on-demand streaming worker log generator, and team scripts (including `clawback.py` — **not** auto-executed).
- `db/schemas/` — **SQLite Schemas**: DDL definitions for internal (`ledger.sql`) and provider (`provider.sql`) databases.
- `docs/` — **Architecture, API Contracts & Incident Specs**: System design, endpoint/queue contracts, and traceable incident scenario definitions.
- `frontend/` — **React + Vite UI**: Reconciliation dashboard and incident explorer (planned for Stage 4).
- `tests/` — **Pytest Suite**: Incident reproduction tests and (in later stages) remediation verification tests.
- `data/` & `logs/` — **Simulated Artifacts**: Generated SQLite databases, daily settlement CSVs, FX rate history, and streaming worker logs.

## Quick Links

- [Architecture Documentation](docs/architecture.md)
- [API Contracts](docs/api_contracts.md)
- [SQLite Schemas](db/schemas/ledger.sql) & [Provider Schema](db/schemas/provider.sql)
- [Simulated Incident Scenarios](docs/incident_scenarios.md)
- [Implementation Plan](IMPLEMENTATION_PLAN.md)
- [Operational Runbook](RUNBOOK.md)
