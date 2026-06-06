# SkyLink Flight Reservation Website

## Prerequisites
- Python 3.11+
- pip

## Installation
```bash
pip install -r requirements.txt
```

## Configuration
Copy `.env.example` to `.env` and fill credentials.

## Running locally
```bash
python app.py
```

## Booking flow walkthrough
1. Search flights
2. Select and price flight
3. Reserve with traveller details
4. Pay and wait for PNR confirmation

## How token refresh works
The app stores access/refresh tokens in memory with expiry and proactively refreshes 60 seconds before expiry.

## Known limitations
- UI uses generic field mapping because supplier response structure can vary.
- No database persistence; session-based flow only.
