"""Daily settlement CSV generation from persisted provider transactions."""

from __future__ import annotations

import csv
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

DEFAULT_SETTLEMENTS_DIR = Path("data/settlements")
SETTLEMENT_COLUMNS = [
    "provider_tx_id",
    "merchant_refund_ref",
    "customer_id",
    "amount",
    "currency",
    "usd_settled_amount",
    "fx_rate_applied",
    "settlement_date",
    "status",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_daily_settlement(
    conn: sqlite3.Connection,
    settlement_date: str,
    settlements_dir: Path | str = DEFAULT_SETTLEMENTS_DIR,
) -> dict[str, object]:
    """Export deterministic daily settlement CSV and upsert settlement_batches."""
    out_dir = Path(settlements_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"settlement_{settlement_date}.csv"

    rows = conn.execute(
        """
        SELECT
            provider_tx_id,
            merchant_refund_ref,
            customer_id,
            amount,
            currency,
            usd_settled_amount,
            fx_rate_applied,
            settlement_date,
            status
        FROM provider_transactions
        WHERE settlement_date = ?
        ORDER BY settled_at ASC, provider_tx_id ASC
        """,
        (settlement_date,),
    ).fetchall()

    total_usd = sum((Decimal(row["usd_settled_amount"]) for row in rows), Decimal("0.00"))
    total_usd_str = f"{total_usd:.2f}"
    tx_count = len(rows)
    generated_at = _utc_now_iso()

    tmp_path = out_dir / f".settlement_{settlement_date}.{uuid.uuid4().hex}.tmp"
    with tmp_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(SETTLEMENT_COLUMNS)
        for row in rows:
            writer.writerow([row[col] for col in SETTLEMENT_COLUMNS])
    os.replace(tmp_path, csv_path)

    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT settlement_batch_id FROM settlement_batches WHERE settlement_date = ?",
            (settlement_date,),
        ).fetchone()
        batch_id = (
            existing["settlement_batch_id"]
            if existing is not None
            else f"BATCH-{settlement_date.replace('-', '')}-{uuid.uuid4().hex[:8].upper()}"
        )
        conn.execute(
            """
            INSERT INTO settlement_batches (
                settlement_batch_id, settlement_date, file_path, tx_count, total_usd, generated_at, is_simulated
            ) VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(settlement_date) DO UPDATE SET
                file_path = excluded.file_path,
                tx_count = excluded.tx_count,
                total_usd = excluded.total_usd,
                generated_at = excluded.generated_at
            """,
            (batch_id, settlement_date, str(csv_path), tx_count, total_usd_str, generated_at),
        )
        conn.execute(
            """
            UPDATE provider_transactions
            SET settlement_batch_id = ?
            WHERE settlement_date = ?
            """,
            (batch_id, settlement_date),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return {
        "settlement_batch_id": batch_id,
        "settlement_date": settlement_date,
        "file_path": str(csv_path),
        "tx_count": tx_count,
        "total_usd": total_usd_str,
        "generated_at": generated_at,
        "is_simulated": True,
    }
