"""Exact Decimal FX conversion for the Payment Provider Simulator.

Rate Selection:
    - USD refunds use a fixed 1.000000 rate.
    - EUR refunds look up the EUR -> USD rate for the settlement date (YYYY-MM-DD)
      from data/fx_rates.csv (or an explicit rate mapping).

Conversion Direction:
    - Rates in fx_rates.csv represent USD per 1 EUR (base_currency='EUR', quote_currency='USD').
    - Conversion formula: usd_settled_amount = amount_eur * rate.

Rounding Method:
    - Monetary USD amounts are quantized to 2 decimal places ('0.01') using ROUND_HALF_UP.
    - FX rates are quantized to 6 decimal places ('0.000001') using ROUND_HALF_UP.

Missing-Rate Behaviour:
    - Raises MissingFxRateError explicitly; never invents rates or uses float.
"""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Mapping

DEFAULT_FX_RATES_PATH = Path("data/fx_rates.csv")
TWO_PLACES = Decimal("0.01")
SIX_PLACES = Decimal("0.000001")


class MissingFxRateError(Exception):
    """Raised when a required EUR -> USD exchange rate is not available."""


def load_fx_rates_csv(fx_rates_path: Path | str = DEFAULT_FX_RATES_PATH) -> dict[str, Decimal]:
    """Load EUR -> USD rates keyed by YYYY-MM-DD from a CSV file."""
    path = Path(fx_rates_path)
    if not path.exists():
        return {}
    rates: dict[str, Decimal] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            base = (row.get("base_currency") or "").strip().upper()
            quote = (row.get("quote_currency") or "").strip().upper()
            if base == "EUR" and quote == "USD":
                rate_date = (row.get("rate_date") or "").strip()
                raw_rate = (row.get("rate") or "").strip()
                if rate_date and raw_rate:
                    try:
                        rates[rate_date] = Decimal(raw_rate).quantize(SIX_PLACES, rounding=ROUND_HALF_UP)
                    except InvalidOperation as exc:
                        raise ValueError(f"Invalid FX rate '{raw_rate}' for {rate_date}") from exc
    return rates


def convert_to_usd(
    amount: Decimal,
    currency: str,
    rate_date: str,
    fx_rates: Mapping[str, Decimal] | None = None,
    fx_rates_path: Path | str = DEFAULT_FX_RATES_PATH,
) -> tuple[Decimal, Decimal]:
    """Convert an exact Decimal refund amount in USD or EUR to (fx_rate_applied, usd_settled_amount)."""
    normalized_currency = currency.strip().upper()
    if normalized_currency == "USD":
        rate = Decimal("1.000000")
        usd_amount = amount.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        return rate, usd_amount

    if normalized_currency == "EUR":
        rates_map = fx_rates if fx_rates is not None else load_fx_rates_csv(fx_rates_path)
        if rate_date not in rates_map:
            raise MissingFxRateError(f"Missing EUR->USD FX rate for date {rate_date}")
        rate = Decimal(rates_map[rate_date]).quantize(SIX_PLACES, rounding=ROUND_HALF_UP)
        usd_amount = (amount * rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        return rate, usd_amount

    raise ValueError(f"Unsupported currency: {currency}")
