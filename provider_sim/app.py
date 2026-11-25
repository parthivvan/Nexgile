"""FastAPI application for the simulated Payment Provider."""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Mapping

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from provider_sim.db import DEFAULT_PROVIDER_DB_PATH, get_connection, init_db
from provider_sim.faults import FaultConfig
from provider_sim.fx import DEFAULT_FX_RATES_PATH, MissingFxRateError, convert_to_usd
from provider_sim.settlements import DEFAULT_SETTLEMENTS_DIR, generate_daily_settlement

SUPPORTED_CURRENCIES = {"USD", "EUR"}


class ProviderRefundRequest(BaseModel):
    merchant_refund_ref: str = Field(..., min_length=1)
    order_id: str = Field(..., min_length=1)
    customer_id: str = Field(..., min_length=1)
    amount: str = Field(..., min_length=1)
    currency: str = Field(..., min_length=1)
    idempotency_key: str | None = None


class ProviderRefundResponse(BaseModel):
    provider_tx_id: str
    merchant_refund_ref: str
    order_id: str
    customer_id: str
    status: str
    amount: str
    currency: str
    usd_settled_amount: str
    fx_rate_applied: str
    settlement_date: str
    settled_at: str
    idempotency_key: str | None = None
    is_simulated: bool = True


class FaultUpdateRequest(BaseModel):
    timeout_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    timeout_delay_seconds: float | None = Field(default=None, ge=0.0)
    slow_ms: int | None = Field(default=None, ge=0)
    slow_rate: float | None = Field(default=None, ge=0.0, le=1.0)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_positive_amount(raw_amount: str) -> Decimal:
    try:
        dec = Decimal(raw_amount.strip())
    except (InvalidOperation, AttributeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid decimal amount: {raw_amount!r}") from exc
    if not dec.is_finite() or dec <= Decimal("0"):
        raise HTTPException(status_code=422, detail="Refund amount must be a positive finite decimal")
    if dec.as_tuple().exponent < -2:
        raise HTTPException(status_code=422, detail="Refund amount cannot have more than 2 decimal places")
    return dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _row_to_response(row: sqlite3.Row) -> ProviderRefundResponse:
    return ProviderRefundResponse(
        provider_tx_id=row["provider_tx_id"],
        merchant_refund_ref=row["merchant_refund_ref"],
        order_id=row["order_id"],
        customer_id=row["customer_id"],
        status=row["status"],
        amount=row["amount"],
        currency=row["currency"],
        usd_settled_amount=row["usd_settled_amount"],
        fx_rate_applied=row["fx_rate_applied"],
        settlement_date=row["settlement_date"],
        settled_at=row["settled_at"],
        idempotency_key=row["idempotency_key"],
        is_simulated=bool(row["is_simulated"]),
    )


def _payload_matches(
    row: sqlite3.Row,
    merchant_refund_ref: str,
    order_id: str,
    customer_id: str,
    amount_str: str,
    currency: str,
) -> bool:
    return (
        row["merchant_refund_ref"] == merchant_refund_ref
        and row["order_id"] == order_id
        and row["customer_id"] == customer_id
        and row["amount"] == amount_str
        and row["currency"] == currency
    )


def create_app(
    db_path: Path | str = DEFAULT_PROVIDER_DB_PATH,
    fx_rates_path: Path | str = DEFAULT_FX_RATES_PATH,
    fx_rates_override: Mapping[str, Decimal] | None = None,
    settlements_dir: Path | str = DEFAULT_SETTLEMENTS_DIR,
    fault_config: FaultConfig | None = None,
) -> FastAPI:
    """Create and configure the Payment Provider Simulator FastAPI app."""
    db_path = Path(db_path)
    fx_rates_path = Path(fx_rates_path)
    settlements_dir = Path(settlements_dir)
    faults = fault_config if fault_config is not None else FaultConfig.from_env()

    init_db(db_path=db_path)

    app = FastAPI(title="Payment Provider Simulator", version="0.1.0")
    app.state.db_path = db_path
    app.state.faults = faults

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "component": "provider_sim"}

    @app.get("/provider/v1/faults")
    def get_faults() -> dict[str, float | int]:
        return {
            "timeout_rate": faults.timeout_rate,
            "timeout_delay_seconds": faults.timeout_delay_seconds,
            "slow_rate": faults.slow_rate,
            "slow_ms": faults.slow_ms,
        }

    @app.post("/provider/v1/faults")
    def update_faults(payload: FaultUpdateRequest) -> dict[str, float | int]:
        if payload.timeout_rate is not None:
            faults.timeout_rate = payload.timeout_rate
        if payload.timeout_delay_seconds is not None:
            faults.timeout_delay_seconds = payload.timeout_delay_seconds
        if payload.slow_ms is not None:
            faults.slow_ms = payload.slow_ms
        if payload.slow_rate is not None:
            faults.slow_rate = payload.slow_rate
        return get_faults()

    @app.post("/provider/v1/refunds", response_model=ProviderRefundResponse)
    async def create_refund(req: ProviderRefundRequest) -> ProviderRefundResponse:
        currency = req.currency.strip().upper()
        if currency not in SUPPORTED_CURRENCIES:
            raise HTTPException(status_code=422, detail=f"Unsupported currency: {req.currency}")

        amount_dec = _parse_positive_amount(req.amount)
        amount_str = f"{amount_dec:.2f}"
        settled_at = _utc_now_iso()
        settlement_date = settled_at[:10]

        try:
            fx_rate, usd_settled = convert_to_usd(
                amount=amount_dec,
                currency=currency,
                rate_date=settlement_date,
                fx_rates=fx_rates_override,
                fx_rates_path=fx_rates_path,
            )
        except MissingFxRateError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        fx_rate_str = f"{fx_rate:.6f}"
        usd_settled_str = f"{usd_settled:.2f}"
        idem_key = req.idempotency_key.strip() if req.idempotency_key else None

        conn = get_connection(db_path)
        created_row: sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if idem_key is not None:
                    existing = conn.execute(
                        "SELECT * FROM provider_transactions WHERE idempotency_key = ?",
                        (idem_key,),
                    ).fetchone()
                    if existing is not None:
                        conn.execute("COMMIT")
                        if not _payload_matches(
                            existing,
                            req.merchant_refund_ref,
                            req.order_id,
                            req.customer_id,
                            amount_str,
                            currency,
                        ):
                            raise HTTPException(
                                status_code=409,
                                detail="Idempotency key reused with a different refund payload",
                            )
                        return _row_to_response(existing)

                provider_tx_id = f"PTX-{uuid.uuid4().hex[:16].upper()}"
                conn.execute(
                    """
                    INSERT INTO provider_transactions (
                        provider_tx_id, merchant_refund_ref, order_id, customer_id,
                        amount, currency, fx_rate_applied, usd_settled_amount,
                        idempotency_key, status, settlement_date, settlement_batch_id,
                        settled_at, is_simulated
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'SETTLED', ?, NULL, ?, 1)
                    """,
                    (
                        provider_tx_id,
                        req.merchant_refund_ref,
                        req.order_id,
                        req.customer_id,
                        amount_str,
                        currency,
                        fx_rate_str,
                        usd_settled_str,
                        idem_key,
                        settlement_date,
                        settled_at,
                    ),
                )
                created_row = conn.execute(
                    "SELECT * FROM provider_transactions WHERE provider_tx_id = ?",
                    (provider_tx_id,),
                ).fetchone()
                conn.execute("COMMIT")
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK")
                if idem_key is not None:
                    existing = conn.execute(
                        "SELECT * FROM provider_transactions WHERE idempotency_key = ?",
                        (idem_key,),
                    ).fetchone()
                    if existing is not None:
                        if not _payload_matches(
                            existing,
                            req.merchant_refund_ref,
                            req.order_id,
                            req.customer_id,
                            amount_str,
                            currency,
                        ):
                            raise HTTPException(
                                status_code=409,
                                detail="Idempotency key reused with a different refund payload",
                            )
                        return _row_to_response(existing)
                raise
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        finally:
            conn.close()

        # CRITICAL: Fault injection occurs AFTER the provider transaction is committed.
        if faults.should_timeout():
            await asyncio.sleep(faults.timeout_delay_seconds)
        elif faults.should_slow():
            await asyncio.sleep(faults.slow_ms / 1000.0)

        return _row_to_response(created_row)

    @app.get("/provider/v1/refunds", response_model=list[ProviderRefundResponse])
    def lookup_refunds(
        provider_tx_id: str | None = Query(default=None),
        idempotency_key: str | None = Query(default=None),
        merchant_refund_ref: str | None = Query(default=None),
    ) -> list[ProviderRefundResponse]:
        """Read-only lookup by stable reference; never creates a new refund."""
        if not any([provider_tx_id, idempotency_key, merchant_refund_ref]):
            raise HTTPException(
                status_code=422,
                detail="Provide at least one of provider_tx_id, idempotency_key, or merchant_refund_ref",
            )
        conn = get_connection(db_path)
        try:
            if provider_tx_id:
                rows = conn.execute(
                    "SELECT * FROM provider_transactions WHERE provider_tx_id = ?",
                    (provider_tx_id,),
                ).fetchall()
            elif idempotency_key:
                rows = conn.execute(
                    "SELECT * FROM provider_transactions WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM provider_transactions WHERE merchant_refund_ref = ? ORDER BY settled_at ASC",
                    (merchant_refund_ref,),
                ).fetchall()
        finally:
            conn.close()

        if not rows:
            raise HTTPException(status_code=404, detail="Provider transaction not found")
        return [_row_to_response(row) for row in rows]

    @app.get("/provider/v1/refunds/{provider_tx_id}", response_model=ProviderRefundResponse)
    def get_refund_by_id(provider_tx_id: str) -> ProviderRefundResponse:
        """Read-only single-transaction lookup by provider_tx_id."""
        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM provider_transactions WHERE provider_tx_id = ?",
                (provider_tx_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise HTTPException(status_code=404, detail="Provider transaction not found")
        return _row_to_response(row)

    @app.get("/provider/v1/settlements/{date_str}")
    def get_daily_settlement(date_str: str) -> dict[str, object]:
        """Generate or refresh the daily settlement CSV for YYYY-MM-DD."""
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="date_str must be YYYY-MM-DD") from exc

        conn = get_connection(db_path)
        try:
            return generate_daily_settlement(conn=conn, settlement_date=date_str, settlements_dir=settlements_dir)
        finally:
            conn.close()

    return app


app = create_app()
