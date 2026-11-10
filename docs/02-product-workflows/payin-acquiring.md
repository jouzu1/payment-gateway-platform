# Payin & Acquiring Multi-Tender Architecture

## 1. Domain Overview & Multi-Tender Strategy

Payment acquiring (Payin) encompasses the processing and collection of funds from end-consumers across heterogeneous payment tenders. To maximize conversion while meeting global security and compliance mandates, the platform provides a unified acquiring layer across four distinct payment modalities:
1. **Cards & EMV 3-D Secure (3DS2)**: Global scheme acquiring (Visa, Mastercard, Amex, JCB) with frictionless and step-up challenge authentication.
2. **E-Wallets**: In-app, mobile web, and app-to-app deep-linking (Apple Pay, Google Pay, GrabPay, OVO, GoPay, ShopeePay).
3. **Dynamic QR Code Rails**: EMVCo compliant dynamic QR schemes (QRIS, UPI, Pix, PromptPay).
4. **Direct Debit & Recurring Mandates**: Bank-backed pull debits with digital mandate registration (SEPA Direct Debit, NACHA e-Mandate, UPI Autopay).

---

## 2. Card Acquiring & EMV 3-D Secure 2.2 / 2.3 Architecture

```
[ Payer Browser / App ] ──► [ Hosted Tokenization Vault ] ──► [ 3DS Directory Server ] ──► [ Issuer ACS ] ──► [ Card Acquirer / Scheme ]
  Card Entry (PAN, CVV)       Generates "tok_vault_901"         Evaluates Fraud Risk          3DS Challenge            ISO 8583 Auth Request
```

### 2.1 PCI-DSS Scoped Tokenization
To maintain strict PCI-DSS Level 1 compliance boundaries:
- Raw Card Primary Account Numbers (PAN), CVVs, and expiration dates never touch merchant servers or the gateway core application nodes.
- Cardholder data is submitted directly from client browsers via an iframe hosted inside the isolated **Cardholder Data Environment (CDE)**.
- The CDE vault encrypts the PAN using AES-256-GCM backed by AWS CloudHSM, stores the ciphertext in an isolated database, and returns an ephemeral `PaymentToken` (e.g., `tok_vault_901`).

### 2.2 EMV 3DS2 Authentication Lifecycle (Frictionless vs. Challenge)
1. **Device Fingerprinting & 3DS Method Call**:
   - The platform SDK collects device and browser signals (IP, User-Agent, screen resolution, color depth, timezone offset, language).
   - If supported by the issuer, a hidden 3DS Method iframe is rendered to establish an early session with the Access Control Server (ACS).
2. **Authentication Request (`AReq`)**:
   - The platform sends an `AReq` to the Card Scheme Directory Server (Visa Secure, Mastercard Identity Check).
   - Contains 30+ contextual parameters: transaction amount, currency, merchant category code (MCC), shipping address, and device metadata.
3. **Frictionless Flow Path**:
   - The issuer ACS scores the risk. If the transaction matches normal cardholder behavior, the ACS returns an Authentication Response (`ARes`) with `transStatus = 'Y'`.
   - The issuer generates an Electronic Commerce Indicator (ECI = `05` for Visa, `02` for Mastercard) and a cryptographic Cardholder Authentication Verification Value (CAVV/AAV).
   - The transaction proceeds immediately to authorization without prompting the user.
4. **Challenge Flow Path**:
   - If the ACS requires verification, it returns `ARes` with `transStatus = 'C'`.
   - The gateway SDK renders the bank's OTP / Biometric authentication dialog inside a secure webview or modal.
   - Upon completion, the ACS issues a Challenge Response (`CRes`) confirming successful authentication.

### 2.3 Dual-Message (Auth/Capture) vs. Single-Message (Sale)
- **Single-Message (Sale / Purchase)**:
  - Authorization and capture occur in a single atomic step. Used for digital goods, food delivery, and immediate service fulfillment.
- **Dual-Message (Authorize + Delayed Capture)**:
  - Step 1 (`Authorize`): Reserves the transaction amount against the customer's credit limit; returns an `auth_code`. The hold is valid for 7 days.
  - Step 2 (`Capture`): Finalizes the charge upon warehouse fulfillment or physical goods shipment. Supports full or partial capture ($\text{CaptureAmount} \le \text{AuthorizedAmount}$).
  - Step 3 (`Cancel / Void`): Releases the authorization hold without incurring interchange fees if the order is cancelled prior to capture.

---

## 3. Alternative Payment Methods (APMs)

### 3.1 E-Wallets (Apple Pay, Google Pay, Regional Super-Apps)
- **Tokenized Wallet Passes (Apple Pay / Google Pay)**:
  - The client device decrypts Apple Pay PKPaymentToken or Google Pay PaymentData using our public EC key.
  - Gateway passes the network token and dynamic cryptogram directly to the card acquirer without requiring 3DS step-up.
