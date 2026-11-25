"""Pytest suite for the Payment Provider Simulator (Stage 2A)."""

from __future__ import annotations

import asyncio
import csv
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from provider_sim.app import create_app
from provider_sim.faults import FaultConfig


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@pytest.fixture()
def provider_env(tmp_path: Path):
    db_path = tmp_path / "provider.db"
    settlements_dir = tmp_path / "settlements"
    fx_rates_path = tmp_path / "fx_rates.csv"
    today = _today_utc()
    with fx_rates_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rate_date", "base_currency", "quote_currency", "rate", "source"])
        writer.writerow([today, "EUR", "USD", "1.084250", "SIMULATED_ECB"])

    faults = FaultConfig(timeout_rate=0.0, timeout_delay_seconds=0.5, slow_rate=0.0, slow_ms=0, seed=42)
    app = create_app(
        db_path=db_path,
        fx_rates_path=fx_rates_path,
        settlements_dir=settlements_dir,
        fault_config=faults,
    )
    client = TestClient(app)
    return {
        "app": app,
        "client": client,
        "db_path": db_path,
        "settlements_dir": settlements_dir,
        "fx_rates_path": fx_rates_path,
        "faults": faults,
        "today": today,
    }


def test_1_successful_usd_refund(provider_env):
    resp = provider_env["client"].post(
        "/provider/v1/refunds",
        json={
            "merchant_refund_ref": "REQ-USD-001",
            "order_id": "ORD-001",
            "customer_id": "CUST-001",
            "amount": "149.99",
            "currency": "USD",
            "idempotency_key": "idem-usd-001",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider_tx_id"].startswith("PTX-")
    assert body["merchant_refund_ref"] == "REQ-USD-001"
    assert body["status"] == "SETTLED"
    assert body["amount"] == "149.99"
    assert body["currency"] == "USD"
    assert body["usd_settled_amount"] == "149.99"
    assert body["fx_rate_applied"] == "1.000000"
    assert body["is_simulated"] is True


def test_2_successful_eur_refund(provider_env):
    resp = provider_env["client"].post(
        "/provider/v1/refunds",
        json={
            "merchant_refund_ref": "REQ-EUR-001",
            "order_id": "ORD-002",
            "customer_id": "CUST-002",
            "amount": "100.00",
            "currency": "EUR",
            "idempotency_key": "idem-eur-001",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["currency"] == "EUR"
    assert body["fx_rate_applied"] == "1.084250"
    assert body["usd_settled_amount"] == "108.43"


@pytest.mark.parametrize("bad_amount", ["0", "0.00", "-10.00", "abc", "12.345"])
def test_3_invalid_amount_rejected(provider_env, bad_amount: str):
    resp = provider_env["client"].post(
        "/provider/v1/refunds",
        json={
            "merchant_refund_ref": "REQ-BAD-AMT",
            "order_id": "ORD-003",
            "customer_id": "CUST-003",
            "amount": bad_amount,
            "currency": "USD",
        },
    )
    assert resp.status_code == 422


def test_4_unsupported_currency_rejected(provider_env):
    resp = provider_env["client"].post(
        "/provider/v1/refunds",
        json={
            "merchant_refund_ref": "REQ-BAD-CCY",
            "order_id": "ORD-004",
            "customer_id": "CUST-004",
            "amount": "25.00",
            "currency": "GBP",
        },
    )
    assert resp.status_code == 422


def test_5_provider_transaction_persisted_in_sqlite(provider_env):
    resp = provider_env["client"].post(
        "/provider/v1/refunds",
        json={
            "merchant_refund_ref": "REQ-PERSIST-001",
            "order_id": "ORD-005",
            "customer_id": "CUST-005",
            "amount": "75.50",
            "currency": "USD",
            "idempotency_key": "idem-persist-001",
        },
    )
    assert resp.status_code == 200
    ptx_id = resp.json()["provider_tx_id"]

    conn = sqlite3.connect(provider_env["db_path"])
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM provider_transactions WHERE provider_tx_id = ?",
        (ptx_id,),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row["merchant_refund_ref"] == "REQ-PERSIST-001"
    assert row["usd_settled_amount"] == "75.50"
    assert row["is_simulated"] == 1


def test_6_repeated_request_without_idempotency_key_creates_separate_operations(provider_env):
    payload = {
        "merchant_refund_ref": "REQ-NO-IDEM",
        "order_id": "ORD-006",
        "customer_id": "CUST-006",
        "amount": "40.00",
        "currency": "USD",
        "idempotency_key": None,
    }
    r1 = provider_env["client"].post("/provider/v1/refunds", json=payload)
    r2 = provider_env["client"].post("/provider/v1/refunds", json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["provider_tx_id"] != r2.json()["provider_tx_id"]


def test_7_repeated_request_with_same_idempotency_key_returns_existing(provider_env):
    payload = {
        "merchant_refund_ref": "REQ-IDEM-REPLAY",
        "order_id": "ORD-007",
        "customer_id": "CUST-007",
        "amount": "60.00",
        "currency": "USD",
        "idempotency_key": "idem-replay-007",
    }
    r1 = provider_env["client"].post("/provider/v1/refunds", json=payload)
    r2 = provider_env["client"].post("/provider/v1/refunds", json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["provider_tx_id"] == r2.json()["provider_tx_id"]


def test_8_same_idempotency_key_different_payload_returns_409(provider_env):
    r1 = provider_env["client"].post(
        "/provider/v1/refunds",
        json={
            "merchant_refund_ref": "REQ-CONFLICT-1",
            "order_id": "ORD-008",
            "customer_id": "CUST-008",
            "amount": "50.00",
            "currency": "USD",
            "idempotency_key": "idem-conflict-008",
        },
    )
    assert r1.status_code == 200

    r2 = provider_env["client"].post(
        "/provider/v1/refunds",
        json={
            "merchant_refund_ref": "REQ-CONFLICT-1",
            "order_id": "ORD-008",
            "customer_id": "CUST-008",
            "amount": "99.00",
            "currency": "USD",
            "idempotency_key": "idem-conflict-008",
        },
    )
    assert r2.status_code == 409


def test_9_concurrent_requests_same_key_create_one_transaction(provider_env):
    payload = {
        "merchant_refund_ref": "REQ-CONC-009",
        "order_id": "ORD-009",
        "customer_id": "CUST-009",
        "amount": "88.88",
        "currency": "USD",
        "idempotency_key": "idem-concurrent-009",
    }

    def send_one():
        with TestClient(provider_env["app"]) as c:
            return c.post("/provider/v1/refunds", json=payload)

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: send_one(), range(8)))

    assert all(r.status_code == 200 for r in responses)
    tx_ids = {r.json()["provider_tx_id"] for r in responses}
    assert len(tx_ids) == 1

    conn = sqlite3.connect(provider_env["db_path"])
    count = conn.execute(
        "SELECT COUNT(*) FROM provider_transactions WHERE idempotency_key = ?",
        ("idem-concurrent-009",),
    ).fetchone()[0]
    conn.close()
    assert count == 1


def test_10_post_commit_timeout_preserves_provider_transaction(provider_env):
    import socket
    import threading
    import time
    import uvicorn

    provider_env["client"].post(
        "/provider/v1/faults",
        json={"timeout_rate": 1.0, "timeout_delay_seconds": 0.5},
    )

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    config = uvicorn.Config(provider_env["app"], host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    try:
        deadline = time.monotonic() + 3.0
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)

        with pytest.raises(httpx.ReadTimeout):
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=0.1) as hc:
                hc.post(
                    "/provider/v1/refunds",
                    json={
                        "merchant_refund_ref": "REQ-TIMEOUT-010",
                        "order_id": "ORD-010",
                        "customer_id": "CUST-010",
                        "amount": "123.45",
                        "currency": "USD",
                        "idempotency_key": "idem-timeout-010",
                    },
                )
    finally:
        server.should_exit = True
        thread.join(timeout=2.0)

    conn = sqlite3.connect(provider_env["db_path"])
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM provider_transactions WHERE merchant_refund_ref = ?",
        ("REQ-TIMEOUT-010",),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row["status"] == "SETTLED"
    assert row["usd_settled_amount"] == "123.45"


def test_11_provider_lookup_returns_existing_and_never_creates(provider_env):
    created = provider_env["client"].post(
        "/provider/v1/refunds",
        json={
            "merchant_refund_ref": "REQ-LOOKUP-011",
            "order_id": "ORD-011",
            "customer_id": "CUST-011",
            "amount": "33.33",
            "currency": "USD",
            "idempotency_key": "idem-lookup-011",
        },
    ).json()

    by_id = provider_env["client"].get(f"/provider/v1/refunds/{created['provider_tx_id']}")
    assert by_id.status_code == 200
    assert by_id.json()["provider_tx_id"] == created["provider_tx_id"]

    by_idem = provider_env["client"].get("/provider/v1/refunds", params={"idempotency_key": "idem-lookup-011"})
    assert by_idem.status_code == 200
    assert len(by_idem.json()) == 1

    missing = provider_env["client"].get("/provider/v1/refunds", params={"idempotency_key": "does-not-exist"})
    assert missing.status_code == 404

    conn = sqlite3.connect(provider_env["db_path"])
    total = conn.execute("SELECT COUNT(*) FROM provider_transactions").fetchone()[0]
    conn.close()
    assert total == 1


def test_12_settlement_csv_reflects_persisted_transactions_idempotently(provider_env):
    provider_env["client"].post(
        "/provider/v1/refunds",
        json={
            "merchant_refund_ref": "REQ-SET-1",
            "order_id": "ORD-012A",
            "customer_id": "CUST-012A",
            "amount": "10.00",
            "currency": "USD",
        },
    )
    provider_env["client"].post(
        "/provider/v1/refunds",
        json={
            "merchant_refund_ref": "REQ-SET-2",
            "order_id": "ORD-012B",
            "customer_id": "CUST-012B",
            "amount": "100.00",
            "currency": "EUR",
        },
    )

    today = provider_env["today"]
    s1 = provider_env["client"].get(f"/provider/v1/settlements/{today}")
    s2 = provider_env["client"].get(f"/provider/v1/settlements/{today}")
    assert s1.status_code == 200
    assert s2.status_code == 200
    assert s1.json()["tx_count"] == 2
    assert s1.json()["total_usd"] == "118.43"
    assert s2.json()["settlement_batch_id"] == s1.json()["settlement_batch_id"]

    csv_path = Path(s1.json()["file_path"])
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert {r["merchant_refund_ref"] for r in rows} == {"REQ-SET-1", "REQ-SET-2"}


def test_13_decimal_conversion_rounding_and_missing_rate(tmp_path: Path):
    from provider_sim.fx import MissingFxRateError, convert_to_usd

    rate, usd = convert_to_usd(
        amount=Decimal("100.01"),
        currency="EUR",
        rate_date="2026-10-15",
        fx_rates={"2026-10-15": Decimal("1.084250")},
    )
    # 100.01 * 1.084250 = 108.43584250 -> ROUND_HALF_UP -> 108.44
    assert rate == Decimal("1.084250")
    assert usd == Decimal("108.44")

    with pytest.raises(MissingFxRateError):
        convert_to_usd(
            amount=Decimal("50.00"),
            currency="EUR",
            rate_date="2026-10-16",
            fx_rates={"2026-10-15": Decimal("1.084250")},
        )
