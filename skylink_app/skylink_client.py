import time
from typing import Any, Dict, Optional

import httpx


BASE_URL = "https://247travels.cloud/api"
# Flight search can fan out to multiple airline/supplier systems, so it often
# needs more than a short default HTTP timeout. Keep connect/write/pool quick,
# but allow a longer read window for supplier responses.
TIMEOUT = httpx.Timeout(connect=10.0, read=90.0, write=30.0, pool=10.0)


class SkyLinkAPIError(Exception):
    def __init__(self, status_code: int, message: str, error_payload: Optional[dict] = None):
        self.status_code = status_code
        self.message = message
        self.error_payload = error_payload or {}
        super().__init__(f"SkyLink API Error {status_code}: {message} | payload={self.error_payload}")


def _raise_for_response(response: httpx.Response) -> None:
    if response.status_code == 200:
        return
    try:
        payload = response.json()
    except Exception:
        payload = {"raw": response.text}
    message = payload.get("message") or payload.get("error") or "Unknown API error"
    raise SkyLinkAPIError(response.status_code, message, payload)


def _request(method: str, endpoint: str, token: Optional[str] = None, json_data: Optional[dict] = None) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as client:
            response = client.request(method, endpoint, json=json_data, headers=headers)
    except httpx.TimeoutException as exc:
        raise SkyLinkAPIError(
            504,
            "SkyLink supplier request timed out. Please try again; live flight suppliers may be slow.",
            {
                "error": "timeout",
                "endpoint": endpoint,
                "method": method,
                "details": str(exc),
            },
        ) from exc
    except httpx.RequestError as exc:
        raise SkyLinkAPIError(
            503,
            "Unable to reach SkyLink API. Please check your connection or try again shortly.",
            {
                "error": "network_error",
                "endpoint": endpoint,
                "method": method,
                "details": str(exc),
            },
        ) from exc
    _raise_for_response(response)
    return response.json()


def login(email: str, password: str) -> dict:
    """POST /api/login — returns full response including access_token and refresh_token"""
    payload = {"email": email, "password": password}
    return _request("POST", "/login", json_data=payload)


def refresh_token(refresh_token_value: str) -> dict:
    """POST /api/token/refresh — refreshes access token when available."""
    payload = {"refresh_token": refresh_token_value}
    return _request("POST", "/token/refresh", json_data=payload)


def search_flights(token: str, search_mode: str, from_code: str, to_code: str,
                   departure_date: str, flight_type: str, adults: int,
                   children: int = 0, infants: int = 0,
                   cabin_class: str = "economy", currency: str = "NGN",
                   return_date: Optional[str] = None) -> dict:
    """POST /api/flights/search — returns flights list, each with booking_token"""
    payload: Dict[str, Any] = {
        "search_mode": search_mode,
        "from": from_code,
        "to": to_code,
        "flights_departure_date": departure_date,
        "flight_type": flight_type,
        "adults": adults,
        "children": children,
        "infants": infants,
        "class": cabin_class,
        "currency": currency,
    }
    if return_date:
        payload["flights_return_date"] = return_date
    return _request("POST", "/flights/search", token=token, json_data=payload)


def price_flight(token: str, booking_token: str, adults: int,
                 children: int = 0, infants: int = 0,
                 currency: str = "NGN", cabin_class: str = "economy") -> dict:
    """POST /api/flights/pricing — ALWAYS call before reserve.
       Returns updated booking_token — use THIS token for reserve, not the search token."""
    payload = {
        "booking_token": booking_token,
        "adults": adults,
        "children": children,
        "infants": infants,
        "currency": currency,
        "cabin_class": cabin_class,
    }
    return _request("POST", "/flights/pricing", token=token, json_data=payload)


def reserve_flight(token: str, booking_token: str, travellers: dict,
                   passengers: dict, currency: str = "NGN",
                   ticket_time_limit_hours: int = 48,
                   bypass_mode: bool = False) -> dict:
    """POST /api/flights/reserve — returns payment_url, invoice_id, payment_reference"""
    payload = {
        "booking_token": booking_token,
        "travellers": travellers,
        "passengers": passengers,
        "currency": currency,
        "ticket_time_limit_hours": ticket_time_limit_hours,
    }
    if bypass_mode:
        # Optional hints for sandbox/admin bypass flows (ignored if unsupported)
        payload.update({
            "test_bypass_mode": True,
            "bypass_payment": True,
            "sandbox_success": True,
        })
    return _request("POST", "/flights/reserve", token=token, json_data=payload)


def call_with_retry(callable_fn, *args, **kwargs):
    """Retry once after 401 by sleeping 1 second (refresh should be handled by caller)."""
    try:
        return callable_fn(*args, **kwargs)
    except SkyLinkAPIError:
        time.sleep(1)
        raise