- **App-to-App Deep-Linking (Regional Wallets)**:
  - Gateway generates an ephemeral universal deep-link (e.g. `gopay://pay?token=...`).
  - Mobile client launches the wallet app for biometric authentication.
  - Wallet provider posts an asynchronous webhook confirming capture.

### 3.2 Dynamic QR Code Rails (EMVCo, QRIS, UPI, Pix)
The gateway generates dynamic Merchant-Presented Mode EMVCo QR code payloads containing TLV (Tag-Length-Value) encoded strings:

```
00020101021226600016ID.CO.QRIS.WWW0118936000000000000000021500000000000000520454115303360540850000.005802ID5914PAYMENT GATEWAY6007JAKARTA62190115ORDER-8271039916304D1B8
```

#### EMVCo TLV Field Specifications:
- `Tag 00` (Payload Format Indicator): `"01"`
- `Tag 01` (Point of Initiation Method): `"12"` (Dynamic: amount embedded, single-use)
- `Tag 52` (Merchant Category Code): `"5411"` (Grocery / Retail)
- `Tag 53` (Transaction Currency): `"360"` (IDR) or `"356"` (INR)
- `Tag 54` (Transaction Amount): `"50000.00"`
- `Tag 58` (Country Code): `"ID"`
- `Tag 62` (Additional Data - Invoice Ref): `"ORDER-827103991"`
- `Tag 63` (CRC16 Checksum): 4-character uppercase hexadecimal computed via polynomial `0x1021`.

### 3.3 Direct Debit & Recurring Mandates
- **Phase 1: Mandate Registration**:
  - Customer signs a digital mandate agreement (e.g. SEPA Mandate, UPI Autopay).
  - The bank issues a registered `MandateReference` (e.g. Unique Mandate Reference - UMR).
- **Phase 2: Scheduled Debit Execution**:
  - The platform dispatches an ISO 20022 `pain.008` (Direct Debit Initiation) 2 banking days prior to payment due date.
- **Phase 3: Return Window & Dispute Monitoring**:
  - Direct debits are subject to statutory return windows (up to 8 weeks for unauthorized debits under SEPA).
  - Returns (NACHA R01-R04, SEPA AC04/MS03) automatically transition transactions to `REVERSED` and trigger ledger chargebacks.

---

## 4. Checkout Session & Payin State Machine

```
               +-----------------------+
               |        CREATED        |
               +-----------+-----------+
                           | select_payment_method()
               +-----------v-----------+
               |    REQUIRES_ACTION    | (3DS Challenge / QR Display / Wallet Redirect)
               +-----------+-----------+
                           | customer_completed_action()
               +-----------v-----------+
               |      PROCESSING       |
               +-----------+-----------+
              +------------+------------+
 (Auth OK)    |                         | (Declined / Timeout)
+-------------v---------+     +---------v-------------+
|      AUTHORIZED       |     |        FAILED         | (Terminal)
+-------------+---------+     +-----------------------+
              | capture() [Within 7 Days]
+-------------v---------+
|       CAPTURED        |
+-------------+---------+
              | settlement_sweep()
+-------------v---------+
|        SETTLED        |
+-------------+---------+
       +------+----------------+
       | dispute_lost()        | refund_issued()
+------v---------+      +------v---------+
|  CHARGEBACK    |      |    REFUNDED    | (Terminal)
+----------------+      +----------------+
```

---

## 5. Webhook Event Distribution Architecture

Merchant systems rely on webhooks to receive real-time notifications of payment state transitions. To guarantee delivery across merchant infrastructure outages:

### 5.1 Transactional Outbox Pattern
Within the exact same database transaction that updates `payment_intents` and commits ledger legs, an outbox record is inserted into `outbox_events`. Debezium CDC captures the transaction from PostgreSQL WAL logs and streams it to Kafka, guaranteeing **zero dropped events**.

### 5.2 Cryptographic HMAC SHA-256 Signing
Every webhook request is signed using the merchant's secret key (`webhook_secret`):
```http
POST /api/webhooks HTTP/1.1
Host: merchant.com
Content-Type: application/json
X-Gateway-Signature: t=1731234567,v1=9b3a6c9e0123456789abcdef0123456789abcdef0123456789abcdef01234567
```
Calculation formula:
$$\text{Signature} = \text{HMAC-SHA256}(K_{\text{secret}}, t + "." + \text{RawPayload})$$
Merchants verify the signature to prevent tampering and reject requests where $|now() - t| > 300\text{ seconds}$ to eliminate replay attacks.

### 5.3 Exponential Backoff Retry Schedule
If the merchant server returns a non-2xx HTTP code or times out ($> 5\text{s}$), the webhook worker retries using the following deterministic schedule:

$$\text{Retry Intervals}: [0\text{s}, 15\text{s}, 1\text{m}, 5\text{m}, 30\text{m}, 2\text{h}, 6\text{h}, 12\text{h}, 24\text{h}]$$

If all 9 attempts fail, the webhook delivery transitions to `DEAD_LETTER` and generates an operational alert in the merchant dashboard.
