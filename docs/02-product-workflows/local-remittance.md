# Domestic Local Remittance & Instant Rail Architecture

## 1. Domain Overview & Clearing vs. Settlement Fundamentals

Domestic remittance represents the core money movement capability for high-speed, local interbank fund transfers. This encompasses both real-time, low-value instant payment rails (e.g., US FedNow and RTP, Indonesian BI-FAST, Indian UPI and IMPS, UK Faster Payments, SEPA Instant) and high-volume, deferred net clearing batch rails (e.g., US ACH/NACHA, SEPA Direct Debit, BACS).

A critical architectural distinction maintained throughout this platform is the difference between **Clearing** and **Settlement**:
- **Clearing**: The exchange of payment instructions between the payer’s bank (Debtor Agent) and payee’s bank (Creditor Agent) and subsequent verification of details. In instant rails, clearing is near-instantaneous ($< 3\text{ seconds}$).
- **Settlement**: The actual irrevocable discharge of financial obligations through central bank reserve accounts (RTGS).
  - *Instant Real-Time Gross Settlement (RTGS)*: Clearing and settlement occur simultaneously on an individual transaction basis (e.g. FedNow, UK Faster Payments).
  - *Deferred Net Settlement (DNS)*: Immediate customer credit confirmation, but interbank net funding settles in designated central bank windows (e.g., BI-FAST 8 daily settlement cycles, US ACH next-day).

---

## 2. Technical Workflow Architecture

The end-to-end local payout lifecycle follows a deterministic 5-stage pipeline:

```
[ 1. Beneficiary Probe ] ──► [ 2. Atomic Hold ] ──► [ 3. Routing ] ──► [ 4. Rail Dispatch ] ──► [ 5. Settle / Reconcile ]
  camt.059 Account Check      Ledger Balance Resv.    Limit & Health Check    pacs.008 Dispatch        pacs.002 Confirmation
```

### Stage 1: Beneficiary Account Probe (Name Inquiry)
Before capital is committed, merchants invoke the `/v1/payouts/validate-beneficiary` endpoint to prevent misdirected payments:
1. The platform dispatches an ISO 20022 `camt.059` (Investigation Request) or proprietary switch message to the destination bank.
2. The destination bank verifies whether the account exists, is active, and returns the registered legal account name (e.g., `"JOHN DOE"`).
3. The response returns an immutable `validation_reference` cached in Redis with a 15-minute TTL.

### Stage 2: Payout Submission & Atomic Ledger Hold
1. The merchant submits a payout order with an `Idempotency-Key` and amount in minor currency units (e.g., `500000` for \$5,000.00).
2. The platform checks for existing idempotency records. If fresh, it invokes `LedgerService.HoldFunds()`:
   - Evaluates whether $\text{AvailableBalance} \ge \text{PayoutAmount} + \text{EstimatedFee}$.
   - Atomically decrements `AvailableBalance` and increments `ReservedPendingBalance`.
   - Generates a cryptographically unique `ReservationId`.

### Stage 3: Smart Routing & Rail Selection
The `SmartRoutingEngine` evaluates the destination routing number, transaction amount, current rail health metrics, and cut-off windows:
- If $\text{Amount} \le \text{InstantRailCeiling}$ (e.g., \$500,000 for FedNow, IDR 250,000,000 for BI-FAST) and Rail Health is `HEALTHY` $\rightarrow$ Route via **Instant Real-Time Rail**.
- If $\text{Amount} > \text{InstantRailCeiling}$ or scheduled for end-of-day execution $\rightarrow$ Queue for **Batch Clearing Window (ACH / NACHA)**.

### Stage 4: Rail Dispatch & ISO 20022 Messaging
- For Instant Rails: Dispatches an ISO 20022 `pacs.008.001.08` (Financial Institutional Customer Credit Transfer) with a unique, unalterable `EndToEndId`.
- For Batch Rails: Assigns the transfer to an open `SettlementBatch` file, formatted in NACHA syntax or ISO 20022 `pain.001`, scheduled for delivery at the upcoming bank cut-off.

### Stage 5: Settlement Confirmation & Ledger Commit
1. The external central switch issues an asynchronous `pacs.002.001.10` (Payment Status Report) with status `ACCP` (Accepted / Settled).
2. The platform updates the payout order state to `SETTLED`.
3. The ledger finalizes the pending reservation into a permanent debit:
   - `DEBIT: 210200 - Merchant Reserved Payout Balance`
   - `CREDIT: 110100 - Central Bank Clearing Transit Asset`
   - `DEBIT: 210200 - Merchant Reserved Payout Balance (Fee)`
   - `CREDIT: 410000 - Platform Gateway Fee Revenue`
4. Dispatches an HMAC-signed `payout.settled` CloudEvent webhook to the merchant.

---

## 3. Local Remittance Finite State Machine (FSM)

