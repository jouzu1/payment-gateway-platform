# Webhook Event Distribution & Asynchronous Integration

## 1. Subsystem Architecture & Guarantees

In payment systems, synchronous client HTTP responses cannot capture asynchronous clearing events that conclude hours or days later (e.g. 3DS challenge completion, domestic instant rail callbacks, ACH clearing, chargeback notices).

The **Webhook Event Distribution Architecture** guarantees:
- **At-Least-Once Delivery**: No event is ever lost, even during database crashes or network failures, powered by the **Transactional Outbox Pattern** with Debezium Change Data Capture (CDC).
- **Cryptographic Non-Repudiation**: Payloads are signed with HMAC SHA-256 using per-merchant secret keys.
- **Resilience Against Merchant Downtime**: Multi-stage exponential backoff retries spanning 24+ hours.

```
[ Database Transaction ]
  ├── 1. Mutate Financial State (PaymentIntent / Payout / Ledger)
  └── 2. INSERT INTO outbox_events (Atomic Commit in Same DB Tx)
              │
              ▼
    [ Debezium CDC Engine ] (Reads PostgreSQL WAL Logs)
              │
              ▼
    [ Apache Kafka Event Bus ] (Topic: payments.webhooks.dispatch)
              │
              ▼
    [ Distributed Webhook Worker Fleet ]
              │
              ▼
    [ Calculate HMAC-SHA256 Signature ]
              │
              ▼
    [ HTTP POST to Merchant Endpoint ]
              │
       +------+----------------+
       │                       │
   (2xx OK)              (Non-2xx / Timeout)
       │                       │
       ▼                       ▼
  [ DELIVERED ]       [ Exponential Backoff Delay Queue ]
                      (Retries: 15s, 1m, 5m, 30m, 2h, 6h, 12h, 24h)
                               │
                        (After 9 Retries)
                               ▼
                        [ DEAD_LETTER Queue ]
```

---

## 2. CloudEvents v1.0 Envelope Specification

Every webhook payload conforms strictly to the **CloudEvents v1.0** open standard:

```json
{
  "specversion": "1.0",
  "id": "evt_9a41b2c8-8921-4f11-b0e2-192837465abc",
  "source": "https://api.gateway.platform.internal/v1",
  "type": "payment_intent.succeeded",
  "datacontenttype": "application/json",
  "time": "2026-11-10T10:45:00.128Z",
  "subject": "pi_018b1234-7000-8000-9000-000000000001",
  "data": {
    "id": "pi_018b1234-7000-8000-9000-000000000001",
    "amount": 25000,
    "currency": "USD",
    "status": "SUCCEEDED",
    "payment_method": {
      "type": "CARD",
      "card_brand": "VISA",
      "last4": "4242"
    },
    "charge_id": "tx_018b1234-8000-9000-a000-000000000002",
    "created_at": "2026-11-10T10:44:50.000Z"
  }
}
```

---

## 3. Cryptographic Signature Verification (HMAC SHA-256)

Every webhook request contains an `X-Signature` HTTP header:
```http
X-Signature: t=1731235500,v1=5d41402abc4b2a76b9719d911017c5927e1f4bc1b29a8a183574d76f8b9e6912
```

### Signature Computation:
1. The gateway extracts the current epoch timestamp $t$ and the raw request body string.
2. Constructs the signed payload string:
   $$\text{SignedPayload} = t + "." + \text{RawBody}$$
3. Computes the HMAC SHA-256 hash using the merchant's private secret:
   $$\text{Signature} = \text{HMAC-SHA256}(K_{\text{webhook\_secret}}, \text{SignedPayload})$$

### Reference Implementation for Merchants (Node.js):
```javascript
const crypto = require('crypto');

function verifyWebhookSignature(rawBody, signatureHeader, secret) {
  const parts = signatureHeader.split(',');
  const timestamp = parts.find(p => p.startsWith('t=')).split('=')[1];
  const signature = parts.find(p => p.startsWith('v1=')).split('=')[1];

  // 1. Prevent Replay Attacks (tolerance: 5 minutes)
  const currentTime = Math.floor(Date.now() / 1000);
  if (Math.abs(currentTime - parseInt(timestamp, 10)) > 300) {
    throw new Error('Webhook timestamp outside acceptable tolerance window.');
  }

  // 2. Compute Expected HMAC
  const signedPayload = `${timestamp}.${rawBody}`;
  const expectedSignature = crypto
    .createHmac('sha256', secret)
    .update(signedPayload, 'utf8')
    .digest('hex');

  // 3. Constant-time comparison to prevent timing attacks
  const isValid = crypto.timingSafeEqual(
    Buffer.from(signature, 'hex'),
    Buffer.from(expectedSignature, 'hex')
  );

  if (!isValid) {
    throw new Error('Invalid cryptographic signature.');
  }
  return true;
}
```

---

## 4. Exponential Backoff Retry Policy & Dead-Letter Handling

When a merchant endpoint fails to respond with an HTTP `2xx` status code within 5.0 seconds, the delivery scheduler queues retries according to the following policy:

| Attempt Number | Delay Before Execution | Cumulative Time Elapsed |
| :--- | :--- | :--- |
| **Attempt 1** | Immediate (0s) | 0 seconds |
| **Attempt 2** | 15 seconds | 15 seconds |
| **Attempt 3** | 1 minute | 1 minute 15 seconds |
| **Attempt 4** | 5 minutes | 6 minutes 15 seconds |
| **Attempt 5** | 30 minutes | 36 minutes 15 seconds |
| **Attempt 6** | 2 hours | 2 hours 36 minutes |
| **Attempt 7** | 6 hours | 8 hours 36 minutes |
| **Attempt 8** | 12 hours | 20 hours 36 minutes |
| **Attempt 9** | 24 hours | 44 hours 36 minutes |

### Dead-Letter Queue (DLQ) & Manual Replay:
- If all 9 attempts fail, the delivery transitions to `DEAD_LETTER`.
- Merchants receive an operational notification in the Developer Dashboard.
- Merchants can invoke `POST /v1/webhooks/deliveries/{id}/replay` to re-trigger delivery after resolving their infrastructure outage.
