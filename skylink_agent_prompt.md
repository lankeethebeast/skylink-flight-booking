# SkyLink Flight Reservation Website — Python Agent Prompt

## Mission
Build a complete flight reservation website in Python that integrates with the SkyLink API
(base URL: https://247travels.com/api/). The app must follow the exact 4-step booking workflow
documented in the SkyLink External Integration Guide v1.0.

---

## Tech Stack
- **Backend**: Python 3.11+ with Flask (or FastAPI if you prefer async)
- **HTTP client**: `httpx` (async) or `requests` (sync)
- **Frontend**: Jinja2 templates (HTML/CSS/vanilla JS) — no heavy frontend framework needed
- **Session storage**: Flask sessions (server-side, signed cookie)
- **Environment config**: `python-dotenv` — store API credentials in `.env`

---

## Project Structure

```
skylink_app/
├── app.py                  # Flask app, routes, session logic
├── skylink_client.py       # All SkyLink API calls (one function per endpoint)
├── templates/
│   ├── base.html           # Layout, nav, flash messages
│   ├── index.html          # Search form
│   ├── results.html        # Flight results list
│   ├── review.html         # Passenger details form
│   ├── payment.html        # Redirect to Paystack + polling UI
│   └── confirmation.html   # PNR display
├── static/
│   └── style.css
├── .env                    # SKYLINK_EMAIL, SKYLINK_PASSWORD
├── requirements.txt
└── README.md
```

---

## Environment Variables (.env)

```
SKYLINK_EMAIL=your_api_account@example.com
SKYLINK_PASSWORD=your_password
FLASK_SECRET_KEY=a-long-random-string
```

---

## skylink_client.py — Implement These Functions Exactly

Build one function per SkyLink endpoint. Each function must:
1. Accept the exact parameters listed below
2. Make the HTTP request with the JWT token in the `Authorization: Bearer <token>` header
3. Return the full parsed JSON response dict
4. Raise a descriptive exception on non-200 status (include HTTP code + message + error fields)

### Function signatures

```python
def login(email: str, password: str) -> dict:
    """POST /api/login — returns full response including access_token and refresh_token"""

def search_flights(token: str, search_mode: str, from_code: str, to_code: str,
                   departure_date: str, flight_type: str, adults: int,
                   children: int = 0, infants: int = 0,
                   cabin_class: str = "economy", currency: str = "NGN",
                   return_date: str = None) -> dict:
    """POST /api/flights/search — returns flights list, each with booking_token"""

def price_flight(token: str, booking_token: str, adults: int,
                 children: int = 0, infants: int = 0,
                 currency: str = "NGN", cabin_class: str = "economy") -> dict:
    """POST /api/flights/pricing — ALWAYS call before reserve.
       Returns updated booking_token — use THIS token for reserve, not the search token."""

def reserve_flight(token: str, booking_token: str, travellers: dict,
                   passengers: dict, currency: str = "NGN",
                   ticket_time_limit_hours: int = 48) -> dict:
    """POST /api/flights/reserve — returns payment_url, invoice_id, payment_reference"""

def poll_booking_status(token: str, invoice_id: str) -> dict:
    """GET /api/flights/booking/status/{invoice_id}
       Returns has_pnr, pnr, booking_status, payment_status, is_paid, error"""
```

---

## travellers Object Format (for reserve_flight)

```python
travellers = {
    "primary_guest": {
        "title": "Mr",              # Mr | Mrs | Ms | Miss | Dr | Prof
        "first_name": "John",
        "last_name": "Doe",
        "other_name": "",
        "email": "john@example.com",  # Required for primary_guest
        "phone": "08012345678",       # Required for primary_guest, digits only
        "country_code": "234",        # Dialling code digits only
        "dob": "1990-01-01",          # YYYY-MM-DD
        "gender": "male",             # male | female
        "passport_number": "A12345678",
        "passport_expiry": "2030-01-01",
        "passport_issue_date": "2020-01-01",
        "nationality": "NG"           # 2-letter ISO country code
    },
    "travelers": {
        "adult_0": { ...same fields as primary_guest... },
        # adult_0 MUST always mirror primary_guest — do not omit it
        # Additional adults: adult_1, adult_2 ...
        # Children: child_0, child_1 ...
        # Infants: infant_0, infant_1 ...
    }
}
```

---

## app.py — Routes and Workflow (implement all of these)

### Token management
- On startup (or first request), call `login()` and store `access_token` and `refresh_token`
  in Flask's app context or a module-level dict with an `expires_at` timestamp.
- Before every API call, check if `access_token` is within 60 seconds of expiry.
  If so, call `POST /api/token/refresh` (or re-login) to get a fresh token.
- Never hard-code the token — always fetch/refresh dynamically.

### Routes

```
GET  /                    → render search form (index.html)
POST /search              → call search_flights(), store results in session, render results.html
POST /select              → receive selected booking_token from results page,
                            call price_flight() immediately, store pricing response in session,
                            render review.html (passenger form)
POST /reserve             → build travellers dict from form, call reserve_flight(),
                            store invoice_id + payment_url in session, render payment.html
GET  /payment             → render payment.html with payment_url and invoice_id
GET  /status              → JSON endpoint: poll poll_booking_status(), return JSON
                            (called by frontend JS every 7 seconds)
GET  /confirmation        → render confirmation.html with PNR data from session
```

### Session keys to store
```python
session['search_params']        # original search form values (for re-search)
session['flight_results']       # list of flights from search response
session['selected_token']       # booking_token chosen by user (from search)
session['pricing_response']     # full pricing response (use THIS token for reserve)
session['invoice_id']           # from reserve response
session['payment_url']          # Paystack checkout URL
session['pnr_data']             # final PNR data from status poll
```

---

## Frontend Behaviour

### index.html — Search Form
Fields: Origin (IATA), Destination (IATA), Departure Date, Return Date (shown only for roundtrip),
Trip Type (oneway/roundtrip/multicity), Adults (1–9), Children, Infants, Cabin Class, Currency.
Validate: infants ≤ adults, adults ≥ 1.

### results.html — Flight Results
Display for each flight: airline name + code, flight number, departure/arrival times, duration,
cabin class, baggage allowance, price + currency, a "Select" button.
Show a warning if `price_changed: true` comes back from pricing.
Show a warning if `cabin_class_shifted: true` or `class_letter_changed: true`.

### review.html — Passenger Form
Render one passenger form section per passenger (adults, children, infants).
The first adult form is always the primary_guest / adult_0.
All passport fields are required. Date fields use `type="date"`.
Show the flight summary (route, date, price) at the top.

### payment.html — Payment & Polling
1. Show the invoice ID, amount, and currency.
2. Display a prominent "Pay Now" button that opens `payment_url` in a new tab.
3. After user clicks Pay Now, start polling `GET /status?invoice_id=<id>` every 7 seconds.
4. Show a spinner and "Waiting for payment confirmation…" message.
5. On `has_pnr: true`, store PNR data and redirect to `/confirmation`.
6. On `booking_status` error, show the error message and a "Search again" button.
7. Stop polling after 5 minutes if no PNR received — show timeout message.

### confirmation.html
Display: PNR code (large, prominent), invoice ID, flight details, passenger name(s),
payment status, and a "Download / Print" button (browser print dialog).

---

## Error Handling Requirements

- **401**: token expired — auto-refresh and retry the request once
- **403 with `blocked: true`**: display carrier-specific block message from `message` field
- **502**: show "Supplier temporarily unavailable. Please search again." with a retry button
- **400**: show field-level validation errors if available
- **Search result expiry**: if user takes > 10 minutes on results page, show a banner:
  "Prices may have changed. Please search again." with a re-search button using stored `search_params`.
- All unhandled exceptions must render a user-friendly error page (no raw tracebacks in production).

---

## Rate Limit Awareness

| Endpoint         | Limit         |
|------------------|---------------|
| /flights/search  | 60 req/min    |
| /flights/pricing | 30 req/min    |
| /flights/reserve | 10 req/min    |
| /login           | 10 req/min (per IP) |

- Add a `time.sleep(1)` guard before retrying any failed request.
- Do not call `/pricing` or `/reserve` in loops.

---

## requirements.txt

```
flask>=3.0
httpx>=0.27
python-dotenv>=1.0
jinja2>=3.1
```

---

## README.md — Include These Sections

1. Prerequisites (Python 3.11+, pip)
2. Installation (`pip install -r requirements.txt`)
3. Configuration (copy `.env.example`, fill in credentials)
4. Running locally (`flask run` or `python app.py`)
5. Booking flow walkthrough (4 steps)
6. How token refresh works
7. Known limitations

---

## Critical Implementation Rules (do not deviate)

1. **Never skip pricing** — always call `/flights/pricing` between search and reserve.
2. **Always use the pricing token** for reserve — not the search token.
3. **Never reuse a booking_token** after a successful reserve call.
4. **Store invoice_id before redirecting** to payment_url.
5. **Poll status** every 5–10 seconds, max 5 minutes, stop on `has_pnr: true` or error.
6. **adult_0 must always mirror primary_guest** in the travellers object.
7. **All API calls over HTTPS only** — the base URL is https://247travels.com/api/
8. **Refresh the access_token proactively** — it expires in 900 seconds (15 minutes).
9. **Handle PNR restriction (403)** gracefully — show the `message` field to the user.
10. **Passport fields are mandatory** — validate all 12 traveller fields on the form before submitting.

---

*Generated from SkyLink External Integration Guide v1.0 — May 2025*
*Base URL: https://247travels.cloud/api/*
