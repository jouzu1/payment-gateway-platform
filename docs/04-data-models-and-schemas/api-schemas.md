# API Schemas, Idempotency Contracts & Rate Limiting

## 1. REST API Design Standards

The platform exposes an enterprise-grade RESTful API designed around the following architectural conventions:
- **Transport**: HTTPS with TLS 1.3 exclusively.
- **Payload Format**: Strict JSON (`application/json`).
- **Dates & Times**: ISO 8601 UTC representation (`YYYY-MM-DDTHH:mm:ss.sssZ`).
- **Monetary Amounts**: Minor currency units as signed 64-bit integers (`int64` / cents).
- **Authentication**: Bearer JWT tokens with tenant and scope claims, or mTLS for high-security banking endpoints.

---

## 2. Standardized Error Response Model

All API errors return a uniform JSON schema adhering to RFC 7807 (Problem Details for HTTP APIs) semantics:

```json
{
  "error": {
    "code": "INSUFFICIENT_FUNDS",
    "message": "The issuing bank declined the transaction due to insufficient available funds.",
    "param": "payment_token",
    "type": "card_error",
    "doc_url": "https://docs.gateway.platform.internal/errors/INSUFFICIENT_FUNDS",
    "request_id": "req_8df72a91-4b1e-4122-8391-7291a82bc192"
  }
}
```

### Unified Error Taxonomy:

| HTTP Status | Unified Error Code | Semantic Description |
| :--- | :--- | :--- |
| **400 Bad Request** | `INVALID_REQUEST_PAYLOAD` | Request body failed JSON schema validation or malformed syntax. |
| **401 Unauthorized** | `AUTHENTICATION_FAILED` | Missing, expired, or cryptographically invalid API key / bearer token. |
| **402 Payment Required**| `CARD_DECLINED` | Issuer refused the charge (insufficient funds, lost/stolen, velocity). |
| **403 Forbidden** | `SANCTIONS_BLOCKED` | AML screening detected a confirmed match against OFAC/UN/PEP lists. |
| **404 Not Found** | `RESOURCE_NOT_FOUND` | Specified entity ID does not exist under the authenticated tenant. |
| **409 Conflict** | `IDEMPOTENCY_IN_FLIGHT` | An identical request is currently processing under this Idempotency-Key. |
| **422 Unprocessable** | `BENEFICIARY_PROBE_FAILED`| Domestic switch returned invalid counterparty account number or name mismatch. |
| **429 Too Many Req** | `RATE_LIMIT_EXCEEDED` | Request rate breached tenant sliding window threshold. |
| **503 Unavailable** | `RAIL_CIRCUIT_OPEN` | External bank rail is degraded; fallbacks exhausted. |

---

## 3. Cursor-Based Pagination Strategy

To ensure deterministic retrieval under high write concurrency, all collection endpoints (`GET /v1/payment_intents`, `GET /v1/payouts`) enforce **Cursor-Based Pagination** rather than naive SQL offset pagination (`OFFSET / LIMIT`):

### Query Parameters:
- `limit`: Number of items to return (integer, default: 20, max: 100).
- `starting_after`: Cursor pointing to the last entity ID observed in the previous page.
- `ending_before`: Cursor for reverse pagination.

### Response Envelope:
```json
{
  "object": "list",
  "data": [
    { "id": "pi_018b1234-abcd-7000-8000-000000000001", "amount": 5000 },
    { "id": "pi_018b1234-abcd-7000-8000-000000000002", "amount": 12000 }
  ],
  "has_more": true,
  "next_cursor": "pi_018b1234-abcd-7000-8000-000000000002"
}
```

---

## 4. Rate Limiting Strategy (Distributed Sliding Window)

To protect downstream core banking systems and prevent abuse, the Envoy edge gateway enforces rate limiting using the **Sliding Window Leaky Bucket** algorithm implemented in Redis.

### Standard Response Headers:
Every API response emits standard rate limiting metadata:
```http
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 942
X-RateLimit-Reset: 1731234580
```

When a tenant breaches their tier ceiling:
```http
HTTP/1.1 429 Too Many Requests
Retry-After: 15
Content-Type: application/json

{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Too many requests. Please wait 15 seconds before retrying."
  }
}
```

---

## 5. Idempotency Framework Contract

Every mutating API call (`POST /v1/payment_intents`, `POST /v1/payouts`, `POST /v1/refunds`) requires an `Idempotency-Key` HTTP header containing a unique UUIDv4.

### Idempotency Lifecycle States:

```
[ Client Request ] ──► [ Check Redis for Key ]
                              │
         +--------------------+--------------------+
         │                                         │
    (Key Found)                               (Key Absent)
         │                                         │
         ▼                                         ▼
+---------------------+                   +---------------------+
| Evaluate State      |                   | SETNX Key = IN_FLIGHT|
+----------+----------+                   | (TTL: 60 Seconds)   |
           │                              +----------+----------+
           ├─► State = IN_FLIGHT  ──► HTTP 409 Conflict  │
           │                                            ▼
           ├─► State = COMMITTED  ──► Return Cached JSON [ Execute Transaction ]
           │                                            │
           └─► State = FAILED     ──► Return Cached Err ▼
                                          +---------------------+
                                          | Update DB & Redis   |
                                          | State = COMMITTED   |
                                          | (TTL: 24 Hours)     |
                                          +---------------------+
```
