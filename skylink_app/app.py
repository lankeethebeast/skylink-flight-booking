"""Flask application factory and HTTP routes for the SkyLink flight booking site."""
import logging
import os
import time
import traceback
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import (
    Flask, flash, jsonify, redirect, render_template, request, session, url_for,
)
from flask_session import Session
import requests

from db import (
    init_db,
    json_dumps,
    upsert_booking,
    upsert_payment,
    get_invoice_id_for_reference,
    local_booking_status,
)
from decorators import handle_api_errors
from flight_utils import display_date, enrich_segments_with_layovers
from invoice_utils import (
    calculate_total_amount,
    display_invoice_id,
    kobo_to_naira,
)
from paystack import (
    PAYSTACK_PUBLIC_KEY,
    is_configured as paystack_is_configured,
    is_public_key_configured as paystack_is_public_key_configured,
    initialize_transaction,
    verify_transaction,
    verify_webhook_signature,
)
from skylink_auth import api_call
from skylink_client import (
    price_flight,
    reserve_flight,
    search_flights,
)

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# ── Logging configuration ─────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("skylink")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

TEST_BYPASS_MODE = os.getenv("TEST_BYPASS_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
SHOW_DEBUG_PANEL = os.getenv("SHOW_DEBUG_PANEL", "true").strip().lower() in {"1", "true", "yes", "on"}
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "true").strip().lower() in {"1", "true", "yes", "on"}

# Server-side session configuration.
# Use PostgreSQL-backed sessions when DATABASE_URL is set (production),
# otherwise fall back to filesystem sessions (local development).
from db import _USE_PG, _active_pg_uri, PG_DBNAME
from flask_sqlalchemy import SQLAlchemy

if _USE_PG:
    pg_uri = _active_pg_uri()
    # Log a redacted view of the URI so deploy issues are easier to debug.
    try:
        from urllib.parse import urlparse
        parsed = urlparse(pg_uri)
        logger.info("PostgreSQL session URI: scheme=%s host=%s port=%s db=%s",
                    parsed.scheme, parsed.hostname, parsed.port,
                    parsed.path.lstrip("/"))
        if PG_DBNAME:
            logger.info("PG_DBNAME override in effect -> %s", PG_DBNAME)
    except Exception:
        pass
    app.config["SQLALCHEMY_DATABASE_URI"] = pg_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    _session_db = SQLAlchemy(app)
    app.config["SESSION_TYPE"] = "sqlalchemy"
    app.config["SESSION_SQLALCHEMY"] = _session_db
    app.config["SESSION_SQLALCHEMY_TABLE"] = "flask_sessions"
    app.config["SESSION_PERMANENT"] = False
else:
    app.config["SESSION_TYPE"] = "filesystem"
    app.config["SESSION_FILE_DIR"] = os.path.join(os.path.dirname(__file__), "flask_session")
    app.config["SESSION_PERMANENT"] = False
Session(app)

# Template filters
app.add_template_filter(display_invoice_id, "display_invoice_id")
app.add_template_filter(display_date, "display_date")

init_db()


# ── Global error handlers ─────────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(_e):
    return render_template(
        "error.html",
        show_debug=FLASK_DEBUG,
        error_type="NotFound",
        error_message="The page you requested was not found.",
        error_traceback="",
        request_path=request.path,
        request_method=request.method,
    ), 404


@app.errorhandler(500)
def internal_error(_e):
    tb = traceback.format_exc()
    logger.error("Unhandled 500: %s", tb)
    return render_template(
        "error.html",
        show_debug=FLASK_DEBUG,
        error_type="InternalServerError",
        error_message="An unexpected server error occurred.",
        error_traceback=tb,
        request_path=request.path,
        request_method=request.method,
    ), 500


# ── Helpers ───────────────────────────────────────────────────────────────────
def _normalize_pricing_response(pricing: dict) -> dict:
    """Copy class/stops fields from the cached flight into pricing data."""
    pricing_body = pricing.get("data", {}) if isinstance(pricing.get("data"), dict) else {}
    if not pricing_body:
        return pricing
    booking_token = pricing.get("booking_token") or pricing_body.get("booking_token")
    flights = session.get("flight_results", []) or []
    selected = next(
        (f for f in flights if f.get("booking_token") == booking_token),
        {},
    ) if booking_token else {}
    for key in ("class_letter", "booking_class", "class_code", "fare_class"):
        if pricing_body.get(key) is None and selected.get(key) is not None:
            pricing_body[key] = selected.get(key)
    for key in ("stops", "number_of_stops", "stop_count"):
        if pricing_body.get(key) is None and selected.get(key) is not None:
            pricing_body[key] = selected.get(key)
    pricing["data"] = pricing_body
    return pricing


