-- Internal Financial Ledger, Checkout Requests, and Worker Queue Schema
-- Database: data/ledger.db (SQLite 3)
-- Monetary rule: All monetary amounts and FX rates are stored as exact TEXT
-- decimal strings and parsed via Python decimal.Decimal. Never use REAL/FLOAT.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS refund_requests (
    refund_request_id      TEXT PRIMARY KEY,
    order_id               TEXT NOT NULL,
    customer_id            TEXT NOT NULL,
    amount                 TEXT NOT NULL,          -- Exact Decimal string, e.g. '149.99'
    currency               TEXT NOT NULL CHECK (currency IN ('USD', 'EUR')),
    channel                TEXT NOT NULL CHECK (channel IN ('web', 'mobile')),
    reason                 TEXT NOT NULL,
    client_idempotency_key TEXT,                   -- Nullable & non-unique in broken baseline
    is_simulated           INTEGER NOT NULL DEFAULT 1 CHECK (is_simulated IN (0, 1)),
    created_at             TEXT NOT NULL           -- ISO-8601 UTC timestamp
);

CREATE INDEX IF NOT EXISTS idx_refund_requests_order
    ON refund_requests (order_id, customer_id);

CREATE INDEX IF NOT EXISTS idx_refund_requests_created
    ON refund_requests (created_at);

CREATE TABLE IF NOT EXISTS refund_jobs (
    job_id            TEXT PRIMARY KEY,
    refund_request_id TEXT NOT NULL REFERENCES refund_requests (refund_request_id),
    status            TEXT NOT NULL CHECK (status IN ('QUEUED', 'PROCESSING', 'RETRY_WAIT', 'SUCCEEDED', 'FAILED')),
    attempt_count     INTEGER NOT NULL DEFAULT 0,
    claimed_by        TEXT,                        -- Worker process ID (unsafe claiming in baseline)
    claimed_at        TEXT,
    next_retry_at     TEXT,
    last_error        TEXT,
    is_simulated      INTEGER NOT NULL DEFAULT 1 CHECK (is_simulated IN (0, 1)),
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_refund_jobs_status
    ON refund_jobs (status, next_retry_at);

CREATE INDEX IF NOT EXISTS idx_refund_jobs_request
    ON refund_jobs (refund_request_id);

CREATE TABLE IF NOT EXISTS fx_rates (
    rate_date      TEXT NOT NULL,                  -- YYYY-MM-DD
    base_currency  TEXT NOT NULL CHECK (base_currency IN ('EUR', 'USD')),
    quote_currency TEXT NOT NULL CHECK (quote_currency IN ('USD', 'EUR')),
    rate           TEXT NOT NULL,                  -- Exact Decimal string, e.g. '1.084200'
    source         TEXT NOT NULL DEFAULT 'SIMULATED_ECB',
    is_simulated   INTEGER NOT NULL DEFAULT 1 CHECK (is_simulated IN (0, 1)),
    PRIMARY KEY (rate_date, base_currency, quote_currency)
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    ledger_entry_id   TEXT PRIMARY KEY,
    refund_request_id TEXT NOT NULL REFERENCES refund_requests (refund_request_id),
    provider_tx_id    TEXT,                        -- Auditable reference to provider_transactions
    customer_id       TEXT NOT NULL,
    order_id          TEXT NOT NULL,
    original_amount   TEXT NOT NULL,               -- Exact Decimal string in original_currency
    original_currency TEXT NOT NULL CHECK (original_currency IN ('USD', 'EUR')),
    fx_rate_used      TEXT NOT NULL,               -- Exact Decimal string
    usd_amount        TEXT NOT NULL,               -- Exact Decimal string in USD
    entry_type        TEXT NOT NULL CHECK (entry_type IN ('REFUND', 'REVERSAL', 'ADJUSTMENT')),
    accounting_month  TEXT NOT NULL,               -- 'YYYY-MM'
    posted_at         TEXT NOT NULL,               -- ISO-8601 UTC timestamp
    is_simulated      INTEGER NOT NULL DEFAULT 1 CHECK (is_simulated IN (0, 1)),
    notes             TEXT
);

CREATE INDEX IF NOT EXISTS idx_ledger_entries_month
    ON ledger_entries (accounting_month, posted_at);

CREATE INDEX IF NOT EXISTS idx_ledger_entries_req
    ON ledger_entries (refund_request_id);

CREATE INDEX IF NOT EXISTS idx_ledger_entries_ptx
    ON ledger_entries (provider_tx_id);

CREATE INDEX IF NOT EXISTS idx_ledger_entries_customer
    ON ledger_entries (customer_id);

CREATE TABLE IF NOT EXISTS monthly_closes (
    accounting_month   TEXT PRIMARY KEY,           -- 'YYYY-MM'
    status             TEXT NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
    ledger_total_usd   TEXT NOT NULL,
    provider_total_usd TEXT NOT NULL,
    discrepancy_usd    TEXT NOT NULL,
    closed_at          TEXT,
    is_simulated       INTEGER NOT NULL DEFAULT 1 CHECK (is_simulated IN (0, 1))
);

CREATE TABLE IF NOT EXISTS reconciliation_runs (
    run_id             TEXT PRIMARY KEY,
    accounting_month   TEXT NOT NULL,
    computed_at        TEXT NOT NULL,
    provider_total_usd TEXT NOT NULL,
    ledger_total_usd   TEXT NOT NULL,
    discrepancy_usd    TEXT NOT NULL,
    is_simulated       INTEGER NOT NULL DEFAULT 1 CHECK (is_simulated IN (0, 1))
);

CREATE TABLE IF NOT EXISTS reconciliation_items (
    item_id             TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES reconciliation_runs (run_id),
    refund_request_id   TEXT,
    provider_tx_id      TEXT,
    ledger_entry_id     TEXT,
    customer_id         TEXT,
    cause_category      TEXT NOT NULL,
    provider_usd_amount TEXT NOT NULL,
    ledger_usd_amount   TEXT NOT NULL,
    delta_usd           TEXT NOT NULL,
    evidence_ref        TEXT,
    is_simulated        INTEGER NOT NULL DEFAULT 1 CHECK (is_simulated IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_items_run
    ON reconciliation_items (run_id, cause_category);
