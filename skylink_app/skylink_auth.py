"""SkyLink API authentication and token caching.

This module owns a single in-process token store, the ``authenticate`` helper
that lazily refreshes or re-logs in, and a thin ``api_call`` wrapper that
retries on 401.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from skylink_client import SkyLinkAPIError, login, refresh_token


token_store: dict = {
    "access_token": None,
    "refresh_token": None,
    "expires_at": datetime.now(timezone.utc),
}


def _parse_expiry_seconds(payload: dict) -> int:
    data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
    return int(payload.get("expires_in") or data.get("expires_in") or 900)


def authenticate(force: bool = False) -> str:
    """Return a valid SkyLink access token, refreshing or re-logging in as needed."""
    now = datetime.now(timezone.utc)
    if (
        not force
        and token_store["access_token"]
        and now < token_store["expires_at"] - timedelta(seconds=60)
    ):
        return token_store["access_token"]

    email = os.getenv("SKYLINK_EMAIL")
    password = os.getenv("SKYLINK_PASSWORD")
    if not email or not password:
        raise RuntimeError("Missing SKYLINK_EMAIL/SKYLINK_PASSWORD in .env")

    if token_store.get("refresh_token") and not force:
        try:
            data = refresh_token(token_store["refresh_token"])
            body = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
            token_store["access_token"] = data.get("access_token") or body.get("access_token")
            token_store["refresh_token"] = (
                data.get("refresh_token")
                or body.get("refresh_token")
                or token_store["refresh_token"]
            )
            token_store["expires_at"] = datetime.now(timezone.utc) + timedelta(
                seconds=_parse_expiry_seconds(data)
            )
            return token_store["access_token"]
        except Exception:
            time.sleep(1)

    data = login(email, password)
    body = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
    token_store["access_token"] = data.get("access_token") or body.get("access_token")
    token_store["refresh_token"] = data.get("refresh_token") or body.get("refresh_token")
    token_store["expires_at"] = datetime.now(timezone.utc) + timedelta(
        seconds=_parse_expiry_seconds(data)
    )
    return token_store["access_token"]


def api_call(fn, *args, **kwargs):
    """Call ``fn(token, *args, **kwargs)``; force re-auth once on 401."""
    token = authenticate()
    try:
        return fn(token, *args, **kwargs)
    except SkyLinkAPIError as exc:
        if exc.status_code == 401:
            time.sleep(1)
            token = authenticate(force=True)
            return fn(token, *args, **kwargs)
        raise