def _person_from_form(prefix: str) -> dict:
    fields = [
        "title", "first_name", "last_name", "other_name", "email", "phone",
        "country_code", "dob", "gender", "nationality",
    ]
    return {f: request.form.get(f"{prefix}_{f}", "") for f in fields}


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/healthz")
def healthz():
    """Lightweight health check — no external API calls."""
    return jsonify({
        "status": "ok",
        "test_bypass_mode": TEST_BYPASS_MODE,
        "show_debug_panel": SHOW_DEBUG_PANEL,
        "flask_debug": FLASK_DEBUG,
    })


@app.route("/")
def index():
    return render_template("index.html")


# ── Search ────────────────────────────────────────────────────────────────────
@app.route("/search", methods=["POST"])
@handle_api_errors
def search():
    form = request.form
    adults = int(form.get("adults", 1))
    children = int(form.get("children", 0))
    infants = int(form.get("infants", 0))
    if adults < 1 or infants > adults:
        flash("Adults must be at least 1 and infants cannot exceed adults.", "error")
        return redirect(url_for("index"))

    trip_type = form.get("trip_type", "oneway")
    cabin_class = form.get("cabin_class", "economy")

    if trip_type == "multicity":
        return _search_multicity(form, adults, children, infants, cabin_class)

    return _search_single(form, adults, children, infants, cabin_class, trip_type)


def _search_multicity(form, adults, children, infants, cabin_class):
    """Multi-city search: each leg is a separate SkyLink request."""
    leg_from = [v.strip().upper() for v in form.getlist("leg_from")]
    leg_to = [v.strip().upper() for v in form.getlist("leg_to")]
    leg_date = [v.strip() for v in form.getlist("leg_date")]

    legs = []
    for i, (f_code, t_code, d) in enumerate(zip(leg_from, leg_to, leg_date)):
        if not (f_code and t_code and d):
            continue
        if len(f_code) != 3 or len(t_code) != 3:
            flash(f"Leg {i + 1}: airport codes must be 3 letters.", "error")
            return redirect(url_for("index"))
        if f_code == t_code:
            flash(f"Leg {i + 1}: origin and destination cannot match.", "error")
            return redirect(url_for("index"))
        legs.append({"index": i, "from_code": f_code, "to_code": t_code, "departure_date": d})

    if len(legs) < 2:
        flash("Multi-city requires at least 2 legs.", "error")
        return redirect(url_for("index"))
    if len(legs) > 5:
        flash("Multi-city supports a maximum of 5 legs.", "error")
        return redirect(url_for("index"))

    for i in range(1, len(legs)):
        if legs[i]["departure_date"] < legs[i - 1]["departure_date"]:
            flash(
                f"Leg {i + 1} departure date must be on or after leg {i}.",
                "error",
            )
            return redirect(url_for("index"))

    search_params = {
        "search_mode": "external",
        "flight_type": "multicity",
        "legs": legs,
        "adults": adults,
        "children": children,
        "infants": infants,
        "cabin_class": cabin_class,
        "currency": "NGN",
        "from_code": legs[0]["from_code"],
        "to_code": legs[-1]["to_code"],
        "departure_date": legs[0]["departure_date"],
    }

    leg_results = []
    raw_leg_responses = []
    for leg in legs:
        data = api_call(
            search_flights,
            search_mode="external",
            from_code=leg["from_code"],
            to_code=leg["to_code"],
            departure_date=leg["departure_date"],
            flight_type="oneway",
            adults=adults,
            children=children,
            infants=infants,
            cabin_class=cabin_class,
            currency="NGN",
        )
        body = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
        flights = body.get("flights", data.get("flights", []))
        leg_flights = flights if isinstance(flights, list) else []
        enrich_segments_with_layovers(leg_flights)
        leg_results.append({
            "index": leg["index"],
            "from_code": leg["from_code"],
            "to_code": leg["to_code"],
            "departure_date": leg["departure_date"],
            "flights": leg_flights,
        })
        raw_leg_responses.append(data)

    session["search_params"] = search_params
    session["multicity_results"] = leg_results
    session["flight_results"] = []
    session["multicity_selected"] = {}

    total = sum(len(lr["flights"]) for lr in leg_results)
    if total == 0:
        flash(
            "No flights found for any leg of your multi-city itinerary. "
            "Try different dates or routes.",
            "warning",
        )

    session["results_generated_at_ms"] = int(time.time() * 1000)
    return render_template(
        "results.html",
        flights=[],
        leg_results=leg_results,
        search_params=search_params,
        is_multicity=True,
        results_generated_at_ms=session["results_generated_at_ms"],
        show_debug_panel=SHOW_DEBUG_PANEL,
        raw_leg_responses=raw_leg_responses,
    )


