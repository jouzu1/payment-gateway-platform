# Universal Pluggable Reconciliation Engine Subsystem

## 1. Subsystem Overview & Architectural Philosophy

### 1.1 Core Mission & Architectural Placement
The **Universal Pluggable Reconciliation Engine** is an enterprise-grade core subsystem residing authoritatively within the **Settlement & Reconciliation Bounded Context** (see [Domain Boundaries](../01-architecture-overview/domain-boundaries.md)). Its primary mandate is to resolve the fundamental structural tension in high-throughput financial platforms: the divergence between real-time, optimistic internal transaction state transitions and asynchronous, multi-rail external bank settlement reality.

In modern multi-product financial infrastructures, money movement encompasses diverse clearing mechanisms:
- Synchronous instant gross settlement rails (e.g., FedNow, BI-FAST, UK Faster Payments, Pix)
- Batch-cleared net settlement card schemes (Visa, Mastercard) and ACH networks (NACHA)
- Regulated custodial escrow trust accounts (P2P Lending FBO Trust Accounts under OJK POJK 10/2022 and SEC custody rules)
- Asynchronous high-frequency streaming aggregators (Dynamic QRIS, UPI, e-wallets)
- Decentralized cryptographic blockchains (Web3 stablecoin fiat on/off ramps on EVM L1/L2 networks)

Traditional payment architectures rely on monolithic, hardcoded reconciliation batch scripts tightly coupled to specific bank statement layouts or single-rail clearing cycles. When a new banking partner is onboarded, or when an existing partner alters its settlement batch timing, fee deduction structure, or statement formatting (e.g., transitioning from SWIFT MT940 to ISO 20022 `camt.053`), monolithic engines require risky core code modifications.

The Universal Pluggable Reconciliation Engine eliminates core code refactoring by introducing a **Service Provider Interface (SPI)** architecture paired with a **Declarative Matching Pipeline**. Clearing rails, statement parsers, and matching topologies are encapsulated as pluggable adapters governed by declarative JSON configuration profiles (see [Extensibility SPI](../01-architecture-overview/extensibility-spi.md)).

```
+----------------------------------------------------------------------------------------------------+
|                                    SETTLEMENT & RECONCILIATION CONTEXT                             |
|                                                                                                    |
|   +------------------------------------+          +--------------------------------------------+   |
|   | Settlement Engine                  |          | Universal Pluggable Reconciliation Engine  |   |
|   | - Batch Aggregation & Netting      |<-------->| - Multi-Format Ingestion (camt, MT, BAI2)  |   |
|   | - Merchant Payout Calculation      |          | - 5 Pluggable Matching Topologies          |   |
|   | - Reserve Hold Management          |          | - 2-Level Idempotency Deduplication        |   |
|   +-----------------+------------------+          | - Automated Break Remediation & FSM        |   |
|                     |                             +---------------------+----------------------+   |
|                     v                                                   |                          |
|   +------------------------------------+                                | Commit Ledger Journals   |
|   | Double-Entry Ledger Subsystem      |<-------------------------------+ & Release Holds          |
|   | - Chart of Accounts (COA)          |                                                           |
|   | - Immutable Journal Postings       |                                                           |
|   | - Balance Conservation Law         |                                                           |
|   +------------------------------------+                                                           |
+----------------------------------------------------------------------------------------------------+
```

### 1.2 Architectural Invariants & Separation of Concerns
The engine strictly enforces four foundational architectural invariants:

1. **Balance Conservation Invariant**:
   No money enters, moves within, or leaves the platform without an auditable matching external bank record or an audited, approved reconciliation break. Every external bank credit or debit line must either pair identically with internal transactions or be quarantined into designated general ledger suspense accounts.
2. **Immutability of Historical Statements**:
   Once ingested and cryptographically fingerprinted, raw statement files and parsed `statement_lines` are immutable. Reconciliation run outcomes never alter raw banking data; all balance adjustments occur via corrective double-entry journal postings in the [Double-Entry Ledger](double-entry-ledger.md).
3. **Decoupled Lifecycle of Intent vs. Settlement**:
   Payment authorization and capture represent transactional intent. Settlement represents the irreversible transfer of central bank money across commercial banking Nostro/Vostro accounts. The reconciliation engine functions as the authoritative gatekeeper converting provisional ledger balances into settled, withdrawable merchant funds.
4. **Partition-Aware Scalability**:
   All database tables storing high-volume statement lines and match executions are partitioned by date ranges, guaranteeing $O(1)$ partition detachment for archival and eliminating table-scan lock escalation during intensive end-of-day (EOD) matching windows (see [PostgreSQL DDL](../04-data-models-and-schemas/postgresql-ddl.md)).

The pipeline strictly separates five functional concerns:
- **Ingestion & Storage**: Protocol transport (SFTP, AWS S3, HTTPS webhook, Blockchain JSON-RPC) and raw payload persistence.
- **Normalization & Parsing**: Parsing heterogeneous formats into canonical `statement_lines` standardized to 64-bit integer minor currency units (`BIGINT`).
- **Topology Matching**: Dynamic dispatch to specialized mathematical matching engines (1:1 Direct, N:M Netting, Tripartite Escrow, Streaming Intraday, Cryptographic On-Chain).
- **Break Management**: Identification, classification, lifecycle state machine tracking, tolerance absorption, and dual-control Maker-Checker remediation.
- **Ledger Attestation**: Posting final balancing journals to the [Double-Entry Ledger](double-entry-ledger.md) and releasing merchant settlement reserves in the [Settlement Engine](settlement-engine.md).

### 1.3 The Pluggable SPI Paradigm
Rather than hardcoding matching logic per partner bank, the engine decouples execution into two layers:
1. **The `ReconciliationRailAdapter` SPI**: Defined in TypeScript and Go in [Extensibility SPI](../01-architecture-overview/extensibility-spi.md), standardizing statement retrieval (`fetchStatements`), format parsing (`parseStatementLines`), capability discovery (`getCapabilities`), streaming line iterators (`streamStatementLines`), and external discrepancy notifications (`notifyDiscrepancyBreak`).
2. **The Declarative `ReconciliationProfile`**: A versioned JSON schema loaded at runtime defining active matching topologies, multi-tier composite fallback keys, quantitative tolerance thresholds, banking cut-off schedules, and exception routing rules.

---

## 2. Pluggable Matching Pipeline Architecture

### 2.1 End-to-End Pipeline Data Flow
The reconciliation engine operates as an asynchronous, event-driven and batch-oriented pipeline. The diagram below illustrates the end-to-end data flow:

```
[ External Banking / Clearing Rails ]
  │
  ├── SFTP / S3 Bucket Polling (camt.053, MT940, BAI2, NACHA)
  ├── Real-Time Webhooks (camt.054, ISO 20022 JSON)
  └── EVM RPC Node Listeners (ERC-20 Transfer Receipts)
  │
  ▼
+─────────────────────────────────────────────────────────────────────────────+
| Step 1: Ingestion & Level-1 Idempotency Filter                              |
| - Verify SHA-256 File Content Hash against clearing_statements.file_hash    |
| - If duplicate: Return existing statement_id immediately (O(1) duplicate)   |
| - Persist raw statement payload to S3 Intelligent-Tiering                   |
+─────────────────────────────────────────────────────────────────────────────+
  │
  ▼
+─────────────────────────────────────────────────────────────────────────────+
| Step 2: Normalization & Multi-Tier Idempotency Filter                       |
| - Parse payload via ReconciliationRailAdapter into NormalizedStatementLine  |
| - Convert all currencies to 64-bit minor integer units (BIGINT cents)       |
| - Compute Line Hash: SHA256(statement_id:line_seq:bank_ref:date:amt:dir)   |
| - Enforce intra-statement idempotency: UNIQUE(statement_id, seq, date)     |
| - Filter cross-statement amended duplicates via natural key lookup         |
+─────────────────────────────────────────────────────────────────────────────+
  │
  ▼
+─────────────────────────────────────────────────────────────────────────────+
| Step 3: Topology Router & Declarative Profile Resolution                    |
| - Lookup rail_id and match criteria in ReconciliationProfile registry       |
| - Route normalized lines to designated Matching Strategy Engine:            |
|   ┌─────────────┬─────────────┬─────────────┬─────────────┬─────────────┐   |
|   |  1:1 Direct | N:M Netting |  Tripartite |  Streaming  |   On-Chain  |   |
|   |   Engine    |    Engine   |    Escrow   |   Intraday  |    Crypto   |   |
|   └─────────────┴─────────────┴─────────────┴─────────────┴─────────────┘   |
+─────────────────────────────────────────────────────────────────────────────+
  │
  ▼
+─────────────────────────────────────────────────────────────────────────────+
| Step 4: Multi-Tier Matching Execution                                       |
| - Tier 1: Primary Deterministic Match (EndToEndId, ARN, LoanID, TxHash)     |
| - Tier 2: Composite Fallback Keys (Account + Amount + Date Window)          |
| - Tier 3: Heuristic & Tolerance Scoring                                     |
+─────────────────────────────────────────────────────────────────────────────+
  │
  ├── Matches Identified ──────────┐
  │                                │ Breaks / Discrepancies
  ▼                                ▼
+──────────────────────────+     +────────────────────────────────────────────+
| Step 5A: Settlement Path |     | Step 5B: Break Management Subsystem        |
| - Persist match records  |     | - Classify Break Taxonomy (6 classes)      |
| - Unlock merchant funds  |     | - Check Automated Tolerance Thresholds     |
| - Commit Double-Entry    |     |   (<= $2.00 or 15 bps -> Fee Write-Off)    |
|   Ledger settlement legs |     | - If > Tolerance: Quarantine to Suspense   |
| - Update run status      |     | - Route to Maker-Checker 4-Eyes Queue      |
+──────────────────────────+     +────────────────────────────────────────────+
```

### 2.2 Reconciliation Pipeline Sequence & Components
The operational sequence involves four coordinated components:
1. **Statement Ingestion Worker (`IngestionService`)**: Consumes files from SFTP servers, Amazon S3 drop locations, or HTTP upload endpoints (`POST /v1/reconciliation/statements`). Computes raw payload hash `SHA256(RawBytes)` and verifies absence in `clearing_statements`.
2. **Normalization Worker (`ParserEngine`)**: Invokes rail-specific parser SPI to transform raw banking records into canonical `NormalizedStatementLine` objects. Normalizes dates to UTC ISO-8601, validates ISO 4217 currency codes, and assigns `statement_lines.line_hash`.
3. **Reconciliation Dispatcher (`TopologyRouter`)**: Queries the registered `ReconciliationProfile` for the target rail. Dispatches batches of statement lines and internal transaction records to the selected matching topology engine.
4. **Settlement & Break Orchestrator (`SettlementAttestationService`)**: Evaluates matching results. Matched transactions transition to `SETTLED`; unmatched internal or external records transition to `reconciliation_breaks` with designated FSM status.

### 2.3 Topology Router & Declarative Profile Resolution
The `TopologyRouter` evaluates three attributes to resolve the appropriate engine:
- `rail_id`: Identifies the partner banking institution or network switch (e.g., `FEDNOW_DIRECT`, `CARD_VISA_INTERCHANGE`, `P2P_ESCROW_CUSTODIAN`).
- `topology_type`: Declares the operational matching paradigm (`DIRECT_1_TO_1`, `NETTING_N_TO_M`, `TRIPARTITE_ESCROW`, `STREAMING_INTRADAY`, `CRYPTOGRAPHIC_ON_CHAIN`).
- `matching_strategy`: Governs the correlation algorithm (`DETERMINISTIC`, `NET_BATCH_AGGREGATE`, `WATERFALL_TRI_PARTY`, `HEURISTIC_SCORE`, `BLOCKCHAIN_FINALITY`).

If a banking partner alters its settlement mechanism (for instance, moving from batch EOD netting to intraday instant clearing), updating the declarative `ReconciliationProfile` redirects processing to the new matching engine without code redeployment.

---

## 3. The Five Core Matching Topologies

### 3.1 Topology 1: 1:1 Direct Matching (Instant Remittance & Single-Message Sales)

#### 3.1.1 Target Financial Products & Operational Context
- **Financial Products**: Local Instant Remittance (FedNow in the US, BI-FAST in Indonesia, Faster Payments in the UK, Pix in Brazil), Direct Payouts, Card Single-Message Sales (`sale` mode).
- **Operational Reality**: The payment instruction is submitted to the central rail and settled immediately on a gross, transaction-by-transaction basis. The partner bank Nostro account is debited or credited simultaneously with payment execution. The bank statement exhibits a discrete credit or debit line for every individual transaction.

#### 3.1.2 Matching Cardinality & Mathematical Invariant
The matching cardinality is strictly 1:1. Exactly one internal payment intent corresponds to exactly one double-entry ledger entry and exactly one bank statement line.

$$\text{InternalTransaction}_{k} \Longleftrightarrow \text{StatementLine}_{m}$$

The mathematical conservation invariant requires exact parity between statement line amount and internal gross transaction amount, subject to authorized bank switch fee deductions:

$$\text{StatementLine.Amount} = \text{InternalTransaction.GrossAmount} - \text{RailFeeVariance}$$

Where allowable variance is constrained by the conjunction:

$$|\text{RailFeeVariance}| \le \min(\text{ToleranceCents}, \text{InternalTransaction.GrossAmount} \times \text{ToleranceBps}) \le \min(\$2.00, 15\text{ bps})$$

#### 3.1.3 Correlation Keys & Matching Mechanics
- **Primary Deterministic Key**: End-to-End Reference (`EndToEndId` in ISO 20022 `<Refs><EndToEndId>`, `rail_transaction_ref`, or client `payout_order_id`).
- **Secondary Fallback Key (Composite Tier 1)**: Computed when external bank statements truncate or corrupt the reference string:
  $$\text{Key}_{\text{direct}} = \text{SHA256}(\text{DestinationBankCode} + \text{TargetAccountNumber} + \text{AmountCents} + \text{BookingDate})$$
- **Heuristic Window**: Matches falling within $\Delta t \le \pm 30\text{ minutes}$ of the internal authorization timestamp are evaluated.

#### 3.1.4 Discrepancy & Break Handling
- **Unmatched Internal**: If an internal transaction in `PENDING_RECONCILIATION` has no matching bank line after 24 hours, the engine initiates an automated rail status query. If the bank confirms failure, funds reserved in Account `120000 - Funds in Transit` are rolled back to the merchant's available balance (Account `210100`).
- **Unmatched External**: An unexpected credit to the Nostro account is quarantined to Account `240900 - Unidentified Receipts / Suspense` pending Treasury investigation.
- **Amount Variance**: Fee variances within tolerance ($|\text{RailFeeVariance}| \le \min(\$2.00, 15\text{ bps})$) are automatically absorbed and posted to Account `510000 - Bank Switch Fee Variance Expense`. Variances exceeding tolerance generate an `AMOUNT_MISMATCH` break.

---

### 3.2 Topology 2: N:M Netting Matching (Card Schemes, ACH Batches & Sweeps)