```
               +-----------------------+
               |         DRAFT         |
               +-----------+-----------+
                           | validate_beneficiary()
               +-----------v-----------+
               | BENEFICIARY_VALIDATED |
               +-----------+-----------+
                           | submit_order() [Funds Reserved in Ledger]
               +-----------v-----------+
               |       SUBMITTED       |
               +-----------+-----------+
                           | route()
               +-----------v-----------+
               |        ROUTED         |
               +-----------+-----------+
              +------------+------------+
 (Instant Rail)|                         | (Batch Clearing)
+--------------v--------+      +---------v-------------+
|  DISPATCHED_TO_RAIL   |      |   QUEUED_IN_BATCH     |
+--------------+--------+      +---------+-------------+
               |                         |
               |  pacs.002 Status Report | batch_dispatched()
               +------------+------------+
                            |
       +--------------------+--------------------+
       |                                         |
+------v---------+                        +------v---------+
|    SETTLED     | (Terminal: Success)    |     FAILED     | (Clearing Reject)
+----------------+                        +------+---------+
                                                 | release_reservation()
                                          +------v---------+
                                          |    REVERSED    | (Terminal: Balance Refunded)
                                          +----------------+
```

### State Machine Transition Rules

| Initial State | Event Trigger | Next State | Ledger Mutation | External Action |
| :--- | :--- | :--- | :--- | :--- |
| `DRAFT` | `ProbeApproved` | `BENEFICIARY_VALIDATED` | None | Verify masked account name |
| `BENEFICIARY_VALIDATED` | `SubmitOrder` | `SUBMITTED` | `HoldFunds()` | Reserve merchant balance |
| `SUBMITTED` | `RouteSelected` | `ROUTED` | None | Assign rail code |
| `ROUTED` | `DispatchInstant` | `DISPATCHED_TO_RAIL` | None | Transmit `pacs.008` message |
| `ROUTED` | `QueueBatch` | `QUEUED_IN_BATCH` | None | Append to open `pain.001` batch |
| `DISPATCHED_TO_RAIL` | `pacs.002.ACCP` | `SETTLED` | `CommitHold()` | Finalize permanent transit credit |
| `DISPATCHED_TO_RAIL` | `pacs.002.RJCT` | `FAILED` | `ReleaseHold()` | Re-credit merchant available balance |
| `FAILED` | `HoldReleased` | `REVERSED` | None | Emit failure CloudEvent webhook |

---

## 4. Virtual Account (VA) Aggregation & Clearing Engine

Virtual Accounts act as shadow bank routing accounts assigned to merchants or individual end-customers, aggregating incoming transfers into a master omnibus clearing pool:

1. **Static Virtual Accounts**:
   - Long-lived 1:1 mapped accounts (e.g. for recurring customer wallet top-ups or borrower loan repayments).
   - Bank switch sends instant credit notifications; platform resolves VA prefix to merchant ID.

2. **Dynamic Virtual Accounts**:
   - Ephemeral accounts created with a strict expected amount and expiration window (e.g. 2 hours for checkout).
   - Underpayment or overpayment rules: System can be configured to reject mismatched amounts or accept partial credits.

3. **Inbound Webhook Verification & Real-Time Sweep**:
   - The partner bank sends an HTTP POST notification signed with RSA-SHA256.
   - Platform verifies signature against the bank's public X.509 certificate.
   - Idempotently credits merchant balance in the double-entry ledger:
     - `DEBIT: 110200 - Bank Partner Inbound Clearing Asset`
     - `CREDIT: 210100 - Merchant Available Balance Liability`
     - `CREDIT: 410000 - Platform Processing Fee Revenue`

---

## 5. Batch Cut-Off Windows & Net Settlement Scheduling

For batch rails (ACH / Direct Debit), transactions are queued and accumulated into batches governed by central bank clearing windows:

| Clearing Window | Window Open | Cut-Off Deadline | Bank Release Time | Settlement Finality |
| :--- | :--- | :--- | :--- | :--- |
| **Morning Cycle (FedACH 1)** | 03:00 UTC | 10:30 UTC | 12:00 UTC | Same-Day 17:00 UTC |
| **Afternoon Cycle (FedACH 2)**| 10:31 UTC | 14:45 UTC | 16:00 UTC | Next-Day 08:30 UTC |
| **Evening Cycle (FedACH 3)** | 14:46 UTC | 18:30 UTC | 20:00 UTC | Next-Day 13:00 UTC |

### Automated Batch Finalization Process
1. At cut-off deadline, an automated scheduler locks the `settlement_batches` record: `status = 'PROCESSING'`.
2. Aggregates total transaction count, gross amount, and platform fees.
3. Generates the ISO 20022 `pain.001.001.09` or NACHA format flat file.
4. Computes SHA-256 hash of the generated file; uploads to the partner bank SFTP server using AS2 or SSH File Transfer with GPG encryption.
5. Monitors for bank ACK (`pain.002`) and return files (`pain.008` / NACHA returns).