def _search_single(form, adults, children, infants, cabin_class, trip_type):
    """One-way / round-trip search."""
    search_params = {
        "search_mode": "external",
        "from_code": form.get("from_code", "").upper(),
        "to_code": form.get("to_code", "").upper(),
        "departure_date": form.get("departure_date"),
        "return_date": form.get("return_date") or None,
        "flight_type": trip_type,
        "adults": adults,
        "children": children,
        "infants": infants,
        "cabin_class": cabin_class,
        "currency": "NGN",
    }
    data = api_call(search_flights, **search_params)
    session["search_params"] = search_params
    body = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
    flights = body.get("flights", data.get("flights", []))
    flights_list = flights if isinstance(flights, list) else []
    enrich_segments_with_layovers(flights_list)
    session["flight_results"] = flights_list
    session.pop("multicity_results", None)
    session.pop("multicity_selected", None)

    session["raw_search_response"] = data

    if not flights_list:
        flash("No flights found for this route/date. Try another date or route.", "warning")
    session["results_generated_at_ms"] = int(time.time() * 1000)
    return render_template(
        "results.html",
        flights=flights_list,
        search_params=search_params,
        is_multicity=False,
        results_generated_at_ms=session["results_generated_at_ms"],
        show_debug_panel=SHOW_DEBUG_PANEL,
        raw_search_response=data,
    )


# ── Pricing ───────────────────────────────────────────────────────────────────
@app.route("/price", methods=["POST"])
@handle_api_errors
def price():
    booking_token = request.form.get("booking_token")
    session["selected_token"] = booking_token
    params = session.get("search_params", {})
    pricing = api_call(
        price_flight,
        booking_token=booking_token,
        adults=params.get("adults", 1),
        children=params.get("children", 0),
        infants=params.get("infants", 0),
        currency=params.get("currency", "NGN"),
        cabin_class=params.get("cabin_class", "economy"),
    )
    session["pricing_response"] = _normalize_pricing_response(pricing)
    return redirect(url_for("passengers"))