#### 3.2.1 Target Financial Products & Operational Context
- **Financial Products**: Card Scheme Acquiring (Visa Base II, Mastercard IPM, Cybersource clearing batches), ACH Batch Clearing (NACHA PPD/CCD files), Merchant Net Settlement Sweeps ($T+0, T+1, T+2$).
- **Operational Reality**: Thousands of consumer card authorizations captured throughout the day are submitted in an aggregated batch file. The card scheme or clearing house clears transactions gross, calculates interchange, scheme fees, and assessment fees, subtracts chargebacks and refunds, and remits one or more lump-sum net credit transfers to the acquiring bank Nostro account.

#### 3.2.2 Multi-Leg Waterfall & Mathematical Invariant
Reconciliation follows a 3-leg reconciliation waterfall:
1. **Internal Captured Batch Leg**: Platform captures $N$ individual payments under a distinct `settlement_batch_id`.
2. **Scheme Clearing File Leg**: External scheme clearing report provides interchange breakdown for $N$ transactions.
3. **Bank Nostro Settlement Leg**: $M$ lump-sum net wire transfers credited to commercial bank Nostro account `110100`.

The mathematical invariant requires conservation of value across all captured credits, deductions, and bank deposits:

$$\sum_{i=1}^{N} \text{GrossCaptured}_i - \sum_{j=1}^{K} \text{InterchangeFee}_j - \sum_{p=1}^{P} \text{SchemeAssessment}_p - \sum_{r=1}^{R} \text{Refund}_r - \sum_{c=1}^{C} \text{Chargeback}_c \equiv \sum_{m=1}^{M} \text{BankSettlementCredit}_m$$

#### 3.2.3 Correlation Keys & Grouping Mechanics
- **Primary Deterministic Key**: `settlement_batches.id` mapped to external clearing cycle batch identifier or Acquirer Reference Number (ARN) summary.
- **Secondary Fallback Key (Composite Tier 1)**:
  $$\text{Key}_{\text{netting}} = \text{SHA256}(\text{CardSchemeCode} + \text{AcquiringMID} + \text{SettlementDate} + \text{Currency})$$
- **Grouping Window**: Transactions assigned to `settlement_batch_id` are grouped, and calculated net settlement is matched against the aggregate credit lines in the bank statement.

#### 3.2.4 Discrepancy & Break Handling
- **Interchange Fee Drift**: If the net bank credit differs from the calculated batch total, the engine analyzes the delta against the interchange fee schedule. Variances up to \$50.00 or $25\text{ bps}$ resulting from card tier qualification downgrades (e.g., standard consumer card downgraded to commercial card interchange) are booked to Account `510000 - Fee Variance Expense`.
- **Missing Clearing Leg**: If a batch has cleared on the scheme report but bank credit is absent after $T+2$ banking days, an `UNMATCHED_INTERNAL` break is raised with Tier-2 escalation to Treasury Operations.
- **Batch Linkage**: All breaks retain a foreign key reference to `settlement_batches.id` (`reconciliation_breaks.settlement_batch_id`).

---

### 3.3 Topology 3: Tripartite Escrow Matching (P2P Lending & Custody)

#### 3.3.1 Target Financial Products & Operational Context
- **Financial Products**: Peer-to-Peer (P2P) Lending Escrow, Marketplace Trust Accounts, Custodial Stored-Value Accounts (under Indonesian POJK 10/2022 and US SEC Rule 15c3-3 customer protection rules).
- **Operational Reality**: Strict regulatory separation between platform operating capital and customer trust funds. All borrower repayments and investor funds must reside in a dedicated For-Benefit-Of (FBO) Trust Escrow Account at a licensed custodian bank. The platform maintains zero balance sheet claim on these funds; co-mingling platform operational funds with client trust funds constitutes a severe regulatory violation.

#### 3.3.2 3-Way Tripartite Mathematical Invariant
Reconciliation requires continuous 3-way synchronization across:
1. **External Custodian Bank FBO Cash Balance** (Account `110400 - Custodian Bank FBO Escrow`).
2. **Borrower Inbound Collections Ledger** (Platform inbound servicing records).
3. **Investor Waterfall Distribution Subledger** (Fractional loan shares, statutory withholding tax, and platform servicing revenue).

$$\text{CustodianBankCashBalance (110400)} \equiv \sum_{b=1}^{B} \text{BorrowerRepayments}_b \equiv \sum_{inv=1}^{M} \text{InvestorDistributions}_{inv} + \text{WithholdingTax (230400)} + \text{ServicingFee (430000)}$$

```
+─────────────────────────────────────────────────────────────────────────────+
|                        TRIPARTITE ESCROW MATCHING FLOW                      |
|                                                                             |
|   Borrower Repayment (Cash Inbound)                                         |
|   Bank FBO Statement Line: +$1,000.00 (Booking Date: 2026-11-11)            |
|                           │                                                 |
|                           ▼                                                 |
|   +─────────────────────────────────────────────────────────────────────+   |
|   | 3-Way Waterfall Allocation Algorithm (Zero Co-Mingling Invariant)   |   |
|   |                                                                     |   |
|   | 1. Servicing Fee Deduction:        $15.00 -> Credit 430000 (Rev)    |   |
|   | 2. Withholding Tax Deduction:      $10.00 -> Credit 230400 (Tax)    |   |
|   | 3. Principal Repayment:           $800.00 -> Pro-Rata to Investors  |   |
|   | 4. Net Interest Repayment:        $175.00 -> Pro-Rata to Investors  |   |
|   |                                                                     |   |
|   | 5. Fractional Allocation (M=100 Investors @ $9.75 each):            |   |
|   |    Sum of Fractional Splits:      $975.00 -> Credit 230100 (Liab)   |   |
|   |    Minor Unit Rounding Residual:    $0.00 -> Credit 240900 (Susp)   |   |
|   +───────────────────────────────────┬─────────────────────────────────+   |
|                                       │                                     |
|                                       ▼                                     |
|   Total Allocated: $15 + $10 + $800 + $175 = $1,000.00                      |
|   Reconciliation Check: Bank Cash ($1,000.00) == Allocated ($1,000.00)      |
|   Status: EXACT_MATCH (FBO Invariant Satisfied, Zero Discrepancy)           |
+─────────────────────────────────────────────────────────────────────────────+
```

#### 3.3.3 Fractional Minor-Unit Rounding Residuals
When an inbound borrower repayment of interest $I$ is divided pro-rata across $M$ fractional investors, rounding down to integer minor units (cents) creates a fractional residual $\Delta$:

$$\Delta = I - \sum_{m=1}^{M} \left\lfloor \text{Share}_m \times I \right\rfloor \quad \text{where } \Delta \in [0, M-1] \text{ minor units}$$

Under regulatory audit rules, this rounding delta cannot be absorbed into platform revenue. The engine explicitly books $\Delta$ to Account `240900 - Rounding Suspense Liability`, preserving mathematical conservation to the exact minor integer unit.

#### 3.3.4 Discrepancy & Break Handling
- **Zero-Tolerance Policy**: For Tripartite Escrow, tolerance rules are configured to `amount_cents: 0` and `basis_points: 0`. No automated write-off is permitted.
- **Escrow Freeze Trigger**: If bank FBO cash differs by even \$0.01 from internal investor ledger balances, the automated payout sweep is halted immediately, and a critical `FRACTIONAL_ROUNDING_BREAK` or `AMOUNT_MISMATCH` alert is dispatched to the Compliance and Legal departments.

---

### 3.4 Topology 4: Streaming Intraday Reconciliation (Dynamic QRIS, UPI & Wallets)

