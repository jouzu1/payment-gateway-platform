# Domain Boundaries, Bounded Contexts & Event Catalog

## 1. Domain-Driven Design (DDD) Decomposition

To prevent monolithic entanglement and allow independent scaling across disparate financial protocols, the Payment Gateway Platform is decomposed into seven strictly isolated **Bounded Contexts**. Each context maintains its own ubiquitous language, domain models, persistence schemas, and lifecycle rules.

```
+------------------------------------------------------------------------------------------------+
|                                    DOMAIN CONTEXT MAP                                          |
|                                                                                                |
|   +-----------------------+                    +------------------------+                      |
|   |   Acquiring / Payin   |                    | Disbursement / Remit   |                      |
|   |       [Upstream]      |                    |       [Upstream]       |                      |
|   +-----------+-----------+                    +-----------+------------+                      |
|               |                                            |                                   |
|               | (PaymentAuthorized / Captured)             | (PayoutInitiated / Settled)       |
|               v                                            v                                   |
|   +---------------------------------------------------------------------+                      |
|   |                  Product Orchestration Layer (DAG)                  |                      |
|   +-------+--------------------+---------------------+------------------+                      |
|           |                    |                     |                                         |
|           v                    v                     v                                         |
|   +---------------+    +---------------+     +---------------+                                 |
|   | Risk & Fraud  |    | Treasury & FX |     |  P2P Lending  |                                 |
|   | [Downstream]  |    | [Downstream]  |     |  [Downstream] |                                 |
|   +-------+-------+    +-------+-------+     +-------+-------+                                 |
|           |                    |                     |                                         |
|           +--------------------+---------------------+                                         |
|                                |                                                               |
|                                v                                                               |
|   +---------------------------------------------------------------------+                      |
|   |                     Immutable Double-Entry Ledger                   |                      |
|   |                   [Core Subsystem / Single Source of Truth]         |                      |
|   +------------------------------------+--------------------------------+                      |
|                                        |                                                       |
|                                        v                                                       |
|   +---------------------------------------------------------------------+                      |
|   |                  Settlement & Reconciliation Engine                 |                      |
|   |                     [Downstream Auditing & Clearing]                |                      |
|   +---------------------------------------------------------------------+                      |
+------------------------------------------------------------------------------------------------+
```

---

## 2. The Seven Bounded Contexts

### 2.1 Acquiring / Payin Context
- **Ubiquitous Language**: `PaymentIntent`, `PaymentMethod`, `Customer`, `TokenVault`, `Authorization`, `Capture`, `Refund`, `Dispute`.
- **Primary Responsibility**: Manages the customer-facing checkout journey, PCI-DSS Level 1 tokenization, EMV 3DS2 frictionless/challenge authentication, card acquiring, and alternative payment method (APM) processing (E-Wallets, Dynamic QRIS/UPI, Direct Debit mandates).
- **Invariants**:
  - A `PaymentIntent` cannot be captured unless an authorization cryptogram exists and is in state `AUTHORIZED`.
  - Capture amount cannot exceed authorized amount ($\text{CaptureAmount} \le \text{AuthorizedAmount}$).
  - All tokenized PANs must be cryptographically salted and hashed before persistence.

### 2.2 Disbursement / Remittance Context
- **Ubiquitous Language**: `PayoutOrder`, `BeneficiaryProbe`, `EndToEndId`, `ClearingWindow`, `CutOffTime`, `TransitAccount`.
- **Primary Responsibility**: Manages domestic instant transfers (FedNow, BI-FAST, Faster Payments), ACH batch files (NACHA, ISO 20022 `pain.001`), counterparty account probing, and domestic switch interactions.
- **Invariants**:
  - Beneficiary probe verification is required prior to high-value dispatches.
  - No payout can be dispatched across external banking switches without an atomic reservation hold placed on the merchant's ledger balance.

