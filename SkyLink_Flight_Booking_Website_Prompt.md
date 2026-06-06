# SkyLink Flight Booking Website — Build Prompt

**Tech Stack:** Python (Flask) · HTML/CSS/JS · SkyLink External API v1.0  
**Design Target:** Modern, premium travel portal — think Wakanow / 247travels.com  
**API Base URL:** `https://247travels.cloud/api/`  
**Auth:** JWT Bearer Token via `POST /api/login`

---

## PROJECT OVERVIEW

Build a full-stack flight booking website powered by the **SkyLink External API**. The site allows users to search for flights, view results, verify pricing, and complete a booking (PNR generation) through a clean, modern travel-brand UI. The backend is Python (Flask), the frontend uses HTML/CSS/JS with a premium travel aesthetic.

---

## VISUAL DESIGN DIRECTION

Match the premium travel portal aesthetic of Wakanow.com and 247travels.com:

- **Color palette:** Deep navy (`#0B1F4A`) as the primary brand color; golden amber (`#F5A623`) as the accent; crisp white for content surfaces.
- **Typography:** Use Google Fonts — `Syne` (700) for headlines, `DM Sans` (400/500) for body. Import via `<link>` in `<head>`.
- **Hero section:** Full-width gradient header (navy → dark blue) with a centered flight search form. Add a subtle world map SVG watermark behind the form at low opacity.
- **Cards:** White, `border-radius: 12px`, `box-shadow: 0 2px 16px rgba(0,0,0,0.08)`. Hover lifts with `transform: translateY(-2px)` transition.
- **Buttons:** Primary = amber fill (`#F5A623`), navy text, `border-radius: 8px`, bold. Hover darkens to `#E09510`.
- **Navigation bar:** Sticky, navy background, white logo text `247Travels`, nav links in white.
- **Loading states:** Skeleton shimmer loaders on flight result cards while fetching.
- **Mobile responsive:** Full mobile support using CSS Grid and Flexbox.

---

## FILE STRUCTURE

```
project/
├── app.py                    # Flask application entry point
├── requirements.txt
├── .env                      # SKYLINK_EMAIL, SKYLINK_PASSWORD
├── skylink/
│   ├── __init__.py
│   ├── auth.py               # Login, token refresh, token caching
│   └── api.py                # search(), pricing(), reserve(), booking_status()
├── templates/
│   ├── base.html             # Shared layout, navbar, footer
│   ├── index.html            # Homepage with search form
│   ├── results.html          # Flight results listing
│   ├── passenger.html        # Passenger details form
│   ├── confirm.html          # Booking confirmation / PNR display
│   └── error.html            # Error page
└── static/
    ├── css/
    │   └── main.css
    └── js/
        └── search.js
```

---

## PART 1 — BACKEND (Flask + SkyLink API)

### 1.1 `requirements.txt`

```
flask>=3.0
requests>=2.31
python-dotenv>=1.0
```

### 1.2 `.env`

```
SKYLINK_EMAIL=your@email.com
SKYLINK_PASSWORD=yourpassword
FLASK_SECRET_KEY=replace_with_random_string
```

### 1.3 `skylink/auth.py` — Token Management

Implement a thread-safe token cache. The `access_token` expires in **900 seconds (15 minutes)**. Proactively refresh before expiry.

```python
"""
Token cache rules (from SkyLink docs):
- POST https://247travels.com/api/login
- Body: { "email": str, "password": str }
- Returns: access_token (expires in 900s), refresh_token (expires in 15 days)
- Include in all requests: Authorization: Bearer <access_token>
- Only accounts with role "api" or "admin" can authenticate.
- Login error codes: INVALID_CREDENTIALS, ACCOUNT_LOCKED, ROLE_NOT_PERMITTED, etc.
"""

import time, threading, requests, os
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://247travels.com/api"
_token_cache = {"access_token": None, "expires_at": 0, "refresh_token": None}
_lock = threading.Lock()

def get_token() -> str:
    """Return a valid access_token, refreshing if within 60s of expiry."""
    with _lock:
        if time.time() < _token_cache["expires_at"] - 60:
            return _token_cache["access_token"]
        return _do_login()

def _do_login() -> str:
    resp = requests.post(f"{BASE_URL}/login", json={
        "email": os.getenv("SKYLINK_EMAIL"),
        "password": os.getenv("SKYLINK_PASSWORD"),
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "LOGIN_SUCCESS":
        raise RuntimeError(f"SkyLink login failed: {data.get('code')}")
    token_data = data["data"]
    _token_cache["access_token"] = token_data["access_token"]
    _token_cache["refresh_token"] = token_data["refresh_token"]
    _token_cache["expires_at"] = time.time() + token_data["expires_in"]  # 900s
    return _token_cache["access_token"]

def auth_headers() -> dict:
    return {"Authorization": f"Bearer {get_token()}", "Content-Type": "application/json"}
```

### 1.4 `skylink/api.py` — API Wrapper