#### 3.4.1 Target Financial Products & Operational Context
- **Financial Products**: Dynamic QRIS (Quick Response Code Indonesia Standard), UPI (Unified Payments Interface in India), Real-Time E-Wallet Callbacks (GoPay, OVO, Dana, GrabPay).
- **Operational Reality**: High-velocity retail transactions occurring at rates of hundreds or thousands of transactions per second. Waiting for midnight EOD batch files creates excessive credit risk and delays merchant settlement. The engine reconciles transactions intraday using streaming events.

#### 3.4.2 In-Memory Sliding-Window Architecture
The engine maintains an in-memory, time-indexed sliding window buffer (configured between 30 seconds and 30 minutes) using **Redis Sorted Sets**:
- Internal transaction intents are pushed to Redis via `ZADD recon:pending:<rail> <unix_timestamp> <intent_payload>`.
- Inbound bank clearing notifications (via webhook or streaming socket) arrive and perform sub-millisecond candidate lookup via indexed keys.

```
[ Internal Intent Event ] ──► ZADD buffer (Score = Timestamp t0)
                                      │
                                      ▼ Window [t0, t0 + 30m]
                              Candidate Key Lookup
                                      ▲
                                      │
[ Bank Real-Time Callback ] ──► ZRANGEBYSCORE buffer (t0 - 30m, t0 + 30m)
```

#### 3.4.3 Fast-Path vs. Slow-Path Execution
1. **Fast-Path (Intraday Real-Time)**:
   - Bank notification arrives within the sliding window $\Delta t \le 30\text{ minutes}$.
   - Deterministic key matches: internal record is updated to `SETTLED_INTRADAY`, provisional ledger hold is converted to settled merchant balance, and the record is evicted from the Redis buffer (`ZREM`).
2. **Slow-Path (End-of-Day Fallback)**:
   - If the sliding window expires without an external clearing event, the transaction is **not** immediately marked as broken. Network delays, aggregator batching, or switch queue lag frequently delay callbacks.
   - The expired intent is gracefully drained from Redis and promoted to the standard End-of-Day (EOD) Batch Reconciliation queue.
   - The EOD job reconciles the transaction against the formal bank statement file (e.g., `camt.053` or MT940). If absent from the EOD statement, a formal `TIMING_DELAY` or `UNMATCHED_INTERNAL` break is raised.

---

### 3.5 Topology 5: Cryptographic On-Chain Reconciliation (Web3 Stablecoin Ramps)

#### 3.5.1 Target Financial Products & Operational Context
- **Financial Products**: Web3 Fiat On-Ramps and Off-Ramps (USDC, USDT on Ethereum, Arbitrum, Polygon), Tokenized Deposit Clearing, Central Bank Digital Currency (CBDC) pilot rails (see [Extensibility SPI](../01-architecture-overview/extensibility-spi.md) Section 6.3).
- **Operational Reality**: Dual-realm settlement spanning traditional centralized fiat banking ledgers (off-chain) and decentralized cryptographic distributed ledgers (on-chain). Transactions on public blockchains are subject to block re-organizations (reorgs), gas price volatility, smart contract execution states, and probabilistic finality.

#### 3.5.2 Dual-Leg Cryptographic Invariant
A Web3 fiat on/off-ramp transaction is only reconciled when both the fiat banking transfer and the on-chain smart contract token transfer are cryptographically verified:

$$\text{FiatNostroMovement} \Longleftrightarrow \text{BlockchainReceiptProof}$$

$$\text{FiatAmountCents} \times \text{FXQuoteRate} \equiv \text{OnChainTokenUnits} \pm \text{PermittedSlippage}$$

Where blockchain proof requires:
- `TransactionStatus == 0x1` (EVM receipt success)
- `Confirmations} \ge N_{\text{finality}}` (e.g., 12 blocks for Ethereum PoS, 64 blocks for Polygon PoS)
- Verification of the ERC-20 `Transfer(address indexed from, address indexed to, uint256 value)` log event emitted by the verified stablecoin contract address.

#### 3.5.3 Finality Windows & Chain Reorganization (Reorg) Protection
Blockchain networks occasionally experience temporary forks where a block previously considered valid is replaced by a longer canonical chain. If a payment gateway marks a transaction as settled during a reorg, it faces double-spend loss.

To prevent this, the engine enforces a two-stage finality verification:
1. **Tentative Match (`PENDING_FINALITY`)**: Transaction receipt verified with minimum confirmation threshold.
2. **Final Match (`ON_CHAIN_CONFIRMED`)**: Finality block height achieved (or PoS checkpoint finalized via Casper FFG).
3. **Reorg Recovery FSM**:
   If a chain reorg drops the transaction from the canonical chain:
   - The engine flags a `CHAIN_REORG_BREAK`.
   - The provisional ledger leg is reversed: `DEBIT: 210100 - Merchant Available Balance`, `CREDIT: 110500 - Blockchain In-Transit`.
   - The engine monitors the mempool for 120 minutes. If the transaction is mined into a new block, the match is re-established. If dropped completely, the break escalates to Manual Intervention.

#### 3.5.4 Gas Accounting & Slippage Reconciliation
- On-chain gas costs paid by the platform relayer are recorded via gas receipts and booked to Account `530000 - Blockchain Gas Expense`.
- Micro-slippage between fiat conversion quotes and on-chain minting (up to 5 bps) is reconciled against Account `110500 - FX Slippage Suspense`.

---

### 3.6 Topology Comparison Matrix

| Dimension | Topology 1: 1:1 Direct Matching | Topology 2: N:M Netting Matching | Topology 3: Tripartite Escrow | Topology 4: Streaming Intraday | Topology 5: Cryptographic On-Chain |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Target Products** | Local Remittance (FedNow, FAST), Card Sales | Card Acquiring (Visa, MC), ACH Batches | P2P Lending Escrow, Marketplace Custody | Dynamic QRIS, UPI, Instant E-Wallets | Web3 USDC/USDT Fiat On/Off-Ramps |
| **Cardinality** | 1 Internal : 1 External | $N$ Internal : $M$ External | 1 Bank : $B$ Repayments : $M$ Splits | 1 Intent : 1 Callback (Streaming) | 1 Fiat Leg : 1 On-Chain Tx Leg |
| **Primary Keys** | `EndToEndId`, `rail_tx_ref` | `settlement_batch_id`, ARN | `loan_id`, `escrow_account_id` | `qr_string_tlv`, `va_number` | `tx_hash`, `contract_address` |
| **State Storage** | PostgreSQL Relational | PostgreSQL Relational | PostgreSQL + Audit Log | Redis Sorted Sets + PostgreSQL | EVM RPC Node + PostgreSQL |
| **Latency SLA** | Minutes to EOD | Batch ($T+1 / T+2$) | Continuous EOD ($T+0$) | Sub-second to 30 mins | Block finality (2 - 15 mins) |
| **Tolerance Write-Off** | $\le \$2.00$ or $15\text{ bps}$ to `510000` | $\le \$50.00$ interchange drift | Strictly \$0.00 (Zero Tolerance) | \$0.00 intraday (EOD fallback) | $\le 5\text{ bps}$ slippage to `110500` |
| **Break Classes** | `UNMATCHED_INTERNAL`, `AMOUNT_MISMATCH` | `AMOUNT_MISMATCH`, `TIMING_DELAY` | `FRACTIONAL_ROUNDING_BREAK` | `TIMING_DELAY` (EOD Drain) | `CHAIN_REORG_BREAK` |

---

## 4. Statement Ingestion & Normalization Pipelines

### 4.1 Multi-Format Bank Statement Parsers
The engine integrates native parsers for the world's standard banking clearing formats:

1. **ISO 20022 XML Family**:
   - `camt.053.001.08`: End-of-day bank-to-customer statement. Extracts `<BkToCstmrStmt>`, statement balances `<Bal>`, and entries `<Ntry>` containing `<Amt>`, credit/debit indicator `<CdtDbtInd>`, and end-to-end reference `<Refs><EndToEndId>`.
   - `camt.052.001.08`: Intraday account report providing real-time account snapshots.
   - `camt.054.001.08`: Real-time debit and credit notifications dispatched via bank webhooks.
2. **SWIFT Structured Flat Files**:
   - `MT940`: End-of-day customer statement. Parses tag `:20:` (Transaction Reference), `:25:` (Account Identification), `:28C:` (Statement Number), `:60F:` (Opening Balance), `:61:` (Statement Line: Value Date, Entry Date, Debit/Credit Mark, Amount, Transaction Type, Reference), and `:86:` (Information to Account Owner narrative).
   - `MT942`: Intraday interim transaction report.
3. **BAI2 (Bank Administration Institute Standard)**:
   - High-volume 80-character fixed/delimited US commercial banking records: Record `01` (Header), `02` (Group Header), `03` (Account Identifier), `16` (Transaction Detail with type codes: `115` Direct Deposit, `495` Outgoing Wire), `49` (Account Trailer), `98` (Group Trailer), and `99` (File Trailer).
4. **NACHA ACH Files**:
   - Standard 94-character US Automated Clearing House clearing batches: File Header (`1`), Company/Batch Header (`5`), Entry Detail (`6` - PPD/CCD records), Addenda (`7`), Batch Control (`8`), and File Control (`9`).
5. **Proprietary Bank CSV/TSV**:
   - Configurable delimiter-separated formats mapped dynamically via column index mappings in the `ReconciliationProfile`.
6. **Streaming JSON Webhooks & Blockchain RPC**:
   - Real-time HTTPS payloads and EVM JSON-RPC `eth_getTransactionReceipt` and `eth_getLogs` decoders.

### 4.2 Ingestion Topologies: Batched vs. Streaming
The ingestion subsystem handles two operational ingestion modes:
- **Batched Ingestion**: Scheduled polling workers query partner bank SFTP servers or Amazon S3 drop buckets at scheduled cut-off times. Large multi-hundred-megabyte statement files are ingested via streaming chunked readers without consuming memory buffers.
- **Streaming Ingestion**: Asynchronous Kafka topics (`clearing.statements.streaming`) consume intraday webhooks and push notifications, feeding normalized events directly to the in-memory sliding window matching pipeline.

### 4.3 Minor Currency Unit & ISO Normalization
To prevent precision loss and IEEE-754 floating-point rounding errors:
- All financial monetary amounts are converted strictly to **64-bit integer minor currency units** (`BIGINT` cents, pence, rupiah, or satoshis). For example, USD \$1,250.50 is stored as `125050`. IDR Rp 1.000.000 is stored as `1000000`.
- Currencies are normalized to uppercase 3-letter **ISO 4217** codes (`USD`, `IDR`, `EUR`, `SGD`, `GBP`).
- Timestamps are normalized to **ISO 8601 UTC** formatted strings (`YYYY-MM-DDTHH:mm:ss.sssZ`).
- Transaction flow direction is normalized to binary enum: `DEBIT` (outflow from bank account) or `CREDIT` (inflow to bank account).

### 4.4 Multi-Level Idempotency & Deduplication Engine
Commercial banking communications are fraught with transmission retries, partial network drops, amended files, and duplicate file uploads. The engine implements a rigorous **Three-Tier Multi-Level Idempotency and Deduplication Model**:

```
[ Raw Statement Ingestion Request ]
                │
                ▼
+─────────────────────────────────────────────────────────────+
| Tier 1: File Content Hash Check (O(1) Full File Idempotency)|
| FileHash = SHA256(RawFileBinaryBytes)                       |
| Query: SELECT id FROM clearing_statements                   |
|        WHERE file_hash = :FileHash                          |
+─────────────────────────────────────────────────────────────+
        │                                     │
   File Exists (Identical Replay)         New File Hash
        │                                     │
        ▼                                     ▼
[ Return HTTP 200 OK ]               [ Check Statement Supersession ]
[ Skip All Processing ]              [ Insert clearing_statements ]
                                     [ Parse Statement Lines ]
                                              │
                                              ▼
                             +───────────────────────────────────────────────────+
                             | Tier 2: Intra-Statement Worker Retry Idempotency  |
                             | LineHash = SHA256(statement_id:line_sequence:     |
                             |            bank_ref:booking_date:amount:direction)|
                             | Constraint: UNIQUE (statement_id, line_sequence,  |
                             |                     booking_date)                 |
                             +───────────────────────────────────────────────────+
                                     │                             │
                             Worker Retry Duplicate            New In-File Line
                                     │                             │
                                     ▼                             ▼
                           [ ON CONFLICT DO NOTHING ]   +───────────────────────────────────+
                                                        | Tier 3: Cross-Statement Dedup     |
                                                        | Natural Key & Occurrence Lookup:  |
                                                        | (rail_id, account_id, bank_ref,   |
                                                        |  booking_date, amount, dir, k)    |
                                                        +───────────────────────────────────+
                                                                │                   │
                                                      Previously Processed      Net-New Line
                                                                │                   │
                                                                ▼                   ▼
                                                     [ Flag: SKIPPED_ALREADY] [ Flag: PENDING     ]
                                                     [ Bypass Recon Engine  ] [ Route to Matcher  ]
```

1. **Tier 1: File Content Hash (Full File Retransmission Guard)**:
   $$\text{FileHash} = \text{SHA256}(\text{RawFileBytes})$$
   Stored in `clearing_statements.file_hash`. Before parsing, the worker checks if `file_hash` already exists in `clearing_statements`. If matched, the engine returns the existing `statement_id` immediately with an idempotent HTTP 200/OK response, terminating processing before consuming CPU or partition query resources.

2. **Tier 2: Intra-Statement Line Hash & Sequence (Worker Retry Protection)**:
   $$\text{LineHash} = \text{SHA256}(\text{statement\_id} + \text{":"} + \text{line\_sequence} + \text{":"} + \text{bank\_reference} + \text{":"} + \text{booking\_date} + \text{":"} + \text{amount\_cents} + \text{":"} + \text{direction})$$
   Stored in `statement_lines.line_hash`. To guarantee partition compatibility in PostgreSQL declarative partitioning by `RANGE (booking_date)`, intra-statement idempotency is enforced via:
   $$\text{UNIQUE}(\text{statement\_id}, \text{line\_sequence}, \text{booking\_date})$$
   - **Preservation of Legitimate Identical Transactions**: High-volume card acquiring and fee assessments frequently feature identical charges on the same day (e.g., two \$3.50 coffee purchases with identical references). Because `line_sequence` is strictly monotonic and assigned 1-based by the parser ($1, 2, \dots, N$), legitimate identical lines generate distinct sequence numbers and distinct `LineHash` values, preventing false-positive deduplication and silent financial data loss.
   - **Worker Crash Idempotency**: If a worker crashes mid-ingestion, re-executing with `ON CONFLICT (statement_id, line_sequence, booking_date) DO NOTHING` safely skips already-persisted rows and resumes insertion without error.

