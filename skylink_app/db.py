"""SQLite database access for bookings, payments, and session storage."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

logger = logging.getLogger("skylink.db")

DB_PATH = os.getenv("SKYLINK_DB_PATH", os.path.join(os.path.dirname(__file__), "skylink.db"))


def get_db_connection() -> sqlite3.Connection:
    """Open a SQLite connection for persistent bookings/payments storage."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_definition: str) -> None:
    """Add a SQLite column when upgrading an existing local database."""
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")


def init_db() -> None:
    """Create database tables if they do not already exist."""
    with get_db_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id TEXT NOT NULL UNIQUE,
                booking_token TEXT,
                pnr TEXT,
                booking_status TEXT DEFAULT 'pending',
                payment_status TEXT DEFAULT 'pending',
                passenger_name TEXT,
                route TEXT,
                flight_date TEXT,
                currency TEXT DEFAULT 'NGN',
                amount_naira REAL DEFAULT 0,
                search_params_json TEXT,
                pricing_response_json TEXT,
                reserve_response_json TEXT,
                latest_status_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reference TEXT NOT NULL UNIQUE,
                invoice_id TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'paystack',
                email TEXT,
                amount_naira REAL DEFAULT 0,
                currency TEXT DEFAULT 'NGN',
                status TEXT DEFAULT 'initialized',
                access_code TEXT,
                authorization_url TEXT,
                paid_at TEXT,
                raw_initialize_json TEXT,
                raw_verify_json TEXT,
                raw_webhook_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(invoice_id) REFERENCES bookings(invoice_id)
            );
            """
        )
        _ensure_column(conn, "bookings", "amount_naira", "REAL DEFAULT 0")
        _ensure_column(conn, "payments", "amount_naira", "REAL DEFAULT 0")


def json_dumps(value: Any) -> str | None:
    """JSON-encode ``value`` with safe defaults; return ``None`` for ``None``."""
    return json.dumps(value, default=str, ensure_ascii=False) if value is not None else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upsert_row(
    table: str,
    conflict_col: str,
    data: Mapping[str, Any],
    allowed_cols: Iterable[str],
) -> None:
    """Insert or update a single row by ``conflict_col``.

    ``created_at`` is set to "now" on insert and preserved on update.
    """
    cols = [conflict_col] + [c for c in allowed_cols if c != conflict_col]
    if not data.get(conflict_col):
        return
    payload = {c: data.get(c) for c in cols}
    # Guard against NOT NULL constraint violations: drop columns whose
    # values are None so the database uses its DEFAULT instead.
    required_not_null = {"invoice_id", "reference"}
    for col in list(payload):
        if payload[col] is None and col in required_not_null:
            logger.warning("Skipping %s: %s is None in %s", table, col, data)
            return
    payload["created_at"] = _now_iso()
    payload["updated_at"] = payload["created_at"]
    placeholders = ", ".join("?" for _ in payload)
    # Only UPDATE columns that actually have non-None values so that partial
    # upserts (e.g. upsert_booking(invoice, payment_status="paid")) don't
    # wipe existing data to NULL.
    updates = ", ".join(
        f"{col}=excluded.{col}"
        for col in payload
        if col not in {conflict_col, "created_at"} and payload[col] is not None
    )
    columns_sql = ", ".join(payload.keys())
    with get_db_connection() as conn:
        conn.execute(
            f"""
            INSERT INTO {table} ({columns_sql})
            VALUES ({placeholders})
            ON CONFLICT({conflict_col}) DO UPDATE SET {updates}
            """,
            list(payload.values()),
        )


# Columns actually present in the ``bookings`` table (matches init_db).
BOOKING_COLS = (
    "booking_token", "pnr", "booking_status", "payment_status", "passenger_name",
    "route", "flight_date", "currency", "amount_naira", "search_params_json",
    "pricing_response_json", "reserve_response_json", "latest_status_json",
)

# Columns actually present in the ``payments`` table.
PAYMENT_COLS = (
    "invoice_id", "provider", "email", "amount_naira", "currency", "status", "access_code",
    "authorization_url", "paid_at", "raw_initialize_json", "raw_verify_json", "raw_webhook_json",
)


def upsert_booking(invoice_id: str, **fields: Any) -> None:
    """Insert or update a booking by ``invoice_id``."""
    if not invoice_id:
        return
    payload = {k: v for k, v in fields.items() if k in BOOKING_COLS}
    payload["invoice_id"] = invoice_id
    _upsert_row("bookings", "invoice_id", payload, BOOKING_COLS)


def upsert_payment(reference: str, invoice_id: str, **fields: Any) -> None:
    """Insert or update a Paystack payment by ``reference``."""
    if not reference or not invoice_id:
        return
    payload = {k: v for k, v in fields.items() if k in PAYMENT_COLS}
    payload.setdefault("provider", "paystack")
    payload.setdefault("status", "initialized")
    payload["reference"] = reference
    payload["invoice_id"] = invoice_id
    _upsert_row("payments", "reference", payload, PAYMENT_COLS)


def get_invoice_id_for_reference(reference: str) -> str | None:
    """Find an invoice_id from a stored payment reference."""
    if not reference:
        return None
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT invoice_id FROM payments WHERE reference = ? LIMIT 1",
            (reference,),
        ).fetchone()
    return row["invoice_id"] if row else None


def get_booking_by_invoice(invoice_id: str) -> dict | None:
    """Return a stored booking row as a dict, or ``None`` if missing."""
    if not invoice_id:
        return None
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM bookings WHERE invoice_id = ? LIMIT 1",
            (invoice_id,),
        ).fetchone()
    return dict(row) if row else None


def local_booking_status(invoice_id: str) -> dict:
    """Build booking status from local data only.

    SkyLink does not expose /api/flights/booking/status/{invoice_id}, so the
    app must not call that external URL. Until the reserve response itself
    includes a PNR, status is based on the local booking/payment records.
    """
    booking = get_booking_by_invoice(invoice_id)
    if not booking:
        return {
            "has_pnr": False,
            "pnr": None,
            "booking_status": "unknown",
            "payment_status": "unknown",
            "invoice_id": invoice_id,
            "message": "Booking not found locally.",
        }

    return {
        "has_pnr": bool(booking.get("pnr")),
        "pnr": booking.get("pnr"),
        "booking_status": booking.get("booking_status") or "reserved",
        "payment_status": booking.get("payment_status") or "pending",
        "invoice_id": booking.get("invoice_id"),
        "passenger_name": booking.get("passenger_name"),
        "route": booking.get("route"),
        "flight_date": booking.get("flight_date"),
        "message": "Status is from local booking records; no external booking-status API is configured.",
    }