```python
"""
SkyLink External API wrapper.

IMPORTANT RULES (from SkyLink External Integration Guide):
1. SEARCH  → POST /api/flights/search   → returns booking_token per flight
2. PRICING → POST /api/flights/pricing  → ALWAYS call before reserve; returns UPDATED booking_token
3. RESERVE → POST /api/flights/reserve  → uses booking_token from PRICING (not search)
4. STATUS  → GET  /api/flights/booking/status/{invoice_id} → poll until has_pnr: true

Token lifecycle:
- booking_token from search must be passed to pricing unchanged.
- After pricing, use the NEW booking_token returned in the pricing response for reserve.
- Never reuse a booking_token after a successful reserve (it is marked used=1).
- booking_token expires in 30min and is extended on each pricing call.

Error handling:
- 400: Bad request / missing params
- 401: JWT expired — auth.py handles refresh automatically
- 403: PNR blocked (blocked: true in body) — show user-friendly message
- 502: Supplier unreachable — show retry option
- 500: Internal error

Rate limits (per authenticated user):
- /api/flights/search:  60/min
- /api/flights/pricing: 30/min
- /api/flights/reserve: 10/min
"""

import requests
from .auth import auth_headers, BASE_URL

def _post(path: str, payload: dict) -> dict:
    resp = requests.post(f"{BASE_URL}{path}", json=payload, headers=auth_headers(), timeout=30)
    return resp.json()

def _get(path: str) -> dict:
    resp = requests.get(f"{BASE_URL}{path}", headers=auth_headers(), timeout=15)
    return resp.json()

def search_flights(payload: dict) -> dict:
    """
    POST /api/flights/search
    Required fields for one-way: search_mode, from, to, flights_departure_date,
                                  flight_type="oneway", adults (min 1, max 9)
    Optional: children, infants (cannot exceed adults), class, currency
    For roundtrip: add flights_return_date, flight_type="roundtrip"
    For multicity: flight_type="multicity", routes=[{from,to,date},...] (min 2 legs)
                   from/to/departure_date are auto-derived from routes array.
    search_mode: "local" (internal DB) or "external" (live airline inventory)
    Returns list of flights, each with booking_token.
    """
    return _post("/flights/search", payload)

def price_flight(booking_token: str, passengers: dict, currency: str = "NGN", cabin_class: str = "economy") -> dict:
    """
    POST /api/flights/pricing
    Always call this immediately before reserve (within 5 minutes).
    Returns updated booking_token — USE THIS token for reserve, not the search token.
    passengers: {"adults": int, "children": int, "infants": int}
    """
    return _post("/flights/pricing", {
        "booking_token": booking_token,
        "passengers": passengers,
        "currency": currency,
        "class": cabin_class,
    })

def reserve_flight(booking_token: str, passengers: dict, travellers: dict, currency: str = "NGN") -> dict:
    """
    POST /api/flights/reserve
    booking_token MUST come from the most recent /api/flights/pricing response.
    travellers object: { "primary_guest": {...}, "travelers": {"adult_0": {...}, ...} }
    primary_guest required fields: title, first_name, last_name, email, phone,
                                   country_code, dob, gender, passport_number,
                                   passport_expiry, passport_issue_date, nationality
    additional passengers keyed: adult_0, adult_1 / child_0, child_1 / infant_0
    adult_0 always mirrors primary_guest — do not omit.
    
    NOTE: This endpoint IMMEDIATELY generates a live PNR. Only call after
    confirming payment (or for internal admin use). Per SkyLink ToS, payment
    must be collected BEFORE calling this endpoint.
    
    Returns for api-role: { status: "payment_required", invoice_id, payment_url, amount }
    """
    return _post("/flights/reserve", {
        "booking_token": booking_token,
        "passengers": passengers,
        "travellers": travellers,
        "currency": currency,
        "ticket_time_limit_hours": 48,
    })

def booking_status(invoice_id: str) -> dict:
    """
    GET /api/flights/booking/status/{invoice_id}
    Poll after payment completes. Poll every 5-10 seconds for up to 5 minutes.
    Stop when has_pnr is True or booking_status shows an error.
    Returns: { invoice_id, pnr, has_pnr, booking_status, payment_status, is_paid }
    """
    return _get(f"/flights/booking/status/{invoice_id}")
```

### 1.5 `app.py` — Flask Routes