3. **Tier 3: Cross-Statement Deduplication for Amended & Retransmitted Statement Files**:
   When partner banks re-transmit amended statement files (e.g., appended afternoon transactions, corrected balances, or cumulative end-of-day statements replacing earlier intraday files), the file hash changes and a new `statement_id` is assigned. Because `statement_id` is part of Tier 2, previously processed lines would generate new line hashes. Cross-statement deduplication resolves this via Natural Business Key tracking:
   $$\text{NaturalKey} = (\text{rail\_id}, \text{account\_id}, \text{bank\_reference}, \text{booking\_date}, \text{amount\_cents}, \text{direction})$$
   - **Ordinal Occurrence Matching**: For transactions with identical natural keys within a statement date window, the engine tracks the intra-statement occurrence count $k \in \{1, 2, \dots\}$:
     $$\text{NaturalOccurrenceKey} = (\text{rail\_id}, \text{account\_id}, \text{bank\_reference}, \text{booking\_date}, \text{amount\_cents}, \text{direction}, k)$$
   - **Deduplication Routing & Ledger Protection**:
     - If occurrence $k$ already exists in an active statement for that rail and account, the line is inserted into `statement_lines` with `reconciliation_status = 'SKIPPED_ALREADY_PROCESSED'` and `original_line_id` pointing to the existing line. **Crucially, it is excluded from `reconciliation_runs`**, preventing duplicate matches, false `UNMATCHED_EXTERNAL` breaks, and duplicate ledger postings.
     - If occurrence $k$ is net-new, it is inserted with `reconciliation_status = 'PENDING'` and queued for active matching.
   - **Statement Supersession**: When an amended statement explicitly replaces an earlier file, `clearing_statements.replaces_statement_id` records the superseded statement ID, and the prior statement status transitions to `SUPERSEDED`.
   - **File Balance Verification Invariant**: Persisting all lines (including skipped duplicates) in `statement_lines` preserves raw file mathematical auditability:
     $$\text{ClosingBalance} - \text{OpeningBalance} \equiv \sum \text{Credits} - \sum \text{Debits}$$
     without double-counting financial transactions in the double-entry general ledger.

---

## 5. Break Management & Exception Handling Subsystem

### 5.1 Break Classification Taxonomy
When reconciliation fails to pair transactions deterministically, the engine isolates the discrepancy into one of **six discrete operational break classes**:

```
+─────────────────────────────────────────────────────────────────────────────+
|                         BREAK CLASSIFICATION TAXONOMY                       |
+──────────────────────────┬──────────────────────────────────────────────────+
| Break Class              | Operational Root Cause                           |
+──────────────────────────┼──────────────────────────────────────────────────+
| UNMATCHED_INTERNAL       | Transaction recorded internally in gateway ledger|
|                          | but missing from external bank statement file.   |
|                          | (e.g., dropped switch message, rail outage).     |
+──────────────────────────┼──────────────────────────────────────────────────+
| UNMATCHED_EXTERNAL       | External bank line present on statement but no   |
|                          | matching internal intent exists. (Unallocated    |
|                          | cash deposit, direct wire from unknown customer).|
+──────────────────────────┼──────────────────────────────────────────────────+
| AMOUNT_MISMATCH          | References match deterministically, but net cash |
|                          | diverges beyond authorized fee schedule.         |
|                          | (Unexpected switch interchange downgrade).       |
+──────────────────────────┼──────────────────────────────────────────────────+
| TIMING_DELAY             | Internal transaction authorized near cut-off;    |
|                          | bank clears transaction in subsequent cycle.     |
+──────────────────────────┼──────────────────────────────────────────────────+
| FRACTIONAL_ROUNDING_BREAK| Sum of fractional investor waterfall splits      |
|                          | differs by 1-2 cents from inbound collection.    |
+──────────────────────────┼──────────────────────────────────────────────────+
| CHAIN_REORG_BREAK        | Blockchain transaction previously confirmed is   |
|                          | dropped due to L1/L2 consensus reorganization.   |
+──────────────────────────┴──────────────────────────────────────────────────+
```

### 5.2 Break Lifecycle Finite State Machine (FSM)
Every break transitions through an audited, deterministic state machine:

```
           ┌───────────────────────┐
           │       DETECTED        │
           └───────────┬───────────┘
                       │
                       ▼
         ┌───────────────────────────┐
         │       INVESTIGATING       │◄────────────────┐
         └─────┬───────┬───────┬─────┘                 │
               │       │       │                       │
        Within │Timing │Timing │Exceeds                │
     Tolerance │Cleared│Expired│Tolerance              │
               │Next   │(>2    │                       │
               │Cycle  │Cycles)│                       │ Checker
               ▼       │       ▼                       │ Rejects
        ┌──────────────┼───────┬────────────────┐      │
        │AUTO_REMEDIATED│      │ MANUAL_PENDING ├──────┘
        └──────────────┼───────┴───────┬────────┘
                       │               │
                       │ ┌─────────────┴─────────────┐
                       │ │ Maker Proposes Adjustment │
                       │ │  Checker Approves Review  │
                       ▼ ▼                           ▼
              ┌────────────────┐            ┌────────────────┐
              │    RESOLVED    │            │  WRITTEN_OFF   │
              └────────────────┘            └────────────────┘
```

- `DETECTED`: Initial break identified by the matching topology engine.
- `INVESTIGATING`: Automated diagnostic probe checks rail APIs, verifies secondary keys, or holds timing-drift breaks for up to 2 subsequent settlement cycles.
- `AUTO_REMEDIATED`: Break fell within quantitative tolerance ($|\Delta \text{Amount}| \le \min(\$2.00, 15\text{ bps})$) and was automatically resolved via fee write-off or suspense parking.
- `MANUAL_PENDING`: Discrepancy exceeds automated limits or grace cycles; queued for Operations Analyst review under Maker-Checker dual authorization.
- `RESOLVED`: Discrepancy resolved via force match, subsequent cycle clearance, customer return, or ledger adjustment.
- `WRITTEN_OFF`: Irrecoverable discrepancy written off to operational expense with Executive approval.

### 5.3 Automated Remediation & Tolerance Allocation
To maintain high straight-through processing (STP) rates and avoid operational fatigue from sub-dollar discrepancies:
- **Automated Fee Write-Off**: If an `AMOUNT_MISMATCH` exhibits an absolute variance:
  $$|\Delta \text{Amount}| \le \min(\$2.00, \text{InternalTransaction.GrossAmount} \times 15\text{ bps})$$
  *(or equivalently: $|\Delta \text{Amount}| \le \$2.00 \text{ AND } |\Delta \text{Amount}| \le \text{InternalTransaction.GrossAmount} \times 15\text{ bps}$)*
  The engine automatically marks the break `AUTO_REMEDIATED` with action `AUTO_FEE_WRITE_OFF` and generates a double-entry journal posting:
  - `DEBIT: 510000 - Bank Switch Fee Variance Expense`
  - `CREDIT: 110100 - Bank Settlement Nostro Account`
  This conjunction guarantees that large-value transfers cannot auto-write-off substantial dollar amounts (capping all auto-remediations strictly at $2.00), while small transactions remain bounded by the 15 bps limit, preserving the $500 Maker-Checker threshold under Section 5.4.
- **Unallocated Cash Parking**: If an `UNMATCHED_EXTERNAL` credit arrives without an internal intent, the funds cannot be absorbed as revenue. The engine auto-posts:
  - `DEBIT: 110100 - Bank Settlement Nostro Account`
  - `CREDIT: 240900 - Unidentified Receipts / Suspense Liability`
  The break is queued for Treasury Ops outreach to the sending bank.
- **Timing Drift Grace Handling**: Transactions flagged with break class `TIMING_DELAY` (authorized within 2 hours of rail cut-off) remain in `INVESTIGATING` status with an automated cycle-hold counter (up to 2 subsequent settlement cycles) before escalating. If the matching clearing record arrives in a subsequent cycle within this grace window, the break automatically transitions from `INVESTIGATING` to `RESOLVED` (action: `AUTO_MATCHED_SUBSEQUENT_CYCLE`). If 2 subsequent settlement cycles elapse without clearance, the break automatically escalates from `INVESTIGATING` to `MANUAL_PENDING` with an operational alert generated for analyst review. This avoids introducing unbacked ghost statuses and aligns strictly with the 6-state `recon_break_status` enum.

