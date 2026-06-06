"""Invoice display, kobo/naira conversion, and total amount helpers."""
from __future__ import annotations

import re
from typing import Any, Mapping


def kobo_to_naira(amount_kobo) -> float:
    """Convert Paystack kobo amount to naira for database storage.

    Defensive against non-numeric / ``None`` inputs.
    """
    try:
        return round(float(amount_kobo or 0) / 100, 2)
    except (TypeError, ValueError):
        return 0.0


def display_invoice_id(value) -> str:
    """Display provider invoice IDs as a short customer-facing reference.

    Example: ``API-AMADEUS-20260604144904-c86583`` -> ``#20260604144904``.
    The original provider invoice remains stored internally for lookups.
    """
    if not value:
        return "N/A"
    match = re.search(r"(\d{14})", str(value))
    return f"#{match.group(1)}" if match else f"#{value}"


def calculate_total_amount(pricing_response: Mapping[str, Any]) -> int:
    """Extract total amount in smallest currency unit (kobo for NGN, cents for others).

    Handles various price field names from the SkyLink pricing API response.
    Returns ``0`` if no value can be parsed.
    """
    if not pricing_response:
        return 0
    data = pricing_response.get("data", {}) if isinstance(pricing_response.get("data"), dict) else {}
    total = (
        pricing_response.get("total_price")
        or data.get("total_price")
        or data.get("verified_price")
        or data.get("original_price")
        or data.get("price")
        or pricing_response.get("total")
        or data.get("total")
    )
    if total:
        try:
            # Convert major-unit amount (e.g. naira) to minor-unit (e.g. kobo).
            return int(float(total) * 100)
        except (ValueError, TypeError):
            return 0
    return 0