```python
"""
Flask routes for the flight booking website.
Booking flow (api-role, 4 steps):
  1. GET  /           → show search form
  2. POST /search     → call SkyLink search, display results
  3. POST /price      → call SkyLink pricing with selected booking_token
  4. GET  /book/{token} → show passenger form (pre-loaded with pricing data)
  5. POST /reserve    → call SkyLink reserve → redirect to payment_url
  6. GET  /status     → poll SkyLink booking status (AJAX endpoint)
  7. GET  /confirm    → show PNR confirmation page

Session stores: selected flight data, pricing token, invoice_id.
"""

from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import os, json
from dotenv import load_dotenv
from skylink.api import search_flights, price_flight, reserve_flight, booking_status

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret")

# ─── 1. Homepage ───────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

# ─── 2. Flight Search ──────────────────────────────────────────────────────────
@app.route("/search", methods=["POST"])
def search():
    trip_type = request.form.get("trip_type", "oneway")
    adults    = int(request.form.get("adults", 1))
    children  = int(request.form.get("children", 0))
    infants   = int(request.form.get("infants", 0))
    cabin     = request.form.get("class", "economy")

    payload = {
        "search_mode": "external",
        "flight_type": trip_type,
        "adults": adults,
        "children": children,
        "infants": infants,
        "class": cabin,
        "currency": "NGN",
    }

    if trip_type == "multicity":
        # Parse routes from repeating form fields: from_0, to_0, date_0, ...
        routes = []
        i = 0
        while request.form.get(f"from_{i}"):
            routes.append({
                "from": request.form[f"from_{i}"],
                "to": request.form[f"to_{i}"],
                "date": request.form[f"date_{i}"],
            })
            i += 1
        payload["routes"] = routes
    else:
        payload["from"] = request.form.get("from")
        payload["to"] = request.form.get("to")
        payload["flights_departure_date"] = request.form.get("departure_date")
        if trip_type == "roundtrip":
            payload["flights_return_date"] = request.form.get("return_date")

    result = search_flights(payload)

    if not result.get("success"):
        return render_template("error.html", message=result.get("message", "Search failed."))

    flights = result["data"]["flights"]
    meta    = result["data"]["meta"]
    # Store search params in session for display
    session["search_params"] = payload
    return render_template("results.html", flights=flights, meta=meta, passengers={
        "adults": adults, "children": children, "infants": infants
    })

# ─── 3. Select & Price ─────────────────────────────────────────────────────────
@app.route("/price", methods=["POST"])
def price():
    booking_token = request.form.get("booking_token")
    passengers = {
        "adults":   int(request.form.get("adults", 1)),
        "children": int(request.form.get("children", 0)),
        "infants":  int(request.form.get("infants", 0)),
    }
    cabin = request.form.get("class", "economy")

    result = price_flight(booking_token, passengers, currency="NGN", cabin_class=cabin)

    if not result.get("success"):
        return render_template("error.html", message=result.get("message", "Pricing failed."))

    pricing = result["data"]
    # IMPORTANT: store the NEW booking_token from pricing response, not search token
    session["pricing_token"]  = pricing["booking_token"]
    session["pricing_data"]   = pricing
    session["passengers"]     = passengers
    session["cabin_class"]    = cabin
    return redirect(url_for("passenger_form"))

# ─── 4. Passenger Form ─────────────────────────────────────────────────────────
@app.route("/passengers")
def passenger_form():
    pricing = session.get("pricing_data")
    if not pricing:
        return redirect(url_for("index"))
    passengers = session.get("passengers", {"adults": 1, "children": 0, "infants": 0})
    return render_template("passenger.html", pricing=pricing, passengers=passengers)

# ─── 5. Reserve (Create Booking) ───────────────────────────────────────────────
@app.route("/reserve", methods=["POST"])
def reserve():
    booking_token = session.get("pricing_token")
    passengers    = session.get("passengers")
    if not booking_token:
        return redirect(url_for("index"))

    # Build primary_guest from form
    primary = {
        "title":               request.form.get("title"),
        "first_name":          request.form.get("first_name"),
        "last_name":           request.form.get("last_name"),
        "other_name":          request.form.get("other_name", ""),
        "email":               request.form.get("email"),
        "phone":               request.form.get("phone"),
        "country_code":        request.form.get("country_code", "234"),
        "dob":                 request.form.get("dob"),
        "gender":              request.form.get("gender"),
        "passport_number":     request.form.get("passport_number"),
        "passport_expiry":     request.form.get("passport_expiry"),
        "passport_issue_date": request.form.get("passport_issue_date"),
        "nationality":         request.form.get("nationality"),
    }

    travellers = {
        "primary_guest": primary,
        "travelers": {"adult_0": primary},  # adult_0 always mirrors primary_guest
    }

    # Additional adults
    for i in range(1, passengers.get("adults", 1)):
        travellers["travelers"][f"adult_{i}"] = _parse_extra_passenger(request.form, f"adult_{i}")

    # Children
    for i in range(passengers.get("children", 0)):
        travellers["travelers"][f"child_{i}"] = _parse_extra_passenger(request.form, f"child_{i}")

    # Infants
    for i in range(passengers.get("infants", 0)):
        travellers["travelers"][f"infant_{i}"] = _parse_extra_passenger(request.form, f"infant_{i}")

    result = reserve_flight(booking_token, passengers, travellers)

    if not result.get("success"):
        # Check for PNR restriction (403 / blocked: true)
        if result.get("blocked"):
            return render_template("error.html",
                message=result.get("message", "This carrier is not available at this time."))
        return render_template("error.html", message=result.get("message", "Booking failed."))

    data = result["data"]
    invoice_id  = data.get("invoice_id")
    payment_url = data.get("payment_url")
    session["invoice_id"] = invoice_id

    # Redirect user to Paystack checkout
    return redirect(payment_url)

# ─── 6. Booking Status (AJAX polling) ─────────────────────────────────────────
@app.route("/status")
def status():
    """
    Called by frontend JS every 5-10 seconds after Paystack redirect.
    Returns JSON. Stop polling when has_pnr is True or booking_status is error.
    """
    invoice_id = session.get("invoice_id") or request.args.get("invoice_id")
    if not invoice_id:
        return jsonify({"error": "No invoice_id in session"}), 400
    result = booking_status(invoice_id)
    return jsonify(result)

# ─── 7. Confirmation Page ──────────────────────────────────────────────────────
@app.route("/confirm")
def confirm():
    invoice_id = session.get("invoice_id")
    if not invoice_id:
        return redirect(url_for("index"))
    return render_template("confirm.html", invoice_id=invoice_id)

# ─── Helpers ───────────────────────────────────────────────────────────────────
def _parse_extra_passenger(form, prefix: str) -> dict:
    return {
        "title":               form.get(f"{prefix}_title"),
        "first_name":          form.get(f"{prefix}_first_name"),
        "last_name":           form.get(f"{prefix}_last_name"),
        "other_name":          form.get(f"{prefix}_other_name", ""),
        "dob":                 form.get(f"{prefix}_dob"),
        "gender":              form.get(f"{prefix}_gender"),
        "passport_number":     form.get(f"{prefix}_passport_number"),
        "passport_expiry":     form.get(f"{prefix}_passport_expiry"),
        "passport_issue_date": form.get(f"{prefix}_passport_issue_date"),
        "nationality":         form.get(f"{prefix}_nationality"),
    }

if __name__ == "__main__":
    app.run(debug=True)
```

---

## PART 2 — FRONTEND TEMPLATES

### 2.1 `templates/base.html`

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}247Travels{% endblock %}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Syne:wght@700;800&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="{{ url_for('static', filename='css/main.css') }}">
</head>
<body>

  <!-- Sticky Navbar -->
  <nav class="navbar">
    <div class="container navbar-inner">
      <a href="/" class="logo">247<span>Travels</span></a>
      <ul class="nav-links">
        <li><a href="/">Flights</a></li>
        <li><a href="#">Hotels</a></li>
        <li><a href="#">Visa</a></li>
        <li><a href="#">Contact</a></li>
      </ul>
    </div>
  </nav>

  {% block content %}{% endblock %}

  <!-- Footer -->
  <footer class="footer">
    <div class="container">
      <p>© 2025 247Travels Limited · Powered by SkyLink API · Lagos · Abuja · Kano</p>
    </div>
  </footer>

  {% block scripts %}{% endblock %}
</body>
</html>
```

### 2.2 `templates/index.html` — Homepage with Search Form

```html
{% extends "base.html" %}
{% block title %}Search Flights — 247Travels{% endblock %}