### 5.4 Maker-Checker Dual Authorization (Four-Eyes Principle)
Under Sarbanes-Oxley (SOX), SOC 1 Type II, and PCI-DSS compliance mandates, no single individual may unilaterally modify ledger balances or force-match broken financial records.

The engine enforces a strict **Maker-Checker Dual Control Policy**:
- **Threshold**: Any manual break resolution, force match, or journal adjustment involving a variance:
  $$\text{DiscrepancyAmount} > \$500.00 \text{ (or equivalent foreign currency)}$$
  requires explicit two-person dual authorization.
- **Workflow**:
  1. **Maker (Operations Analyst)**: Inspects the broken record in the Operations Portal. Selects resolution action (e.g., `MANUAL_FORCE_MATCH`, `ADJUSTMENT_JOURNAL`, `RETURN_TO_REMITTER`), uploads supporting bank documentation, and enters a detailed audit rationale. The break transitions to `MANUAL_PENDING`.
  2. **Checker (Operations Manager / Treasury Director)**: Receives the review task in the authorization queue. The checker cannot be the same user who initiated the maker request (`maker_id != checker_id`). Upon checker approval:
     - The resolution action is executed.
     - The corrective ledger journal is committed.
     - `reconciliation_breaks.resolved_by` and `reconciliation_breaks.approved_by` are permanently stamped alongside the cryptographic audit hash.
  3. **Checker Rejection & Re-investigation**: If the Checker rejects the proposed resolution (e.g., due to insufficient bank corroboration, unverified fee discrepancy, or improper general ledger allocation):
     - The break transitions from `MANUAL_PENDING` back to `INVESTIGATING`.
     - The Checker's mandatory rejection rationale is recorded in `reconciliation_breaks.notes` and appended to the immutable audit log.
     - The Maker receives an operational alert to collect additional documentation, consult the bank rail partner, or formulate a revised adjustment proposal.

### 5.5 Aging SLA Matrix & Regulatory Escalation
Breaks are actively tracked against strict aging SLAs to ensure timely financial reporting:

```
+─────────────────────────────────────────────────────────────────────────────+
|                         AGING SLA & ESCALATION MATRIX                       |
+──────────┬───────────────────┬──────────────────┬───────────────────────────+
| Tier     | Age Window        | Severity Level   | Operational Action        |
+──────────┼───────────────────┼──────────────────┼───────────────────────────+
| Tier 1   | 0 - 24 Hours      | Low / Warning    | Automated rail API status |
|          |                   |                  | inquiry; automated retry. |
+──────────┼───────────────────┼──────────────────┼───────────────────────────+
| Tier 2   | 24 - 48 Hours     | Medium / Action  | Assigned to Ops Analyst;  |
|          |                   |                  | Merchant reserve hold.    |
+──────────┼───────────────────┼──────────────────┼───────────────────────────+
| Tier 3   | 48 Hours - 7 Days | High / Critical  | Escalated to Treasury     |
|          |                   |                  | Manager; Formal bank rail |
|          |                   |                  | dispute ticket filed.     |
+──────────┼───────────────────┼──────────────────┼───────────────────────────+
| Tier 4   | > 7 Days          | Severe / Breach  | Executive notification    |
|          |                   |                  | (VP Finance & Legal); SAR |
|          |                   |                  | regulatory filing review. |
+──────────┴───────────────────┴──────────────────┴───────────────────────────+
```

---

## 6. FinOps & Performance Optimizations

Reconciliation systems frequently suffer severe performance degradation and cloud cost overruns during end-of-month and end-of-year accounting closures. The engine incorporates cutting-edge FinOps optimizations across database storage, compute autoscaling, and cloud network transit.

### 6.1 PostgreSQL Declarative Partitioning & Indexing Strategy
In a platform processing 10 million transactions daily, `statement_lines` and `reconciliation_matches` accumulate hundreds of millions of records annually. Unpartitioned tables cause B-tree index bloat, severe buffer pool thrashing, and prolonged query times.

#### Declarative Range Partitioning
The engine partitions both high-volume tables by monthly date ranges:
- `statement_lines`: Partitioned by `RANGE (booking_date)`:
  - `statement_lines_y2026m11`: `FROM ('2026-11-01') TO ('2026-12-01')`
  - `statement_lines_default`: Catches out-of-range dates.
- `reconciliation_matches`: Partitioned by `RANGE (created_at)`:
  - `reconciliation_matches_y2026m11`: `FROM ('2026-11-01 00:00:00+00') TO ('2026-12-01 00:00:00+00')`
  - `reconciliation_matches_default`: Catches out-of-range timestamps.

#### Partition Pruning & Query Economics
When an EOD reconciliation job runs for booking date `2026-11-11`, the PostgreSQL query planner applies **Partition Pruning**, eliminating 98% of table partitions from the query execution tree. Disk I/O drops by over 95%, and hot partition indexes remain fully resident in database RAM (`shared_buffers`).

#### $O(1)$ Historical Archival
Retaining financial data beyond regulatory mandates (typically 7 years) incurs ongoing database storage costs. Dropping historical partitions is an instantaneous $O(1)$ DDL operation:
```sql
ALTER TABLE statement_lines DETACH PARTITION statement_lines_y2019m11;
DROP TABLE statement_lines_y2019m11;
```
This reclaims disk space instantly without table-locking overhead, row-by-row deletions, or table bloat associated with PostgreSQL `VACUUM`.

#### Composite Primary Keys
To comply with PostgreSQL partitioning requirements (where partition keys must be part of the primary key), composite keys are enforced:
- `statement_lines`: `PRIMARY KEY (id, booking_date)`
- `reconciliation_matches`: `PRIMARY KEY (id, created_at)`

### 6.2 High-Throughput In-Memory & Streaming Acceleration
- **Redis Sorted Sets (`ZADD`)**: Intraday streaming candidate lookups execute in $O(\log N)$ time, achieving candidate search latencies $< 1.5\text{ ms}$.
- **PostgreSQL Multi-Row Bulk Insert (`COPY`)**: During EOD statement ingestion, workers bypass individual `INSERT` statements by streaming records via PostgreSQL binary `COPY` protocol in chunks of 5,000 records, cutting Write-Ahead Log (WAL) overhead by 80%.

### 6.3 Cloud Infrastructure Cost Optimization (FinOps)

#### Karpenter Graviton Spot Auto-Scaling
EOD bank statement reconciliation is characterized by short, compute-intensive bursts (typically 30 to 90 minutes following bank cut-off windows at 17:00 and 23:59). Running oversized on-demand x86 worker pools 24/7 wastes significant cloud budget.
- The platform deploys reconciliation workers on AWS Elastic Kubernetes Service (EKS) managed by **Karpenter**.
- Karpenter dynamically provisions **AWS Graviton3 / Graviton4 (ARM64)** Spot instances (`c7g.2xlarge` / `c8g.2xlarge`) when the reconciliation queue depth spikes.
- Upon job completion, the nodes are terminated immediately.
- Utilizing ARM64 Spot architecture yields a **60% to 70% reduction in compute costs** compared to equivalent x86 on-demand instances (see [FinOps TCO Model](../06-cloud-cost-efficiency/finops-tco-model.md)).