# ── Multi-city ────────────────────────────────────────────────────────────────
@app.route("/multicity/select", methods=["POST"])
@handle_api_errors
def multicity_select():
    """Record a flight selection for one leg of a multi-city itinerary."""
    params = session.get("search_params", {})
    if params.get("flight_type") != "multicity":
        flash("No active multi-city search.", "error")
        return redirect(url_for("index"))

    leg_index = request.form.get("leg_index")
    booking_token = request.form.get("booking_token")
    if leg_index is None or not booking_token:
        flash("Invalid selection.", "error")
        return redirect(url_for("multicity_results_view"))

    selections = session.get("multicity_selected", {}) or {}
    selections[str(leg_index)] = booking_token
    session["multicity_selected"] = selections

    legs = params.get("legs", [])
    total_legs = len(legs)

    if len(selections) < total_legs or total_legs == 0:
        return redirect(url_for("multicity_results_view"))

    leg_pricings = []
    total_price = 0.0
    currency = params.get("currency", "NGN")
    for leg in legs:
        tok = selections.get(str(leg["index"]))
        if not tok:
            continue
        pricing = api_call(
            price_flight,
            booking_token=tok,
            adults=params.get("adults", 1),
            children=params.get("children", 0),
            infants=params.get("infants", 0),
            currency=currency,
            cabin_class=params.get("cabin_class", "economy"),
        )
        body = pricing.get("data", {}) if isinstance(pricing.get("data"), dict) else pricing
        leg_total = float(body.get("total_price") or body.get("price") or 0)
        total_price += leg_total
        leg_pricings.append({"leg": leg, "pricing": pricing, "total": leg_total})

    canonical = leg_pricings[-1]["pricing"] if leg_pricings else {}
    if isinstance(canonical, dict):
        canonical = dict(canonical)
        data_obj = canonical.get("data") if isinstance(canonical.get("data"), dict) else None
        if data_obj is not None:
            data_obj = dict(data_obj)
            data_obj["total_price"] = total_price
            data_obj["currency"] = currency
            canonical["data"] = data_obj
        else:
            canonical["total_price"] = total_price
            canonical["currency"] = currency
        canonical["_multicity_legs"] = [
            {
                "from_code": lp["leg"]["from_code"],
                "to_code": lp["leg"]["to_code"],
                "departure_date": lp["leg"]["departure_date"],
                "total": lp["total"],
                "currency": currency,
            }
            for lp in leg_pricings
        ]

    session["pricing_response"] = canonical
    return redirect(url_for("passengers"))


@app.route("/multicity/results")
def multicity_results_view():
    params = session.get("search_params", {})
    leg_results = session.get("multicity_results", [])
    if params.get("flight_type") != "multicity" or not leg_results:
        return redirect(url_for("index"))
    return render_template(
        "results.html",
        flights=[],
        leg_results=leg_results,
        search_params=params,
        is_multicity=True,
    )


@app.route("/multicity/reset", methods=["POST"])
def multicity_reset():
    session["multicity_selected"] = {}
    return redirect(url_for("multicity_results_view"))


@app.route("/passengers")
def passengers():
    pricing = session.get("pricing_response", {})
    params = session.get("search_params", {})
    if not pricing:
        return redirect(url_for("index"))
    return render_template("review.html", pricing=pricing, search_params=params)


# ── Reserve ───────────────────────────────────────────────────────────────────
@app.route("/reserve", methods=["POST"])
@handle_api_errors
def reserve():
    params = session.get("search_params", {})
    pricing = session.get("pricing_response", {})
    booking_token = pricing.get("booking_token") or pricing.get("data", {}).get("booking_token")

    adults = params.get("adults", 1)
    children = params.get("children", 0)
    infants = params.get("infants", 0)

    primary = _person_from_form("adult_0")
    travelers = {"adult_0": dict(primary)}
    for i in range(1, adults):
        travelers[f"adult_{i}"] = _person_from_form(f"adult_{i}")
    for i in range(children):
        travelers[f"child_{i}"] = _person_from_form(f"child_{i}")
    for i in range(infants):
        travelers[f"infant_{i}"] = _person_from_form(f"infant_{i}")

    travellers = {"primary_guest": primary, "travelers": travelers}
    passengers = {"adults": adults, "children": children, "infants": infants}

    result = api_call(
        reserve_flight,
        booking_token=booking_token,
        travellers=travellers,
        passengers=passengers,
        currency=params.get("currency", "NGN"),
        ticket_time_limit_hours=48,
        bypass_mode=TEST_BYPASS_MODE,
    )

    # Extract invoice_id from response or generate one if missing.
    invoice_id = result.get("invoice_id") or result.get("data", {}).get("invoice_id")
    if not invoice_id:
        invoice_id = f"INV-{uuid.uuid4().hex[:12].upper()}"

    session["invoice_id"] = invoice_id
    session["payment_url"] = result.get("payment_url") or result.get("data", {}).get("payment_url")
    session["reserve_response"] = result

    reserve_data = result.get("data", {}) if isinstance(result.get("data"), dict) else result
    # NOTE: calculate_total_amount() already returns kobo; we store naira.
    # The previous code double-converted (kobo -> naira by /100) which inflated
    # the recorded amount by a factor of 100. Pass the kobo amount directly.
    amount_kobo = calculate_total_amount(pricing)
    amount_naira = kobo_to_naira(amount_kobo) if amount_kobo else 0.0
    booking_status = reserve_data.get("booking_status") or reserve_data.get("status") or "reserved"
    # Always start as "pending" for the Paystack flow; only become "paid" after
    # Paystack verification.  The provider's own payment_status is not used here
    # because the user must complete the Paystack checkout before PNR is revealed.
    payment_status = "paid" if TEST_BYPASS_MODE else "pending"
    if reserve_data.get("pnr"):
        session["pnr_data"] = {
            "pnr": reserve_data.get("pnr"),
            "has_pnr": True,
            "booking_status": booking_status or "confirmed",
            "payment_status": payment_status,
            "passenger_name": reserve_data.get("passenger_name"),
            "route": reserve_data.get("route"),
            "flight_date": reserve_data.get("flight_date"),
        }

    upsert_booking(
        invoice_id,
        booking_token=booking_token,
        pnr=reserve_data.get("pnr"),
        booking_status=booking_status,
        payment_status=payment_status,
        passenger_name=reserve_data.get("passenger_name")
            or f"{primary.get('first_name', '')} {primary.get('last_name', '')}".strip(),
        route=reserve_data.get("route") or f"{params.get('from_code', '')}-{params.get('to_code', '')}",
        flight_date=reserve_data.get("flight_date") or params.get("departure_date"),
        currency=params.get("currency", "NGN"),
        amount_naira=amount_naira,
        search_params_json=json_dumps(params),
        pricing_response_json=json_dumps(pricing),
        reserve_response_json=json_dumps(result),
    )

    invoice_id = session.get("invoice_id")
    latest_status = local_booking_status(invoice_id)
    session["latest_status_response"] = latest_status

    return redirect(url_for("confirm"))