{% block content %}
<!-- Hero Section -->
<section class="hero">
  <!-- SVG world map watermark (inline, low-opacity) -->
  <svg class="hero-map" viewBox="0 0 900 450" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <!-- Add simplified world outline paths here, or use a CDN path -->
    <text x="50%" y="50%" text-anchor="middle" fill="white" opacity="0.04"
          font-size="220" font-family="Syne, sans-serif">✈</text>
  </svg>

  <div class="container">
    <h1 class="hero-title">Where to next?</h1>
    <p class="hero-sub">Search hundreds of airlines. Best fares, instant booking.</p>

    <!-- Trip Type Tabs -->
    <div class="trip-tabs">
      <button class="tab-btn active" data-type="oneway">One Way</button>
      <button class="tab-btn" data-type="roundtrip">Round Trip</button>
      <button class="tab-btn" data-type="multicity">Multi-City</button>
    </div>

    <!-- Search Form -->
    <form action="/search" method="POST" class="search-card" id="searchForm">
      <input type="hidden" name="trip_type" id="tripTypeInput" value="oneway">

      <!-- One-way / Roundtrip Row -->
      <div id="simpleRoute" class="form-row">
        <div class="form-group">
          <label>From</label>
          <input type="text" name="from" placeholder="City or Airport (e.g. LOS)" required
                 class="form-input" autocomplete="off">
        </div>
        <div class="swap-btn" onclick="swapRoutes()">⇄</div>
        <div class="form-group">
          <label>To</label>
          <input type="text" name="to" placeholder="City or Airport (e.g. LHR)" required
                 class="form-input" autocomplete="off">
        </div>
        <div class="form-group">
          <label>Departure</label>
          <input type="date" name="departure_date" required class="form-input">
        </div>
        <div class="form-group" id="returnDateGroup" style="display:none">
          <label>Return</label>
          <input type="date" name="return_date" class="form-input">
        </div>
      </div>

      <!-- Multi-city Legs -->
      <div id="multiCityRoutes" style="display:none">
        <div class="multicity-legs" id="legsContainer">
          <!-- Leg 0 -->
          <div class="leg-row">
            <input type="text" name="from_0" placeholder="From" class="form-input">
            <input type="text" name="to_0" placeholder="To" class="form-input">
            <input type="date" name="date_0" class="form-input">
          </div>
          <!-- Leg 1 -->
          <div class="leg-row">
            <input type="text" name="from_1" placeholder="From" class="form-input">
            <input type="text" name="to_1" placeholder="To" class="form-input">
            <input type="date" name="date_1" class="form-input">
          </div>
        </div>
        <button type="button" onclick="addLeg()" class="btn-outline">+ Add another city</button>
      </div>

      <!-- Passengers & Class Row -->
      <div class="form-row passengers-row">
        <div class="form-group">
          <label>Adults</label>
          <select name="adults" class="form-input">
            {% for i in range(1, 10) %}
            <option value="{{ i }}" {% if i == 1 %}selected{% endif %}>{{ i }} Adult{{ 's' if i > 1 }}</option>
            {% endfor %}
          </select>
        </div>
        <div class="form-group">
          <label>Children</label>
          <select name="children" class="form-input">
            {% for i in range(0, 9) %}
            <option value="{{ i }}">{{ i }} Child{{ 'ren' if i > 1 else ('ren' if i == 1 else '') }}</option>
            {% endfor %}
          </select>
        </div>
        <div class="form-group">
          <label>Infants</label>
          <select name="infants" class="form-input">
            {% for i in range(0, 9) %}
            <option value="{{ i }}">{{ i }} Infant{{ 's' if i > 1 }}</option>
            {% endfor %}
          </select>
        </div>
        <div class="form-group">
          <label>Class</label>
          <select name="class" class="form-input">
            <option value="economy">Economy</option>
            <option value="premium_economy">Premium Economy</option>
            <option value="business">Business</option>
            <option value="first">First Class</option>
          </select>
        </div>
        <button type="submit" class="btn-search">Search Flights ✈</button>
      </div>
    </form>
  </div>
</section>

<!-- Popular Destinations -->
<section class="destinations">
  <div class="container">
    <h2 class="section-title">Popular Destinations</h2>
    <div class="dest-grid">
      {% for dest in [("Dubai", "DXB", "from ₦ 759,354"), ("London", "LHR", "from ₦ 1,204,000"),
                      ("New York", "JFK", "from ₦ 1,850,000"), ("Accra", "ACC", "from ₦ 95,000")] %}
      <div class="dest-card">
        <div class="dest-icon">✈</div>
        <div class="dest-info">
          <h3>{{ dest[0] }}</h3>
          <p class="dest-code">{{ dest[1] }}</p>
          <p class="dest-price">{{ dest[2] }}</p>
        </div>
      </div>
      {% endfor %}
    </div>
  </div>
</section>
{% endblock %}

{% block scripts %}
<script src="{{ url_for('static', filename='js/search.js') }}"></script>
{% endblock %}
```

### 2.3 `templates/results.html` — Flight Results

```html
{% extends "base.html" %}
{% block title %}Flight Results — 247Travels{% endblock %}

{% block content %}
<section class="results-section">
  <div class="container">
    <div class="results-header">
      <h2>{{ meta.total_flights }} flights found</h2>
      <p class="meta-info">{{ meta.origin }} → {{ meta.destination }} · {{ meta.currency }}</p>
    </div>

    {% if flights %}
    <div class="results-list">
      {% for flight in flights %}
      <div class="flight-card">
        <div class="flight-airline">
          <span class="airline-code">{{ flight.airline }}</span>
          <span class="airline-name">{{ flight.airline_name }}</span>
          <span class="flight-no">{{ flight.flight_no }}</span>
        </div>

        <div class="flight-route">
          <div class="route-point">
            <span class="time">{{ flight.departure_time }}</span>
            <span class="code">{{ flight.departure_code }}</span>
          </div>
          <div class="route-line">
            <span class="duration">{{ flight.duration_time }}</span>
            <div class="line-bar"></div>
            <span class="flight-class">{{ flight.class }}</span>
          </div>
          <div class="route-point">
            <span class="time">{{ flight.arrival_time }}</span>
            <span class="code">{{ flight.arrival_code }}</span>
          </div>
        </div>

        <div class="flight-meta">
          <span class="baggage">🧳 {{ flight.baggage }}</span>
        </div>

        <div class="flight-price">
          <span class="price">₦{{ "{:,.0f}".format(flight.price) }}</span>
          <span class="per-pax">per person</span>
          <!-- POST to /price with the booking_token from THIS flight -->
          <form action="/price" method="POST" style="margin:0">
            <input type="hidden" name="booking_token" value="{{ flight.booking_token }}">
            <input type="hidden" name="adults" value="{{ passengers.adults }}">
            <input type="hidden" name="children" value="{{ passengers.children }}">
            <input type="hidden" name="infants" value="{{ passengers.infants }}">
            <input type="hidden" name="class" value="{{ flight.class | lower }}">
            <button type="submit" class="btn-select">Select</button>
          </form>
        </div>
      </div>
      {% endfor %}
    </div>
    {% else %}
    <div class="no-results">
      <p>No flights found for this route. Please try different dates or airports.</p>
      <a href="/" class="btn-primary">New Search</a>
    </div>
    {% endif %}
  </div>
</section>
{% endblock %}
```

### 2.4 `templates/passenger.html` — Passenger Details

```html
{% extends "base.html" %}
{% block title %}Passenger Details — 247Travels{% endblock %}

