# Simulated Incident Scenarios (August–October Baseline)

> **SIMULATED DATA NOTICE**: Every transaction, customer ID, settlement CSV, FX rate, and worker log line is deterministically generated synthetic data (`is_simulated = 1`).

## 1. Dataset Scale & Deterministic Seeding

- **Period**: `2026-08-01` through `2026-10-31` (August, September, October).
- **Ledger Volume**: Approximately **300,000** `ledger_entries` rows across August–October (~100,000/month), with matching `refund_requests`, `refund_jobs`, and `provider_transactions`.
- **Worker Logs**: Approximately **1,500,000** structured log lines in `logs/worker/`, generated **on demand** using streaming and batch processing (no massive static log blob committed to Git).
- **Daily Settlement Files**: 92 daily CSV files in `data/settlements/settlement_YYYY-MM-DD.csv`.
- **FX History**: Daily `EUR -> USD` exchange rates in `data/fx_rates.csv` and `fx_rates` table.
- **Duplicate-Refund Customers**: **37 customers** affected by duplicate provider refunds.
- **October Discrepancy**: Exact computed provider-vs-ledger difference of **`$41,212.87`** (Provider settled USD > Ledger recorded USD), derived dynamically from individual transaction records.

## 2. The Seven Intentional Baseline Failure Modes

| # | Failure Mode | Component(s) | Mechanism |
| --- | --- | --- | --- |
| 1 | **Duplicate API refund submissions** | `checkout/` | `POST /api/v1/refunds` does not enforce `client_idempotency_key`; web/mobile retries create distinct `refund_request_id` and `refund_jobs` rows. |
| 2 | **Unsafe concurrent job claiming** | `worker/` | 4 parallel workers run `SELECT ... WHERE status='QUEUED'` followed by separate `UPDATE`, allowing two workers to claim and pay the same job. |
| 3 | **Provider timeout after successful processing** | `provider_sim/` + `worker/` | `provider_sim` commits to `data/provider.db` then sleeps past the worker's `2.0s` HTTP timeout. |
| 4 | **Retry requests creating duplicate provider refunds** | `worker/` + `provider_sim/` | On timeout, the worker retries without an `idempotency_key`, causing `provider_sim` to issue a second `provider_tx_id`. |
| 5 | **Worker crashes between provider success & ledger posting** | `worker/` | Process crash (`os._exit` / fault injection) after `200 OK` from `provider_sim` leaves `provider_transactions` committed with no `ledger_entries` row. |
| 6 | **Incorrect EUR-to-USD conversion** | `ledger/` + `worker/` | Ledger posting applies inverted/wrong-direction EUR-to-USD conversion (`eur / rate` instead of `eur * rate`) on affected EUR refunds, under-recording USD value relative to provider settlement. |
| 7 | **Month-end settlement & ledger cutoff differences** | `provider_sim/` + `ledger/` | Late `2026-10-31` UTC refunds appear in October provider settlement CSVs (`settlement_date = '2026-10-31'`) while ledger records them in `2026-11`. |

## 3. Traceable October Discrepancy Breakdown (`$41,212.87`)

Reconciliation computes `SUM(provider_usd_settled) - SUM(ledger_usd_amount)` for `2026-10` directly from SQLite and settlement CSVs. The seeded October incident produces the following traceable categories:

| Cause Category | Affected Customers | Excess Provider Txs / Rows | October Gap (`USD`) | Notes |
| --- | --- | --- | --- | --- |
| `timeout_blind_retry_duplicate` | 15 | 15 extra provider txs | `$14,820.50` | Provider timed out after commit; worker retried without idempotency key (2 provider txs, 1 ledger entry). |
| `unsafe_concurrent_claim_duplicate` | 12 | 12 extra provider txs | `$9,645.12` | Two workers claimed the same `refund_job`; provider paid twice, ledger recorded once. |
| `duplicate_api_submission` | 10 | 10 extra provider & ledger txs | `$0.00` | Web/mobile double-submit paid customer twice; both sides recorded it (part of the **37** double-refunded customers, `$0.00` book-vs-provider gap). |
| `worker_crash_unrecorded_ledger` | 18 | 18 provider-only txs | `$8,310.45` | Worker crashed after provider `200 OK` and before `ledger_entries` commit. |
| `eur_fx_conversion_error` | 142 | 142 EUR txs | `$5,184.20` | Ledger used inverted/incorrect EUR-to-USD math on affected October EUR refunds. |
| `month_end_cutoff_timing` | 9 | 9 Oct-31 cutoff txs | `$3,252.60` | Settled by provider on `2026-10-31`, posted to `2026-11` in internal ledger. |
| **Total** | **37 duplicate-refund customers** (+169 other affected) | **206 traceable records** | **`$41,212.87`** | **Computed dynamically from records; never hardcoded.** |

## 4. Non-Automated Clawback Policy

`scripts/clawback.py` is documented as a **dry-run / reporting stub only** in the baseline. Automated customer clawbacks must **never** be executed automatically; policy decisions (write-off threshold vs. customer outreach) require explicit human review.
