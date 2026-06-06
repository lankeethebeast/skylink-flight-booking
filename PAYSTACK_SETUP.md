# Paystack Payment Integration Setup

This guide walks through setting up Paystack payments in the SkyLink Flight Booking application.

## Overview

The application now includes full Paystack integration for processing flight booking payments. Features include:

- **Payment Initialization**: Initialize Paystack payments with customer email
- **Payment Verification**: Verify payment status after customer returns from Paystack
- **Webhook Support**: Optional webhook for real-time payment notifications
- **Local Invoice IDs**: Generates invoice IDs locally if SkyLink API doesn't provide them
- **Session Management**: Tracks payment status throughout the booking flow

## Prerequisites

1. A Paystack account (create at https://paystack.com)
2. Your Paystack API keys from the dashboard
3. Environment variables configured in `.env`

## Step 1: Create Paystack Account

1. Go to https://paystack.com and sign up
2. Complete email verification and account setup
3. In Dashboard → Settings → Developers, copy your keys:
   - **Public Key** (starts with `pk_`)
   - **Secret Key** (starts with `sk_`)

> ⚠️ **Important**: Use test keys for development (`pk_test_*`, `sk_test_*`). Switch to live keys only in production.

## Step 2: Configure Environment Variables

Update `.env` file in the project root:

```bash
# Paystack Payment Configuration
PAYSTACK_PUBLIC_KEY=pk_test_YOUR_PUBLIC_KEY_HERE
PAYSTACK_SECRET_KEY=sk_test_YOUR_SECRET_KEY_HERE
```

### Example with Test Keys

```bash
PAYSTACK_PUBLIC_KEY=pk_test_51234567890abcdefghijk
PAYSTACK_SECRET_KEY=sk_test_51234567890abcdefghijk
```

## Step 3: API Endpoints

The application provides these payment-related endpoints:

### Initialize Payment

**POST** `/payment/paystack`

```bash
curl -X POST http://localhost:5000/payment/paystack \
  -d "email=customer@example.com"
```

**Response:**
```json
{
  "status": true,
  "authorization_url": "https://checkout.paystack.com/...",
  "access_code": "acc_...",
  "reference": "INV-...",
  "callback_url": "http://localhost:5000/payment/callback?reference=..."
}
```

### Verify Payment

**GET** `/payment/paystack/verify?reference=REFERENCE`

Returns payment status and details:
```json
{
  "status": true,
  "message": "Payment successful",
  "reference": "INV-abc123",
  "amount": 100000,
  "paid_at": "2024-06-03T10:30:00.000Z"
}
```

### Webhook Endpoint

**POST** `/payment/paystack/webhook`

Receives payment notifications from Paystack. Signature verification is performed.

## Step 4: Testing Payments

### Test Payment Flow

1. Start the application: `python app.py`
2. Book a flight and proceed to confirmation
3. Click "💳 Pay with Paystack" button
4. Enter test email (any email format)
5. You'll be redirected to Paystack test checkout

### Test Card Details

Paystack provides test cards for development:

| Card Number | Expiry | CVC | Auth Method |
|-------------|--------|-----|-------------|
| 4111111111111111 | Any future date | Any 3 digits | PIN: 1234 |
| 5531886652142540 | Any future date | Any 3 digits | PIN: 1234 |
| 507786 (Visa, bin only) | Any future date | Any 3 digits | PIN: 1234 |

**For testing OTP:**
- Enter any 6-digit number when prompted for OTP

### Successful Payment

1. After entering card details and OTP, you'll see confirmation
2. You'll be redirected to `/payment/callback`
3. Payment verification is automatic
4. You're returned to `/confirm` with updated payment status

## Step 5: Webhook Setup (Optional but Recommended)

Set up webhooks for real-time payment confirmations:

1. Go to Dashboard → Settings → Developers
2. Click "Webhooks" tab
3. Add your webhook URL: `https://yourdomain.com/payment/paystack/webhook`
4. Select events to monitor: `charge.success`, `charge.failed`
5. Save

The webhook endpoint validates signatures using your secret key and logs payment events.

## Database Integration (Optional)

To persist payment data, you can:

1. Store invoice IDs and payment references in a database
2. Track payment status in user accounts
3. Associate payments with bookings

Example storage structure:

```python
class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.String(50), unique=True)
    reference = db.Column(db.String(100))
    email = db.Column(db.String(120))
    amount = db.Column(db.Integer)  # In kobo/cents
    status = db.Column(db.String(20))  # pending, paid, failed
    paid_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
```

## Troubleshooting

### Keys Not Working

**Error**: `Paystack not configured`
- **Fix**: Ensure `PAYSTACK_SECRET_KEY` is set in `.env`
- Restart Flask app after updating `.env`

### Payment Verification Fails

**Error**: `Payment verification failed`
- Check if the reference is correct
- Verify Paystack API is accessible (check internet connection)
- Ensure secret key is correct

### Webhook Not Receiving Events

- Check webhook URL is publicly accessible
- Verify signature validation isn't rejecting valid requests
- Check Paystack dashboard for webhook delivery logs

### Modal Not Appearing

**Fix**: Ensure Paystack scripts are loaded:
```html
<script src="https://js.paystack.co/v1/inline.js"></script>
```

## Production Deployment

### Before Going Live

1. **Switch to Live Keys**
   - Update `.env` with live keys (`pk_live_*`, `sk_live_*`)
   - Test with real transactions

2. **Enable HTTPS**
   - Webhook signatures require secure connections
   - Redirect all HTTP to HTTPS

3. **Secure Secrets**
   - Use environment variables, never commit keys to git
   - Use `.env.local` for sensitive data
   - Consider secrets management tools (AWS Secrets Manager, HashiCorp Vault)

4. **Set Up Monitoring**
   - Log payment events for auditing
   - Set up alerts for failed payments
   - Monitor webhook delivery

5. **Test Payment Flow**
   - Complete end-to-end payment tests
   - Verify PNR is issued after payment
   - Test error scenarios

### Environment Variables for Production

```bash
PAYSTACK_PUBLIC_KEY=pk_live_YOUR_LIVE_PUBLIC_KEY
PAYSTACK_SECRET_KEY=sk_live_YOUR_LIVE_SECRET_KEY
FLASK_DEBUG=false
TEST_BYPASS_MODE=false
```

## File Structure

```
skylink_app/
├── app.py                           # Main Flask app with payment routes
├── templates/
│   ├── confirmation.html            # Booking confirmation with payment button
│   └── payment_callback.html        # Payment verification page
└── skylink_client.py                # SkyLink API client
```

## Integration Flow Diagram

```
User Books Flight
    ↓
Enter Passenger Details
    ↓
Reserve Flight (SkyLink API)
    ↓
Confirmation Page
    ↓
[Click "Pay with Paystack"]
    ↓
Payment Modal (Enter Email)
    ↓
/payment/paystack (Initialize)
    ↓
Paystack Checkout
    ↓
Customer Enters Card Details
    ↓
Paystack Redirects to /payment/callback
    ↓
/payment/paystack/verify (Async)
    ↓
Update Session with Payment Status
    ↓
Redirect to /confirm with Updated Status
```

## Code Examples

### Initialize Payment (JavaScript)

```javascript
async function initializePaystackPayment(email) {
  const res = await fetch('/payment/paystack', {
    method: 'POST',
    body: new FormData(form)
  });
  const data = await res.json();
  window.location.href = data.authorization_url;
}
```

### Verify Payment (JavaScript)

```javascript
async function verifyPayment(reference) {
  const res = await fetch(`/payment/paystack/verify?reference=${reference}`);
  const data = await res.json();
  if (data.status) {
    console.log('Payment successful:', data.amount);
  }
}
```

### Handle Webhook (Python)

```python
@app.route("/payment/paystack/webhook", methods=["POST"])
def paystack_webhook():
    event = request.json
    if event.get("event") == "charge.success":
        reference = event["data"]["reference"]
        # Update your database
        mark_payment_as_complete(reference)
    return jsonify({"status": "ok"}), 200
```

## Support

For issues with:
- **Paystack Integration**: Contact Paystack Support (https://paystack.com/support)
- **App Integration**: Check Flask/Python documentation
- **Test Cards**: See Paystack docs at https://paystack.com/docs/payments/test-authentication

## References

- [Paystack API Documentation](https://paystack.com/docs/api/)
- [Paystack Integration Guide](https://paystack.com/docs/payments/integration-guide/)
- [Paystack Test Cards](https://paystack.com/docs/payments/test-authentication/)
- [Paystack Webhooks](https://paystack.com/docs/webhooks/)