### 2.3 Treasury & Foreign Exchange (FX) Context
- **Ubiquitous Language**: `CurrencyPair`, `FXQuote`, `LiquidityProvider`, `SpreadBps`, `NostroAccount`, `VostroAccount`, `SpotHedge`.
- **Primary Responsibility**: Ingests real-time rate feeds from institutional liquidity providers, calculates spread markups based on tenant tier, locks guaranteed FX quotes with deterministic TTLs (60 seconds), and monitors multi-currency nostro/vostro pool balances.
- **Invariants**:
  - Guaranteed quote rates are immutable for the duration of their TTL ($t < t_{\text{expires}}$).
  - Outbound cross-border orders cannot proceed if target nostro pool liquidity is insufficient.

### 2.4 P2P Lending Context
- **Ubiquitous Language**: `LoanAccount`, `Borrower`, `InvestorPool`, `EscrowFBOAccount`, `InstallmentSchedule`, `WaterfallSplit`.
- **Primary Responsibility**: Enforces regulatory segregation of peer-to-peer lending capital. Coordinates loan origination, escrow capital holding at custodian banks, borrower disbursement, automated repayment collection, and mathematical waterfall split distributions to fractional lenders.
- **Invariants**:
  - Platform operating capital must NEVER co-mingle with Investor/Borrower Escrow FBO balances.
  - Loan disbursement requires 100% committed investor capital ($\sum \text{InvestorCommitment} \equiv \text{LoanPrincipal}$).
  - Repayment distributions strictly obey statutory priority: Penalties $\rightarrow$ Servicing Fee $\rightarrow$ Withholding Tax $\rightarrow$ Principal $\rightarrow$ Net Interest.

### 2.5 Double-Entry Ledger Context
- **Ubiquitous Language**: `ChartOfAccounts`, `JournalEntry`, `EntryLeg`, `ClearedBalance`, `PendingBalance`, `HoldReservation`.
- **Primary Responsibility**: Immutably records all financial value movements across the enterprise. Acts as the ultimate single source of truth for all balances.
- **Invariants**:
  - Fundamental Equation: $\sum \text{Debits} \equiv \sum \text{Credits}$ for every individual `JournalEntry`.
  - Zero Mutations: Balances are append-only; update and delete operations are rejected at the database trigger level.
  - Minor Currency Units: All monetary values are strictly unsigned 64-bit integers (`BIGINT`).

### 2.6 Risk, Fraud & AML/Sanctions Context
- **Ubiquitous Language**: `SanctionsScreening`, `FuzzyMatchScore`, `SDNList`, `PEPDomain`, `3DSRiskScore`, `DeviceFingerprint`.
- **Primary Responsibility**: Performs real-time screening against OFAC, UN, EU, and PEP watchlists; evaluates device fingerprints and behavioral signals for checkout fraud; and dictates EMV 3DS2 frictionless vs. challenge authentication paths.
- **Invariants**:
  - Any party with a confirmed sanctions match ($\text{Score} \ge 0.85$) is immediately halted and blocked from transaction commitment.
  - Screening logs are immutable and cryptographically archived for regulatory audits.

### 2.7 Settlement & Reconciliation Context
- **Ubiquitous Language**: `SettlementBatch`, `BankStatement`, `CAMT053`, `MT940`, `ThreeWayReconciliation`, `DiscrepancyBreak`.
- **Primary Responsibility**: Ingests external bank statements and clearing files, performs automated 3-way transaction matching, isolates bank fees, schedules merchant settlement sweeps, and routes unreconciled breaks to operational dispute queues.
- **Invariants**:
  - Net settlement sweep cannot be executed on accounts under active dispute or compliance hold.
  - Every external bank debit or credit must be paired with an internal ledger entry or logged as an audited break.

---

## 3. Inter-Context Relationship Map & Integration Contracts

The contexts interact using two distinct operational patterns:
1. **Synchronous Command-Query via gRPC (mTLS)**: Used exclusively on the critical transactional path where immediate atomic confirmation is required (e.g., `HoldFunds`, `ScreenParty`, `EvaluateRoute`).
2. **Asynchronous Event-Driven Choreography via Kafka (Transactional Outbox)**: Used for state propagation, notifications, post-commit projections, and cross-context saga orchestration.

