"""Database access for bookings, payments, and session storage.

Supports both SQLite (local development) and PostgreSQL (production on Render).
The backend is selected automatically based on the ``DATABASE_URL`` environment
variable: if it starts with ``postgres`` or ``postgresql`` the PostgreSQL driver
is used, otherwise SQLite is the default.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

logger = logging.getLogger("skylink.db")

# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------
DATABASE_URL: str = os.getenv("DATABASE_URL", "")
_USE_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))

# For Render's DATABASE_URL which may use ``postgres://`` (deprecated by
# some drivers).  psycopg2 prefers ``postgresql://``.
PG_CONN_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1) if _USE_PG else ""

DB_PATH = os.getenv("SKYLINK_DB_PATH", os.path.join(os.path.dirname(__file__), "skylink.db"))

# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _get_pg_connection():
    """Return a new PostgreSQL connection using psycopg2."""
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(PG_CONN_URL)
    conn.autocommit = False
    return conn


def get_db_connection():
    """Open a database connection.

    Returns a ``sqlite3.Connection`` or a ``psycopg2.extensions.connection``
    depending on the configured backend.  Both expose a ``.execute()``
    method with ``?``-style placeholders for SQLite or ``%s``-style for PG.
    """
    if _USE_PG:
        return _get_pg_connection()
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


_PH = lambda n: ",".join(["?" for _ in range(n)]) if not _USE_PG else ",".join(["%s" for _ in range(n)])
_PH_NAMED = lambda keys: ",".join([f":{k}" for k in keys]) if _USE_PG else ",".join(["?" for _ in keys])


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_BOOKINGS_DDL_PG = """
CREATE TABLE IF NOT EXISTS bookings (
    id SERIAL PRIMARY KEY,
    invoice_id TEXT NOT NULL UNIQUE,
    booking_token TEXT,
    pnr TEXT,
    booking_status TEXT DEFAULT 'pending',
    payment_status TEXT DEFAULT 'pending',
    passenger_name TEXT,
    route TEXT,
    flight_date TEXT,
    currency TEXT DEFAULT 'NGN',
    amount_naira DOUBLE PRECISION DEFAULT 0,
    search_params_json TEXT,
    pricing_response_json TEXT,
    reserve_response_json TEXT,
    latest_status_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_PAYMENTS_DDL_PG = """
CREATE TABLE IF NOT EXISTS payments (
    id SERIAL PRIMARY KEY,
    reference TEXT NOT NULL UNIQUE,
    invoice_id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'paystack',
    email TEXT,
    amount_naira DOUBLE PRECISION DEFAULT 0,
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

_BOOKINGS_DDL_SQLITE = """
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
"""

_PAYMENTS_DDL_SQLITE = """
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


def _ensure_column_sqlite(table_name: str, column_name: str, column_definition: str) -> None:
    """Add a column to an existing SQLite table if it doesn't exist."""
    with get_db_connection() as conn:
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
        if column_name not in columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")
            conn.commit()


def _ensure_column_pg(table_name: str, column_name: str, column_definition: str) -> None:
    """Add a column to an existing PostgreSQL table if it doesn't exist."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s",
                (table_name, column_name),
            )
            if not cur.fetchone():
                cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")
                conn.commit()


def init_db() -> None:
    """Create database tables if they do not already exist."""
    if _USE_PG:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_BOOKINGS_DDL_PG)
                cur.execute(_PAYMENTS_DDL_PG)
                conn.commit()
        _ensure_column_pg("bookings", "amount_naira", "DOUBLE PRECISION DEFAULT 0")
        _ensure_column_pg("payments", "amount_naira", "DOUBLE PRECISION DEFAULT 0")
        logger.info("PostgreSQL database initialised.")
    else:
        with get_db_connection() as conn:
            conn.executescript(_BOOKINGS_DDL_SQLITE + _PAYMENTS_DDL_SQLITE)
        _ensure_column_sqlite("bookings", "amount_naira", "REAL DEFAULT 0")
        _ensure_column_sqlite("payments", "amount_naira", "REAL DEFAULT 0")
        logger.info("SQLite database initialised at %s", DB_PATH)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def json_dumps(value: Any) -> str | None:
    """JSON-encode ``value`` with safe defaults; return ``None`` for ``None``."""
    return json.dumps(value, default=str, ensure_ascii=False) if value is not None else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Generic upsert
# ---------------------------------------------------------------------------

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

    required_not_null = {"invoice_id", "reference"}
    for col in list(payload):
        if payload[col] is None and col in required_not_null:
            logger.warning("Skipping %s: %s is None in %s", table, col, data)
            return

    payload["created_at"] = _now_iso()
    payload["updated_at"] = payload["created_at"]

    # Only UPDATE columns that actually have non-None values so that partial
    # upserts don't wipe existing data to NULL.
    update_cols = [c for c in payload if c not in {conflict_col, "created_at"} and payload[c] is not None]

    if _USE_PG:
        _upsert_row_pg(table, conflict_col, payload, update_cols)
    else:
        _upsert_row_sqlite(table, conflict_col, payload, update_cols)


def _upsert_row_sqlite(table, conflict_col, payload, update_cols):
    columns_sql = ", ".join(payload.keys())
    placeholders = ",".join(["?" for _ in payload])
    updates = ", ".join(f"{col}=excluded.{col}" for col in update_cols)
    with get_db_connection() as conn:
        conn.execute(
            f"INSERT INTO {table} ({columns_sql}) VALUES ({placeholders}) "
            f"ON CONFLICT({conflict_col}) DO UPDATE SET {updates}",
            list(payload.values()),
        )
        conn.commit()


def _upsert_row_pg(table, conflict_col, payload, update_cols):
    columns = list(payload.keys())
    col_names = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(f"{col}=EXCLUDED.{col}" for col in update_cols)
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) "
                f"ON CONFLICT({conflict_col}) DO UPDATE SET {updates}",
                list(payload.values()),
            )
            conn.commit()


# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

BOOKING_COLS = (
    "booking_token", "pnr", "booking_status", "payment_status", "passenger_name",
    "route", "flight_date", "currency", "amount_naira", "search_params_json",
    "pricing_response_json", "reserve_response_json", "latest_status_json",
)

PAYMENT_COLS = (
    "invoice_id", "provider", "email", "amount_naira", "currency", "status", "access_code",
    "authorization_url", "paid_at", "raw_initialize_json", "raw_verify_json", "raw_webhook_json",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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
        if _USE_PG:
            with conn.cursor() as cur:
                cur.execute("SELECT invoice_id FROM payments WHERE reference = %s LIMIT 1", (reference,))
                row = cur.fetchone()
        else:
            row = conn.execute(
                "SELECT invoice_id FROM payments WHERE reference = ? LIMIT 1",
                (reference,),
            ).fetchone()
    if row is None:
        return None
    return row[0] if _USE_PG else row["invoice_id"]


def get_booking_by_invoice(invoice_id: str) -> dict | None:
    """Return a stored booking row as a dict, or ``None`` if missing."""
    if not invoice_id:
        return None
    with get_db_connection() as conn:
        if _USE_PG:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM bookings WHERE invoice_id = %s LIMIT 1", (invoice_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                cols = [desc[0] for desc in cur.description]
                return dict(zip(cols, row))
        else:
            row = conn.execute(
                "SELECT * FROM bookings WHERE invoice_id = ? LIMIT 1",
                (invoice_id,),
            ).fetchone()
            return dict(row) if row else None


def local_booking_status(invoice_id: str) -> dict:
    """Build booking status from local data only."""
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