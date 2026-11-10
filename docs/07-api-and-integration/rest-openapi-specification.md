# REST OpenAPI 3.1 Specification Documentation

## 1. Overview & Interface Scope

The external REST API provides a standardized interface for merchants, financial institutions, and platforms to integrate payment acquiring, domestic instant remittance, guaranteed FX cross-border payouts, P2P lending escrow, and double-entry ledger queries.

The complete machine-readable specification is authored in OpenAPI 3.1.0 standard and located at:
`schemas/openapi.yaml`

---

## 2. Core Endpoint Catalog

### 2.1 Payment Acquiring & Payin Endpoints

| Method | Endpoint Path | Summary | Key Request Parameters | Primary Responses |
| :--- | :--- | :--- | :--- | :--- |
| `POST` | `/v1/payment_intents` | Create Payment Intent | `amount`, `currency`, `allowed_methods`, `capture_method` | `201 Created` (`PaymentIntent`), `409 Conflict` |
| `GET` | `/v1/payment_intents` | List Payment Intents | `limit`, `starting_after`, `status` | `200 OK` (Paginated list) |
| `GET` | `/v1/payment_intents/{id}`| Retrieve Payment Intent | `id` (path) | `200 OK` (`PaymentIntent`), `404 Not Found` |
| `POST` | `/v1/payment_intents/{id}/confirm` | Confirm Intent & Authorize | `payment_token`, `payment_method_id`, `return_url` | `200 OK`, `402 Declined` |
| `POST` | `/v1/payment_intents/{id}/capture` | Capture Authorized Intent | `amount_to_capture` (optional partial capture) | `200 OK` (`PaymentIntent`) |
| `POST` | `/v1/payment_intents/{id}/cancel` | Cancel / Void Authorization | `id` (path) | `200 OK` (`PaymentIntent`) |
| `POST` | `/v1/refunds` | Create Refund | `transaction_id`, `amount`, `currency`, `reason` | `201 Created` (`Refund`) |

### 2.2 Local & Cross-Border Remittance Endpoints

| Method | Endpoint Path | Summary | Key Request Parameters | Primary Responses |
| :--- | :--- | :--- | :--- | :--- |
| `POST` | `/v1/payouts/validate-beneficiary` | Probe Beneficiary Account | `bank_code`, `account_number` | `200 OK` (`BeneficiaryValidationResult`) |
| `POST` | `/v1/payouts/quotes` | Lock Guaranteed FX Quote | `base_currency`, `target_currency`, `base_amount` | `201 Created` (`FXQuote` with 60s TTL) |
| `POST` | `/v1/payouts` | Dispatch Payout Order | `payout_type`, `source_amount`, `beneficiary_details` | `201 Created` (`PayoutOrder`), `403 Sanction` |
| `GET` | `/v1/payouts/{id}` | Get Payout Status | `id` (path) | `200 OK` (`PayoutOrder`) |

### 2.3 P2P Lending & Escrow Endpoints

| Method | Endpoint Path | Summary | Key Request Parameters | Primary Responses |
| :--- | :--- | :--- | :--- | :--- |
| `POST` | `/v1/p2p/loans` | Originate Loan Contract | `borrower_id`, `principal_amount`, `interest_rate` | `201 Created` (`LoanAccount`) |
| `POST` | `/v1/p2p/loans/{id}/disburse` | Disburse from Escrow | `id` (path), `Idempotency-Key` | `200 OK` (`LoanAccount` status `DISBURSED`) |
| `POST` | `/v1/p2p/loans/{id}/repay` | Process Repayment & Waterfall | `installment_sequence`, `collection_amount` | `200 OK` (`RepaymentWaterfallResult`) |

### 2.4 Double-Entry Ledger Endpoints

| Method | Endpoint Path | Summary | Key Request Parameters | Primary Responses |
| :--- | :--- | :--- | :--- | :--- |
| `POST` | `/v1/ledger/accounts` | Create Ledger Account | `account_number`, `category`, `currency` | `201 Created` (`LedgerAccount`) |
| `GET` | `/v1/ledger/accounts/{id}/balance` | Real-Time Account Balance | `id` (path) | `200 OK` (`AccountBalanceSnapshot`) |
| `POST` | `/v1/ledger/journals` | Post Balanced Journal Entry | `correlation_id`, `transaction_type`, `entry_legs` | `201 Created`, `422 Balance Mismatch` |

---

## 3. Request & Response Examples

### 3.1 Creating a PaymentIntent
```http
POST /v1/payment_intents HTTP/1.1
Host: api.gateway.platform.internal
Authorization: Bearer sec_live_9a8f12c4901b
Idempotency-Key: e8a912c4-7b12-4012-912a-89a712c9b401
Content-Type: application/json

{
  "amount": 25000,
  "currency": "USD",
  "capture_method": "AUTOMATIC",
  "allowed_payment_methods": ["CARD", "VA_BCA", "QRIS"],
  "description": "Order #9042 - Mechanical Keyboard"
}
```

#### Response (`201 Created`):
```json
{
  "id": "pi_018b1234-7000-8000-9000-000000000001",
  "amount": 25000,
  "currency": "USD",
  "status": "REQUIRES_PAYMENT_METHOD",
  "client_secret": "pi_sec_8df72b9a7f",
  "capture_method": "AUTOMATIC",
  "allowed_payment_methods": ["CARD", "VA_BCA", "QRIS"],
  "created_at": "2026-11-10T10:45:00.000Z"
}
```

### 3.2 Locking a Guaranteed FX Quote
```http
POST /v1/payouts/quotes HTTP/1.1
Host: api.gateway.platform.internal
Authorization: Bearer sec_live_9a8f12c4901b
Content-Type: application/json

{
  "base_currency": "USD",
  "target_currency": "IDR",
  "base_amount": 100000
}
```

#### Response (`201 Created`):
```json
{
  "id": "quote_018b9012-4000-8000-a000-000000000099",
  "base_currency": "USD",
  "target_currency": "IDR",
  "base_amount": 100000,
  "target_amount": 1565025000,
  "exchange_rate": 15650.25000000,
  "expires_at": "2026-11-10T10:46:00.000Z"
}
```
