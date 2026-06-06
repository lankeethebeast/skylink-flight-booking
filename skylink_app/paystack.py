"""Paystack HTTP client for transaction initialize, verify, and webhook handling."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any, Mapping, Optional

import requests

logger = logging.getLogger("skylink.paystack")

PAYSTACK_PUBLIC_KEY = os.getenv("PAYSTACK_PUBLIC_KEY", "")
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")
PAYSTACK_API_BASE = "https://api.paystack.co"

TIMEOUT = 10  # seconds


def is_configured() -> bool:
    """Return True when a real (non-placeholder) Paystack secret key is set."""
    return bool(PAYSTACK_SECRET_KEY) and not PAYSTACK_SECRET_KEY.startswith("sk_test_YOUR")


def is_public_key_configured() -> bool:
    """Return True when a real (non-placeholder) Paystack public key is set."""
    return bool(PAYSTACK_PUBLIC_KEY) and not PAYSTACK_PUBLIC_KEY.startswith("pk_test_YOUR")


def initialize_transaction(email: str, amount_kobo: int, reference: str,
                           callback_url: str, metadata: Optional[dict] = None) -> dict:
    """POST /transaction/initialize — returns the parsed JSON response."""
    payload: dict[str, Any] = {
        "email": email,
        "amount": amount_kobo,  # smallest unit (kobo for NGN)
        "reference": reference,
        "callback_url": callback_url,
        "metadata": metadata or {},
    }
    response = requests.post(
        f"{PAYSTACK_API_BASE}/transaction/initialize",
        headers={
            "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def verify_transaction(reference: str) -> dict:
    """GET /transaction/verify/{reference} — returns the parsed JSON response."""
    response = requests.get(
        f"{PAYSTACK_API_BASE}/transaction/verify/{reference}",
        headers={"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def verify_webhook_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    """Constant-time HMAC-SHA512 verification of a Paystack webhook payload."""
    if not signature or not PAYSTACK_SECRET_KEY:
        return False
    expected = hmac.new(
        PAYSTACK_SECRET_KEY.encode(),
        raw_body,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