{% block content %}
<section class="passenger-section">
  <div class="container">

    <!-- Price Summary Banner -->
    <div class="price-banner">
      <div>
        <span class="verified-badge">✓ Price Verified</span>
        {% if pricing.price_changed %}
        <span class="price-changed-badge">⚠ Price changed from ₦{{ "{:,.0f}".format(pricing.original_price) }}</span>
        {% endif %}
      </div>
      <div class="price-total">
        <span>Total: </span>
        <strong>₦{{ "{:,.0f}".format(pricing.verified_price) }}</strong>
      </div>
      <div class="expires">Offer expires: {{ pricing.expires_at }}</div>
    </div>

    <h2>Passenger Details</h2>
    <p class="section-hint">Enter names exactly as they appear on your passport.</p>

    <form action="/reserve" method="POST" class="passenger-form">

      <!-- Lead Passenger (primary_guest) -->
      <div class="passenger-block">
        <h3>Lead Passenger (Adult 1)</h3>
        <div class="form-grid">
          <div class="form-group">
            <label>Title</label>
            <select name="title" class="form-input" required>
              <option value="">Select</option>
              <option value="Mr">Mr</option>
              <option value="Mrs">Mrs</option>
              <option value="Ms">Ms</option>
              <option value="Miss">Miss</option>
              <option value="Dr">Dr</option>
              <option value="Prof">Prof</option>
            </select>
          </div>
          <div class="form-group">
            <label>First Name</label>
            <input type="text" name="first_name" class="form-input" required placeholder="As on passport">
          </div>
          <div class="form-group">
            <label>Middle Name</label>
            <input type="text" name="other_name" class="form-input" placeholder="Optional">
          </div>
          <div class="form-group">
            <label>Last Name</label>
            <input type="text" name="last_name" class="form-input" required placeholder="As on passport">
          </div>
          <div class="form-group">
            <label>Email</label>
            <input type="email" name="email" class="form-input" required>
          </div>
          <div class="form-group">
            <label>Phone Number</label>
            <input type="tel" name="phone" class="form-input" required placeholder="08012345678">
          </div>
          <div class="form-group">
            <label>Country Code</label>
            <input type="text" name="country_code" class="form-input" value="234" required>
          </div>
          <div class="form-group">
            <label>Date of Birth</label>
            <input type="date" name="dob" class="form-input" required>
          </div>
          <div class="form-group">
            <label>Gender</label>
            <select name="gender" class="form-input" required>
              <option value="male">Male</option>
              <option value="female">Female</option>
            </select>
          </div>
          <div class="form-group">
            <label>Passport Number</label>
            <input type="text" name="passport_number" class="form-input" required placeholder="A12345678">
          </div>
          <div class="form-group">
            <label>Passport Issue Date</label>
            <input type="date" name="passport_issue_date" class="form-input" required>
          </div>
          <div class="form-group">
            <label>Passport Expiry Date</label>
            <input type="date" name="passport_expiry" class="form-input" required>
          </div>
          <div class="form-group">
            <label>Nationality (ISO code)</label>
            <input type="text" name="nationality" class="form-input" required placeholder="NG" maxlength="2">
          </div>
        </div>
      </div>

      <!-- Additional adult passengers (adult_1, adult_2...) -->
      {% for i in range(1, passengers.adults) %}
      <div class="passenger-block">
        <h3>Adult {{ i + 1 }}</h3>
        <div class="form-grid">
          <!-- Repeat same fields with prefix adult_{{ i }}_ -->
          <div class="form-group">
            <label>Title</label>
            <select name="adult_{{ i }}_title" class="form-input">
              <option>Mr</option><option>Mrs</option><option>Ms</option>
            </select>
          </div>
          <div class="form-group">
            <label>First Name</label>
            <input type="text" name="adult_{{ i }}_first_name" class="form-input" required>
          </div>
          <div class="form-group">
            <label>Last Name</label>
            <input type="text" name="adult_{{ i }}_last_name" class="form-input" required>
          </div>
          <div class="form-group">
            <label>Date of Birth</label>
            <input type="date" name="adult_{{ i }}_dob" class="form-input" required>
          </div>
          <div class="form-group">
            <label>Gender</label>
            <select name="adult_{{ i }}_gender" class="form-input">
              <option value="male">Male</option><option value="female">Female</option>
            </select>
          </div>
          <div class="form-group">
            <label>Passport Number</label>
            <input type="text" name="adult_{{ i }}_passport_number" class="form-input" required>
          </div>
          <div class="form-group">
            <label>Passport Expiry</label>
            <input type="date" name="adult_{{ i }}_passport_expiry" class="form-input" required>
          </div>
          <div class="form-group">
            <label>Passport Issue Date</label>
            <input type="date" name="adult_{{ i }}_passport_issue_date" class="form-input" required>
          </div>
          <div class="form-group">
            <label>Nationality</label>
            <input type="text" name="adult_{{ i }}_nationality" class="form-input" maxlength="2" required>
          </div>
        </div>
      </div>
      {% endfor %}

      <!-- Children & Infants: same pattern with child_N_ and infant_N_ prefixes -->

      <div class="form-actions">
        <button type="submit" class="btn-primary btn-large">
          Continue to Payment →
        </button>
        <p class="payment-note">
          You will be redirected to a secure Paystack payment page.
          Your PNR will be issued automatically after payment is confirmed.
        </p>
      </div>
    </form>
  </div>
</section>
{% endblock %}
```

### 2.5 `templates/confirm.html` — Confirmation & PNR Polling

```html
{% extends "base.html" %}
{% block title %}Booking Confirmation — 247Travels{% endblock %}

{% block content %}
<section class="confirm-section">
  <div class="container confirm-container">

    <!-- Polling State (shown while waiting for PNR) -->
    <div id="pollingState" class="polling-card">
      <div class="spinner"></div>
      <h2>Confirming your booking…</h2>
      <p>Payment received. Generating your PNR with the airline. This takes a few seconds.</p>
    </div>

    <!-- Success State (shown when has_pnr: true) -->
    <div id="successState" style="display:none" class="success-card">
      <div class="success-icon">✓</div>
      <h2>Booking Confirmed!</h2>
      <div class="pnr-display">
        <p>Your PNR Reference</p>
        <h1 id="pnrCode" class="pnr-code">—</h1>
      </div>
      <p class="pnr-note">Screenshot or write this down. Present it at check-in.</p>
      <a href="/" class="btn-primary">Book Another Flight</a>
    </div>

    <!-- Error State -->
    <div id="errorState" style="display:none" class="error-card">
      <h2>Something went wrong</h2>
      <p id="errorMessage">Your payment may have been processed but PNR generation failed.
         Please contact support with your invoice ID: <strong id="invoiceDisplay"></strong></p>
      <a href="tel:+2347057000247" class="btn-primary">Call Support</a>
    </div>

  </div>