# ── Status & Confirm ──────────────────────────────────────────────────────────
@app.route("/status")
@handle_api_errors
def status():
    invoice_id = request.args.get("invoice_id") or session.get("invoice_id")
    status_data = local_booking_status(invoice_id)

    # Only expose PNR data after payment is confirmed as paid
    if status_data.get("has_pnr") and (
        session.get("payment_status") == "paid"
        or status_data.get("payment_status") == "paid"
    ):
        session["pnr_data"] = status_data
    session["latest_status_response"] = status_data
    if invoice_id and status_data.get("booking_status") != "unknown":
        upsert_booking(invoice_id, latest_status_json=json_dumps(status_data))

    # Redact PNR from the response if payment is not yet confirmed as paid.
    # Only check the DATABASE payment_status (not session) to avoid stale
    # "paid" from a previous booking leaking the PNR of the current one.
    is_paid = status_data.get("payment_status") == "paid"
    if not is_paid and status_data.get("has_pnr"):
        status_data["has_pnr"] = False
        status_data["pnr"] = None

    if status_data.get("booking_status") == "error" or status_data.get("error"):
        return jsonify(status_data), 400
    return jsonify(status_data)


@app.route("/confirm")
def confirm():
    invoice_id = session.get("invoice_id")
    local_status = local_booking_status(invoice_id)
    payment_status = session.get("payment_status") or local_status.get("payment_status")
    if payment_status:
        session["payment_status"] = payment_status
    session["latest_status_response"] = local_status
    # Always use the DATABASE payment_status for the current booking — never
    # trust the session, which may hold "paid" from a previous booking.
    payment_status = local_status.get("payment_status") or "pending"
    session["payment_status"] = payment_status

    # Only expose PNR when payment is confirmed as paid
    pnr_data = session.get("pnr_data") if payment_status == "paid" else None

    # Redact PNR from debug panel data when payment is not paid
    display_status = dict(local_status)
    if payment_status != "paid" and display_status.get("has_pnr"):
        display_status["has_pnr"] = False
        display_status["pnr"] = None

    return render_template(
        "confirmation.html",
        pnr_data=pnr_data,
        invoice_id=invoice_id,
        display_invoice_id=display_invoice_id(invoice_id),
        payment_url=session.get("payment_url"),
        test_bypass_mode=TEST_BYPASS_MODE,
        show_debug_panel=SHOW_DEBUG_PANEL,
        reserve_response=session.get("reserve_response"),
        latest_status_response=display_status,
        payment_status=payment_status,
        paystack_public_key=PAYSTACK_PUBLIC_KEY,
        paystack_is_public_key_configured=paystack_is_public_key_configured,
        payment_amount=session.get("payment_amount"),
    )