```
+-----------------------------------+----------------------------------+-----------------------+
| Upstream Context                  | Downstream Context               | Integration Contract  |
+-----------------------------------+----------------------------------+-----------------------+
| Acquiring / Payin                 | Double-Entry Ledger              | Synchronous gRPC      |
| Acquiring / Payin                 | Risk & Fraud                     | Synchronous gRPC      |
| Product Orchestrator              | Treasury & FX                    | Synchronous gRPC      |
| Product Orchestrator              | Disbursement / Remittance        | Asynchronous CloudEvt |
| P2P Lending                       | Double-Entry Ledger              | Synchronous gRPC      |
| Double-Entry Ledger (Outbox)      | Settlement & Reconciliation      | Kafka Event Stream    |
| Settlement & Reconciliation       | Merchant Notification / Webhooks | Asynchronous CloudEvt |
+-----------------------------------+----------------------------------+-----------------------+
```

---

## 4. Enterprise Domain Event Catalog

All asynchronous domain events conform to the **CloudEvents v1.0** specification. Events are published to Apache Kafka with strict partition key pinning to ensure monotonic ordering per entity.

### 4.1 CloudEvent Envelope Standard
```json
{
  "specversion": "1.0",
  "id": "evt_9a41b2c8-8921-4f11-b0e2-192837465abc",
  "source": "https://api.gateway.platform.internal/acquiring",
  "type": "com.gateway.payment.captured.v1",
  "datacontenttype": "application/json",
  "time": "2026-11-10T10:45:00.128Z",
  "subject": "pi_891273918237",
  "data": {}
}
```

### 4.2 Comprehensive Event Catalog

| Event Type | Kafka Topic | Partition Key | Emitted When |
| :--- | :--- | :--- | :--- |
| `com.gateway.payment.authorized.v1` | `payments.payin.lifecycle` | `payment_intent_id` | Card or APM authorization is approved by the issuer. |
| `com.gateway.payment.captured.v1` | `payments.payin.lifecycle` | `payment_intent_id` | Payment is captured and funds are committed to pending merchant balance. |
| `com.gateway.payment.failed.v1` | `payments.payin.lifecycle` | `payment_intent_id` | Transaction is declined by bank, 3DS fails, or timeout occurs. |
| `com.gateway.payout.initiated.v1` | `payments.payout.lifecycle` | `payout_order_id` | Domestic or cross-border payout order is validated and funds reserved. |
| `com.gateway.payout.dispatched.v1`| `payments.payout.lifecycle` | `payout_order_id` | Instruction is submitted to external clearing rail (FedNow, SWIFT). |
| `com.gateway.payout.settled.v1` | `payments.payout.lifecycle` | `payout_order_id` | Bank clearing switch confirms receipt and settlement of payout. |
| `com.gateway.payout.reversed.v1` | `payments.payout.lifecycle` | `payout_order_id` | ACH return or clearing reject triggers automated fund release back to merchant. |
| `com.gateway.p2p.loan.funded.v1` | `payments.p2p.lifecycle` | `loan_id` | 100% of investor capital is pooled into the custodian bank FBO escrow. |
| `com.gateway.p2p.disbursed.v1` | `payments.p2p.lifecycle` | `loan_id` | Loan principal is remitted from FBO escrow to the borrower bank account. |
| `com.gateway.p2p.repaid.v1` | `payments.p2p.lifecycle` | `installment_id` | Borrower installment collected; waterfall engine executes investor distribution splits. |
| `com.gateway.ledger.entry.posted.v1`| `payments.ledger.journal` | `account_id` | Balanced double-entry journal entry is committed to the immutable log. |
| `com.gateway.settlement.closed.v1`| `payments.settlement.batch`| `merchant_id` | Daily cut-off batch is finalized; net funds scheduled for merchant bank sweep. |
| `com.gateway.recon.break.detected.v1`| `payments.recon.breaks` | `bank_reference` | Bank statement reconciliation detects an un-matched transaction or fee discrepancy. |