</section>
{% endblock %}

{% block scripts %}
<script>
const INVOICE_ID = "{{ invoice_id }}";
document.getElementById("invoiceDisplay").textContent = INVOICE_ID;

let pollCount = 0;
const MAX_POLLS = 60;  // 60 × 5s = 5 minutes

async function pollStatus() {
  if (pollCount >= MAX_POLLS) {
    showError("Timed out waiting for PNR. Please contact support.");
    return;
  }
  pollCount++;

  try {
    const resp = await fetch(`/status?invoice_id=${INVOICE_ID}`);
    const data = await resp.json();
    const booking = data?.data;

    if (booking?.has_pnr) {
      document.getElementById("pnrCode").textContent = booking.pnr;
      document.getElementById("pollingState").style.display = "none";
      document.getElementById("successState").style.display = "block";
      return;
    }

    if (booking?.booking_status === "failed" || booking?.error) {
      showError(booking.error || "Booking failed. Contact support.");
      return;
    }

    // Keep polling
    setTimeout(pollStatus, 5000);

  } catch(e) {
    setTimeout(pollStatus, 8000);  // Backoff on network error
  }
}

function showError(msg) {
  document.getElementById("pollingState").style.display = "none";
  document.getElementById("errorState").style.display = "block";
  document.getElementById("errorMessage").innerHTML =
    msg + `<br>Invoice ID: <strong>${INVOICE_ID}</strong>`;
}

// Start polling immediately on page load
pollStatus();
</script>
{% endblock %}
```

### 2.6 `templates/error.html`

```html
{% extends "base.html" %}
{% block title %}Error — 247Travels{% endblock %}
{% block content %}
<section class="error-section">
  <div class="container">
    <h2>Something went wrong</h2>
    <p>{{ message }}</p>
    <a href="/" class="btn-primary">Start Over</a>
  </div>