@app.route("/confirmation")
def confirmation_legacy():
    return redirect(url_for("confirm"))


# ── Paystack Payment Integration ──────────────────────────────────────────────
@app.route("/payment/paystack", methods=["POST"])
def paystack_payment():
    """Initialize a Paystack transaction for the current booking.

    This endpoint intentionally does NOT use the ``@handle_api_errors``
    decorator so that it always returns JSON — the front-end fetch() call
    needs a parseable JSON body on every code path.
    """
    try:
        return _paystack_payment_inner()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in paystack_payment")
        return jsonify({"error": f"Server error: {exc}"}), 500


def _paystack_payment_inner():
    """Core logic separated so the outer wrapper can catch everything."""
    if not paystack_is_configured():
        return jsonify({
            "error": "Paystack not configured. Please set PAYSTACK_SECRET_KEY in your Render environment variables."
        }), 500

    email = request.form.get("email") or session.get("email") or request.form.get("adult_0_email")
    if not email:
        return jsonify({"error": "Email address is required for payment"}), 400

    pricing = session.get("pricing_response", {})
    amount_kobo = calculate_total_amount(pricing)
    if not amount_kobo:
        return jsonify({
            "error": "Unable to determine payment amount. Please go back and try again."
        }), 400

    invoice_id = session.get("invoice_id") or f"INV-{uuid.uuid4().hex[:12].upper()}"
    session["invoice_id"] = invoice_id
    currency = pricing.get("currency") or pricing.get("data", {}).get("currency") or "NGN"
    upsert_booking(
        invoice_id,
        booking_token=pricing.get("booking_token") or pricing.get("data", {}).get("booking_token"),
        payment_status="pending",
        currency=currency,
        amount_naira=kobo_to_naira(amount_kobo),
        pricing_response_json=json_dumps(pricing),
    )

    metadata = {
        "invoice_id": invoice_id,
        "booking_token": pricing.get("booking_token")
            or pricing.get("data", {}).get("booking_token"),
        "custom_fields": [
            {
                "display_name": "Invoice ID",
                "variable_name": "invoice_id",
                "value": invoice_id,
            }
        ],
    }
    pnr_data = session.get("pnr_data", {})
    if pnr_data and pnr_data.get("passenger_name"):
        metadata["passenger_name"] = pnr_data["passenger_name"]

    callback_url = url_for("payment_callback", _external=True)

    try:
        data = initialize_transaction(
            email=email,
            amount_kobo=amount_kobo,
            reference=invoice_id,
            callback_url=callback_url,
            metadata=metadata,
        )
    except requests.RequestException as exc:
        logger.error("Paystack initialize error: %s", exc)
        return jsonify({"error": f"Payment service error: {exc}"}), 500

    if data.get("status"):
        session["paystack_access_code"] = data["data"]["access_code"]
        session["paystack_reference"] = data["data"]["reference"]
        upsert_payment(
            data["data"]["reference"],
            invoice_id,
            email=email,
            amount_naira=kobo_to_naira(amount_kobo),
            currency=currency,
            status="initialized",
            access_code=data["data"].get("access_code"),
            authorization_url=data["data"].get("authorization_url"),
            raw_initialize_json=json_dumps(data),
        )
        return jsonify({
            "status": True,
            "authorization_url": data["data"]["authorization_url"],
            "access_code": data["data"]["access_code"],
            "reference": data["data"]["reference"],
            "callback_url": callback_url,
        })

    return jsonify({
        "status": False,
        "message": data.get("message", "Payment initialization failed"),
    }), 400


@app.route("/payment/callback")
def payment_callback():
    """Paystack callback page - verifies payment asynchronously."""
    return render_template("payment_callback.html")


