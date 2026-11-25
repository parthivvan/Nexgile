-- Independent Payment Provider Simulator Schema
-- Database: data/provider.db (SQLite 3)
-- Strictly separate from data/ledger.db.
-- Monetary rule: All monetary amounts and FX rates are stored as exact TEXT
-- decimal strings and parsed via Python decimal.Decimal. Never use REAL/FLOAT.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS provider_transactions (
    provider_tx_id      TEXT PRIMARY KEY,
    merchant_refund_ref TEXT NOT NULL,             -- Maps to refund_requests.refund_request_id
    order_id            TEXT NOT NULL,
    customer_id         TEXT NOT NULL,
    amount              TEXT NOT NULL,             -- Exact Decimal string
    currency            TEXT NOT NULL CHECK (currency IN ('USD', 'EUR')),
    fx_rate_applied     TEXT NOT NULL,             -- Exact Decimal string
    usd_settled_amount  TEXT NOT NULL,             -- Exact Decimal string settled in USD
    idempotency_key     TEXT,                      -- Nullable & non-unique when NULL in baseline
    status              TEXT NOT NULL CHECK (status IN ('SETTLED', 'FAILED')),
    settlement_date     TEXT NOT NULL,             -- 'YYYY-MM-DD'
    settlement_batch_id TEXT,
    settled_at          TEXT NOT NULL,             -- ISO-8601 UTC timestamp
    is_simulated        INTEGER NOT NULL DEFAULT 1 CHECK (is_simulated IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_provider_tx_ref
    ON provider_transactions (merchant_refund_ref);

CREATE INDEX IF NOT EXISTS idx_provider_tx_settlement_date
    ON provider_transactions (settlement_date);

CREATE INDEX IF NOT EXISTS idx_provider_tx_customer
    ON provider_transactions (customer_id);

CREATE TABLE IF NOT EXISTS settlement_batches (
    settlement_batch_id TEXT PRIMARY KEY,
    settlement_date     TEXT NOT NULL UNIQUE,      -- 'YYYY-MM-DD'
    file_path           TEXT NOT NULL,             -- e.g. 'data/settlements/settlement_2026-10-31.csv'
    tx_count            INTEGER NOT NULL,
    total_usd           TEXT NOT NULL,             -- Exact Decimal string
    generated_at        TEXT NOT NULL,
    is_simulated        INTEGER NOT NULL DEFAULT 1 CHECK (is_simulated IN (0, 1))
);