</section>
{% endblock %}
```

---

## PART 3 — CSS (`static/css/main.css`)

```css
/* ── Variables ──────────────────────────────────────────────────────────────── */
:root {
  --navy:   #0B1F4A;
  --navy-dark: #071530;
  --amber:  #F5A623;
  --amber-dark: #E09510;
  --white:  #FFFFFF;
  --gray-50: #F8F9FB;
  --gray-100: #EEF0F5;
  --gray-400: #9AA1B4;
  --gray-700: #374162;
  --text:   #1A2341;
  --text-muted: #6B7491;
  --shadow-sm: 0 2px 8px rgba(11,31,74,0.08);
  --shadow-md: 0 4px 24px rgba(11,31,74,0.12);
  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;
  font-family: 'DM Sans', sans-serif;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body { background: var(--gray-50); color: var(--text); }

.container { max-width: 1200px; margin: 0 auto; padding: 0 24px; }

/* ── Navbar ─────────────────────────────────────────────────────────────────── */
.navbar {
  background: var(--navy);
  position: sticky; top: 0; z-index: 100;
  padding: 16px 0;
  box-shadow: 0 2px 16px rgba(0,0,0,0.2);
}
.navbar-inner { display: flex; align-items: center; justify-content: space-between; }
.logo { font-family: 'Syne', sans-serif; font-size: 24px; font-weight: 800; color: var(--white); text-decoration: none; }
.logo span { color: var(--amber); }
.nav-links { list-style: none; display: flex; gap: 32px; }
.nav-links a { color: rgba(255,255,255,0.8); text-decoration: none; font-size: 14px; font-weight: 500; transition: color .2s; }
.nav-links a:hover { color: var(--amber); }

/* ── Hero ───────────────────────────────────────────────────────────────────── */
.hero {
  background: linear-gradient(135deg, var(--navy-dark) 0%, var(--navy) 60%, #1a3a7a 100%);
  padding: 72px 0 56px;
  position: relative; overflow: hidden;
}
.hero-map {
  position: absolute; top: 50%; left: 50%;
  transform: translate(-50%, -50%);
  width: 110%; max-width: 1100px; opacity: 0.04;
  pointer-events: none;
}
.hero-title {
  font-family: 'Syne', sans-serif;
  font-size: clamp(36px, 5vw, 64px);
  font-weight: 800; color: var(--white);
  text-align: center; margin-bottom: 12px;
}
.hero-sub {
  text-align: center; color: rgba(255,255,255,0.7);
  font-size: 17px; margin-bottom: 32px;
}

/* Trip tabs */
.trip-tabs {
  display: flex; justify-content: center; gap: 4px; margin-bottom: 20px;
}
.tab-btn {
  background: rgba(255,255,255,0.1); border: 1px solid rgba(255,255,255,0.2);
  color: rgba(255,255,255,0.8); padding: 8px 20px; border-radius: 20px;
  cursor: pointer; font-family: 'DM Sans', sans-serif; font-size: 14px; font-weight: 500;
  transition: all .2s;
}
.tab-btn.active, .tab-btn:hover {
  background: var(--amber); color: var(--navy); border-color: var(--amber);
}

/* Search card */
.search-card {
  background: var(--white); border-radius: var(--radius-lg);
  padding: 28px 32px; box-shadow: var(--shadow-md);
  position: relative; z-index: 1;
}
.form-row {
  display: flex; gap: 16px; align-items: flex-end;
  flex-wrap: wrap; margin-bottom: 16px;
}
.form-group { display: flex; flex-direction: column; gap: 6px; flex: 1; min-width: 140px; }
.form-group label { font-size: 12px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }
.form-input {
  height: 44px; padding: 0 14px; border: 1.5px solid var(--gray-100);
  border-radius: var(--radius-sm); font-family: 'DM Sans', sans-serif;
  font-size: 15px; color: var(--text); background: var(--white);
  transition: border-color .2s; outline: none;
}
.form-input:focus { border-color: var(--navy); }
.swap-btn {
  font-size: 20px; cursor: pointer; padding: 10px; color: var(--navy);
  opacity: 0.6; transition: opacity .2s; margin-bottom: 2px;
}
.swap-btn:hover { opacity: 1; }
.btn-search {
  background: var(--amber); color: var(--navy); font-family: 'DM Sans', sans-serif;
  font-size: 15px; font-weight: 600; padding: 0 32px; height: 44px;
  border: none; border-radius: var(--radius-sm); cursor: pointer;
  transition: background .2s, transform .1s; white-space: nowrap;
}
.btn-search:hover { background: var(--amber-dark); }
.btn-search:active { transform: scale(0.98); }

/* ── Destinations ───────────────────────────────────────────────────────────── */
.destinations { padding: 64px 0; }
.section-title {
  font-family: 'Syne', sans-serif; font-size: 28px; font-weight: 700;
  color: var(--navy); margin-bottom: 32px;
}
.dest-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 20px; }
.dest-card {
  background: var(--white); border-radius: var(--radius-md);
  padding: 24px; box-shadow: var(--shadow-sm); display: flex; gap: 16px;
  align-items: center; cursor: pointer; transition: transform .2s, box-shadow .2s;
}
.dest-card:hover { transform: translateY(-2px); box-shadow: var(--shadow-md); }
.dest-icon { font-size: 28px; }
.dest-info h3 { font-size: 16px; font-weight: 600; color: var(--navy); }
.dest-code { font-size: 13px; color: var(--text-muted); }
.dest-price { font-size: 14px; font-weight: 600; color: var(--amber-dark); margin-top: 4px; }

/* ── Results ────────────────────────────────────────────────────────────────── */
.results-section { padding: 48px 0; }
.results-header { margin-bottom: 24px; }
.results-header h2 { font-family: 'Syne', sans-serif; font-size: 24px; color: var(--navy); }
.meta-info { color: var(--text-muted); font-size: 14px; margin-top: 4px; }
.results-list { display: flex; flex-direction: column; gap: 16px; }

.flight-card {
  background: var(--white); border-radius: var(--radius-md);
  padding: 20px 24px; box-shadow: var(--shadow-sm);
  display: flex; align-items: center; gap: 24px; flex-wrap: wrap;
  transition: box-shadow .2s;
}
.flight-card:hover { box-shadow: var(--shadow-md); }

.flight-airline { min-width: 120px; }
.airline-code { font-family: 'Syne', sans-serif; font-size: 22px; font-weight: 700; color: var(--navy); display: block; }
.airline-name { font-size: 12px; color: var(--text-muted); display: block; }
.flight-no { font-size: 12px; color: var(--amber-dark); font-weight: 600; display: block; margin-top: 2px; }

.flight-route { display: flex; align-items: center; gap: 16px; flex: 1; justify-content: center; }
.route-point { text-align: center; }
.time { font-size: 22px; font-weight: 600; color: var(--navy); display: block; font-family: 'Syne', sans-serif; }
.code { font-size: 14px; color: var(--text-muted); }
.route-line { display: flex; flex-direction: column; align-items: center; gap: 4px; min-width: 120px; }
.duration { font-size: 12px; color: var(--text-muted); }
.line-bar { height: 1.5px; width: 100%; background: var(--gray-100); position: relative; }
.line-bar::after { content: '✈'; position: absolute; right: 0; top: -8px; font-size: 14px; color: var(--navy); }
.flight-class { font-size: 11px; color: var(--amber-dark); font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }

.flight-meta { font-size: 13px; color: var(--text-muted); }
.flight-price { text-align: right; min-width: 140px; }
.price { font-family: 'Syne', sans-serif; font-size: 24px; font-weight: 700; color: var(--navy); display: block; }
.per-pax { font-size: 12px; color: var(--text-muted); display: block; margin-bottom: 10px; }

.btn-select, .btn-primary {
  background: var(--amber); color: var(--navy);
  border: none; border-radius: var(--radius-sm);
  padding: 10px 24px; font-family: 'DM Sans', sans-serif;
  font-size: 14px; font-weight: 600; cursor: pointer;
  transition: background .2s, transform .1s; text-decoration: none; display: inline-block;
}
.btn-select:hover, .btn-primary:hover { background: var(--amber-dark); }

/* ── Passenger Form ─────────────────────────────────────────────────────────── */
.passenger-section { padding: 48px 0; }
.price-banner {
  background: var(--navy); color: white; border-radius: var(--radius-md);
  padding: 16px 24px; display: flex; align-items: center; justify-content: space-between;
  flex-wrap: wrap; gap: 12px; margin-bottom: 32px;
}
.verified-badge { background: #22C55E; color: white; font-size: 12px; font-weight: 600;
  padding: 4px 12px; border-radius: 20px; }
.price-changed-badge { background: #EF4444; color: white; font-size: 12px; font-weight: 600;
  padding: 4px 12px; border-radius: 20px; margin-left: 8px; }
.price-total { font-size: 20px; }
.price-total strong { font-family: 'Syne', sans-serif; font-size: 26px; color: var(--amber); }
.expires { font-size: 12px; opacity: 0.7; }

.passenger-block {
  background: var(--white); border-radius: var(--radius-md);
  padding: 24px; box-shadow: var(--shadow-sm); margin-bottom: 24px;
}
.passenger-block h3 { font-family: 'Syne', sans-serif; font-size: 18px; color: var(--navy); margin-bottom: 20px; }
.form-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px;
}
.form-actions { text-align: center; padding: 16px 0; }
.btn-large { padding: 14px 48px; font-size: 16px; border-radius: 10px; }
.payment-note { font-size: 13px; color: var(--text-muted); margin-top: 12px; }