#### S3 Gateway VPC Endpoints
Bank statement ingestion involves uploading and downloading hundreds of gigabytes of raw ISO XML, MT940, and image files. Routing S3 traffic through AWS NAT Gateways incurs standard data processing charges of **\$0.045 per gigabyte**.
- The infrastructure establishes **AWS S3 Gateway VPC Endpoints** within the VPC routing tables.
- All S3 traffic between Kubernetes worker pods and statement storage buckets flows across the private AWS backbone at **\$0.00 transfer cost**, eliminating thousands of dollars in monthly NAT gateway charges.

### 6.4 S3 Storage Tiering & Lifecycle Economics
Raw banking statements must be retained for 7 to 10 years to satisfy statutory audit mandates, but are rarely accessed after 90 days.
- **0 - 90 Days**: Raw statements reside in **S3 Intelligent-Tiering** for immediate access during active reconciliation and dispute handling (\$0.023 / GB / month).
- **Day 91+**: AWS S3 Lifecycle policies automatically transition statement archives to **S3 Glacier Instant Retrieval** (\$0.004 / GB / month).
- **Economic Impact**: Achieves a **68% storage cost reduction** while retaining sub-second medical/financial retrieval capabilities during regulatory audits without retrieval delays.

---

## 7. Operational Schema & Interface Alignment

### 7.1 Database Table Mappings (schema.sql)
The Universal Pluggable Reconciliation Engine maps directly to five dedicated database tables defined in [schema.sql](../../schemas/schema.sql):

```
+─────────────────────────────────────────────────────────────────────────────+
|                        RECONCILIATION DATABASE ENTITIES                     |
+──────────────────────────┬──────────────────────────────────────────────────+
| Table Name               | Description & Partitioning Strategy              |
+──────────────────────────┼──────────────────────────────────────────────────+
| clearing_statements      | Stores ingested statement metadata, bank codes,  |
|                          | opening/closing balances, Level-1 file hash      |
|                          | (file_hash SHA-256), and optional                |
|                          | replaces_statement_id for amended statements.    |
+──────────────────────────┼──────────────────────────────────────────────────+
| statement_lines          | Individual normalized bank lines. Partitioned by |
| (Partitioned)            | RANGE (booking_date) with composite primary key  |
|                          | (id, booking_date), Level-2 unique constraint    |
|                          | (statement_id, line_sequence, booking_date), and |
|                          | index on line_hash and natural key lookup.        |
+──────────────────────────┼──────────────────────────────────────────────────+
| reconciliation_runs      | Records batch run executions, execution metrics, |
|                          | target topology, and completion status.          |
+──────────────────────────┼──────────────────────────────────────────────────+
| reconciliation_matches   | Records matched pairs/groups between internal    |
| (Partitioned)            | transactions and external statement lines.       |
|                          | Partitioned by RANGE (created_at) with composite |
|                          | primary key (id, created_at).                    |
+──────────────────────────┼──────────────────────────────────────────────────+
| reconciliation_breaks    | Records discrepancies, break classifications,    |
|                          | FSM lifecycle states, aging tracking, and maker- |
|                          | checker resolution audit trails. Foreign key     |
|                          | settlement_batch_id references settlement_batches|
|                          | with ON DELETE SET NULL.                         |
+──────────────────────────┴──────────────────────────────────────────────────+
```

#### Enumeration Mappings
The operational schema defines eight core PostgreSQL enumerations:
- `statement_format_type`: `CAMT_053`, `CAMT_052`, `CAMT_054`, `MT940`, `MT942`, `BAI2`, `NACHA`, `CSV`, `JSON_WEBHOOK`, `BLOCKCHAIN_RPC`.
- `statement_status`: `PENDING_INGESTION`, `PARSED`, `PROCESSING`, `RECONCILED`, `HAS_BREAKS`, `SUPERSEDED`, `FAILED`.
- `reconciliation_topology_type`: `DIRECT_1_TO_1`, `NETTING_N_TO_M`, `TRIPARTITE_ESCROW`, `STREAMING_INTRADAY`, `CRYPTOGRAPHIC_ON_CHAIN`.
- `reconciliation_run_status`: `INITIALIZED`, `MATCHING`, `COMPLETED`, `COMPLETED_WITH_BREAKS`, `FAILED`.
- `recon_match_status`: `EXACT_MATCH`, `NET_FEE_MATCH`, `HEURISTIC_MATCH`, `SPLIT_MATCH`, `ON_CHAIN_CONFIRMED`.
- `recon_break_class`: `UNMATCHED_INTERNAL`, `UNMATCHED_EXTERNAL`, `AMOUNT_MISMATCH`, `TIMING_DELAY`, `FRACTIONAL_ROUNDING_BREAK`, `CHAIN_REORG_BREAK`.
- `recon_break_status`: `DETECTED`, `INVESTIGATING`, `AUTO_REMEDIATED`, `MANUAL_PENDING`, `RESOLVED`, `WRITTEN_OFF`.
- `break_resolution_action`: `AUTO_FEE_WRITE_OFF`, `MANUAL_FORCE_MATCH`, `POST_TO_SUSPENSE`, `ADJUSTMENT_JOURNAL`, `RETURN_TO_REMITTER`, `WRITE_OFF_LOSS`.

### 7.2 API & Protobuf Service Contracts
The engine exposes unified control planes across REST and gRPC:

#### REST Endpoints ([openapi.yaml](../../schemas/openapi.yaml))
- `POST /v1/reconciliation/statements`: Uploads a bank clearing statement file (multipart/form-data or raw stream).
- `POST /v1/reconciliation/runs`: Triggers an on-demand reconciliation run for a specific rail, topology, and date window.
- `GET /v1/reconciliation/runs/{id}`: Retrieves run execution status, matched counts, and variance summaries.
- `GET /v1/reconciliation/breaks`: Queries open discrepancies with filters for rail, break class, status, and aging bucket.
- `POST /v1/reconciliation/breaks/{id}/resolve`: Submits a Maker-Checker resolution action (RFC 7807 problem details returned on validation error).

#### gRPC Service ([service.proto](../../schemas/service.proto))
```protobuf
service ReconciliationService {
  rpc IngestStatement(IngestStatementRequest) returns (IngestStatementResponse);
  rpc TriggerReconciliation(TriggerReconciliationRequest) returns (TriggerReconciliationResponse);
  rpc GetReconciliationSummary(GetReconciliationSummaryRequest) returns (GetReconciliationSummaryResponse);
  rpc ListBreaks(ListBreaksRequest) returns (ListBreaksResponse);
  rpc ResolveBreak(ResolveBreakRequest) returns (ResolveBreakResponse);
}
```

### 7.3 Cross-Subsystem Integration Links
For comprehensive technical details on adjacent domain subsystems, refer to:
- [Extensibility SPI](../01-architecture-overview/extensibility-spi.md): Defines `ReconciliationRailAdapter` and declarative `ReconciliationProfile` specifications.
- [Domain Boundaries](../01-architecture-overview/domain-boundaries.md): Establishes authoritative domain boundaries and entity ownership.
- [Double-Entry Ledger](double-entry-ledger.md): Governs the Chart of Accounts, immutable journal postings, and balance conservation laws.
- [Settlement Engine](settlement-engine.md): Details batch aggregation, merchant payout calculation, and net settlement sweeps.
- [Automated Reconciliation](../05-reliability-and-performance/automated-reconciliation.md): High-level overview of reliability, 3-way reconciliation, and heuristic matching.
- [PostgreSQL DDL](../04-data-models-and-schemas/postgresql-ddl.md): Physical database schema, tablespaces, and partitioning strategies.
- [FinOps TCO Model](../06-cloud-cost-efficiency/finops-tco-model.md): Multi-year cloud cost modeling and infrastructure optimization.