@app.route("/payment/paystack/verify", methods=["GET"])
@handle_api_errors
def paystack_verify():
    """Verify a Paystack payment and update local booking/payment state."""
    if not paystack_is_configured():
        return jsonify({"error": "Paystack not configured"}), 500

    reference = request.args.get("reference") or session.get("paystack_reference")
    if not reference:
        return jsonify({"error": "No payment reference"}), 400

    try:
        data = verify_transaction(reference)
    except requests.RequestException as exc:
        logger.error("Paystack verification error: %s", exc)
        return jsonify({"error": str(exc)}), 500

    if data.get("status") and data["data"].get("status") == "success":
        session["payment_status"] = "paid"
        session["paystack_reference"] = reference
        session["payment_verified_at"] = datetime.now(timezone.utc).isoformat()
        session["payment_amount"] = data["data"].get("amount")

        if session.get("pnr_data"):
            session["pnr_data"]["payment_status"] = "paid"

        # No external booking-status endpoint exists; rely on local data.
        metadata = data["data"].get("metadata") or {}
        invoice_id = (
            session.get("invoice_id")
            or metadata.get("invoice_id")
            or get_invoice_id_for_reference(reference)
            or reference
        )
        session["invoice_id"] = invoice_id
        upsert_payment(
            reference,
            invoice_id or reference,
            amount_naira=kobo_to_naira(data["data"].get("amount")),
            currency=data["data"].get("currency") or "NGN",
            status="paid",
            paid_at=data["data"].get("paid_at"),
            raw_verify_json=json_dumps(data),
        )
        if invoice_id:
            upsert_booking(
                invoice_id,
                payment_status="paid",
                amount_naira=kobo_to_naira(data["data"].get("amount")),
            )
        status_data = local_booking_status(invoice_id)
        session["latest_status_response"] = status_data
        pnr_generated = bool(status_data.get("has_pnr") and status_data.get("pnr"))
        logger.info("Payment verified for invoice %s.", invoice_id)

        return jsonify({
            "status": True,
            "message": "Payment successful",
            "reference": reference,
            "amount": data["data"].get("amount"),
            "paid_at": data["data"].get("paid_at"),
            "pnr_generated": pnr_generated,
        })

    invoice_id = session.get("invoice_id") or reference
    upsert_payment(
        reference,
        invoice_id,
        status=data.get("data", {}).get("status") or "failed",
        raw_verify_json=json_dumps(data),
    )
    return jsonify({
        "status": False,
        "message": data.get("message", "Payment verification failed"),
    }), 400


@app.route("/payment/paystack/webhook", methods=["POST"])
def paystack_webhook():
    """Handle Paystack webhook for payment confirmation."""
    signature = request.headers.get("x-paystack-signature")
    body = request.get_data()

    if not verify_webhook_signature(body, signature):
        return jsonify({"error": "Invalid signature"}), 401

    event = request.get_json(silent=True) or {}
    if event.get("event") == "charge.success":
        data = event.get("data", {})
        reference = data.get("reference")
        amount = data.get("amount")
        customer_email = data.get("customer", {}).get("email")
        invoice_id = (
            data.get("metadata", {}).get("invoice_id")
            or get_invoice_id_for_reference(reference)
            or reference
        )

        logger.info(
            "Payment confirmed via webhook: reference=%s, amount=%s, email=%s",
            reference, amount, customer_email,
        )
        upsert_payment(
            reference,
            invoice_id,
            email=customer_email,
            amount_naira=kobo_to_naira(amount),
            currency=data.get("currency") or "NGN",
            status="paid",
            paid_at=data.get("paid_at"),
            raw_webhook_json=json_dumps(event),
        )
        upsert_booking(
            invoice_id,
            payment_status="paid",
            amount_naira=kobo_to_naira(amount),
        )

        if reference and reference == session.get("paystack_reference"):
            session["payment_status"] = "paid"
            session["payment_verified_at"] = datetime.now(timezone.utc).isoformat()
            if session.get("pnr_data"):
                session["pnr_data"]["payment_status"] = "paid"
    elif event:
        data = event.get("data", {})
        reference = data.get("reference")
        invoice_id = (
            data.get("metadata", {}).get("invoice_id")
            or get_invoice_id_for_reference(reference)
            or reference
        )
        if reference and invoice_id:
            upsert_payment(
                reference,
                invoice_id,
                email=data.get("customer", {}).get("email"),
                amount_naira=kobo_to_naira(data.get("amount")),
                currency=data.get("currency") or "NGN",
                status=data.get("status") or event.get("event"),
                raw_webhook_json=json_dumps(event),
            )

    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(debug=FLASK_DEBUG)