/* ── Confirm ────────────────────────────────────────────────────────────────── */
.confirm-section { padding: 80px 0; }
.confirm-container { max-width: 560px; }
.polling-card, .success-card, .error-card {
  background: var(--white); border-radius: var(--radius-lg);
  padding: 48px 40px; text-align: center; box-shadow: var(--shadow-md);
}
.polling-card h2, .success-card h2 { font-family: 'Syne', sans-serif; font-size: 28px; color: var(--navy); margin-bottom: 12px; }
.success-icon { font-size: 56px; color: #22C55E; margin-bottom: 16px; }
.pnr-display { background: var(--gray-50); border-radius: var(--radius-md); padding: 24px; margin: 24px 0; }
.pnr-display p { font-size: 13px; color: var(--text-muted); margin-bottom: 8px; }
.pnr-code { font-family: 'Syne', sans-serif; font-size: 48px; font-weight: 800; color: var(--navy); letter-spacing: 6px; }
.pnr-note { color: var(--text-muted); font-size: 14px; margin-bottom: 24px; }

/* Spinner */
.spinner {
  width: 48px; height: 48px; border: 4px solid var(--gray-100);
  border-top-color: var(--navy); border-radius: 50%;
  animation: spin 1s linear infinite; margin: 0 auto 24px;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ── Multi-city ─────────────────────────────────────────────────────────────── */
.multicity-legs { display: flex; flex-direction: column; gap: 12px; margin-bottom: 12px; }
.leg-row { display: flex; gap: 12px; flex-wrap: wrap; }
.leg-row .form-input { flex: 1; min-width: 140px; }
.btn-outline {
  background: transparent; border: 1.5px solid var(--navy);
  color: var(--navy); padding: 8px 20px; border-radius: var(--radius-sm);
  cursor: pointer; font-family: 'DM Sans', sans-serif; font-size: 14px; font-weight: 500;
}

/* ── Footer ─────────────────────────────────────────────────────────────────── */
.footer { background: var(--navy-dark); color: rgba(255,255,255,0.5); padding: 24px 0; text-align: center; font-size: 13px; }

/* ── Responsive ─────────────────────────────────────────────────────────────── */
@media (max-width: 768px) {
  .form-row { flex-direction: column; }
  .btn-search { width: 100%; justify-content: center; }
  .flight-card { flex-direction: column; align-items: flex-start; }
  .flight-price { text-align: left; width: 100%; }
  .nav-links { display: none; }
}
```

---

## PART 4 — JAVASCRIPT (`static/js/search.js`)

```javascript
// Trip type tab switching
document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");

    const type = btn.dataset.type;
    document.getElementById("tripTypeInput").value = type;
    document.getElementById("simpleRoute").style.display   = type !== "multicity" ? "flex" : "none";
    document.getElementById("multiCityRoutes").style.display = type === "multicity" ? "block" : "none";
    document.getElementById("returnDateGroup").style.display = type === "roundtrip" ? "block" : "none";
  });
});

// Swap origin / destination
function swapRoutes() {
  const fromInput = document.querySelector('input[name="from"]');
  const toInput   = document.querySelector('input[name="to"]');
  [fromInput.value, toInput.value] = [toInput.value, fromInput.value];
}

// Add multi-city leg
let legCount = 2;
function addLeg() {
  if (legCount >= 6) return;  // max 6 legs
  const container = document.getElementById("legsContainer");
  const div = document.createElement("div");
  div.className = "leg-row";
  div.innerHTML = `
    <input type="text" name="from_${legCount}" placeholder="From" class="form-input">
    <input type="text" name="to_${legCount}" placeholder="To" class="form-input">
    <input type="date" name="date_${legCount}" class="form-input">
    <button type="button" onclick="this.parentElement.remove(); legCount--" style="background:none;border:none;cursor:pointer;color:#9AA1B4;font-size:20px">✕</button>
  `;
  container.appendChild(div);
  legCount++;
}
```

---

## PART 5 — CRITICAL IMPLEMENTATION NOTES

### Token & Booking Flow

```
1. Login once → cache access_token (900s TTL) → refresh before expiry
2. Search (external mode) → get flights[] with booking_token per flight
3. User selects a flight → POST booking_token to /price
4. Pricing returns a NEW booking_token → store in Flask session
5. User fills passenger form → POST to /reserve using the PRICING token (not search token)
6. Reserve returns payment_url + invoice_id → redirect user to Paystack
7. After Paystack callback, poll GET /status/{invoice_id} every 5-10s
8. When has_pnr: true → display PNR on confirmation page
```

### Error Handling Checklist

| Scenario | Handling |
|---|---|
| `401 Unauthorized` | `auth.py` auto-refreshes token; transparent to user |
| `403 blocked: true` | Display: "This carrier is unavailable. Please choose another flight." |
| `502 Bad Gateway` | Display: "Supplier temporarily unavailable. Please try again." + button |
| `price_changed: true` in pricing | Show old vs new price on passenger form before user proceeds |
| `verification_skipped: true` | Proceed normally; note that live re-check was skipped |
| `has_pnr` never becomes `true` | Stop polling after 5 min; show invoice_id and support phone |

### SkyLink API Field Constraints

- `adults`: 1–9 (minimum 1 required)
- `infants`: cannot exceed `adults`
- `nationality`: 2-letter ISO code (`"NG"`, `"GB"`, `"US"`)
- `country_code`: digits only, no `+` (`"234"` not `"+234"`)
- `passport_number`: alphanumeric only
- All dates: `YYYY-MM-DD` format
- `booking_token`: pass UNCHANGED — do not modify
- Multi-city: minimum 2 legs in `routes` array
- `ticket_time_limit_hours`: default 48 (hours before PNR must be ticketed)
- For Amadeus via admin role: maps to `DELAY_TO_CANCEL` (`PT48H`)
- For Brightsun: `ticket_time_limit_hours` is accepted but ignored

### PNR Restriction Response (403)

```json
{
  "success": false,
  "blocked": true,
  "carrier": "KQ",
  "message": "KQ reservations are not available at this time."
}
```
Check for `blocked: true` in reserve response and render a friendly message.

### Rate Limits

- Search: 60/min · Pricing: 30/min · Reserve: 10/min · Login: 10/min (per IP)
- Cache search results client-side; re-search if user takes >10 minutes

### Session Data to Store

```python
session["search_params"]  = { ...search payload... }
session["pricing_token"]  = pricing["data"]["booking_token"]  # from PRICING, not search
session["pricing_data"]   = pricing["data"]
session["passengers"]     = { "adults": N, "children": N, "infants": N }
session["cabin_class"]    = "economy"
session["invoice_id"]     = reserve_data["data"]["invoice_id"]
```

---

## QUICK START

```bash
# 1. Create project
mkdir flight-booking && cd flight-booking

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install flask requests python-dotenv

# 4. Copy .env with your SkyLink credentials
# (account must have role "api" or "admin")

# 5. Run development server
python app.py
# → http://localhost:5000
```

---

*Generated from SkyLink External API Guide v1.0 · 247Travels · May 2025*
