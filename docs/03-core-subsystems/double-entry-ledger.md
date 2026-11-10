# Immutable Double-Entry Ledger Subsystem

## 1. Subsystem Overview & Financial Invariants

The Double-Entry Ledger Subsystem is the immutable financial core and single source of truth for all value movements within the Payment Gateway Platform. It guarantees that money cannot be created or destroyed out of thin air, ensuring that every financial transfer is fully accounted for with cryptographic precision.

### Core Mathematical Invariants:
1. **The Fundamental Accounting Equation**:
   $$\text{Assets} = \text{Liabilities} + \text{Equity}$$
2. **Zero-Sum Balanced Journal Postings**:
   $$\sum_{i=1}^{N} \text{Debit}_i \equiv \sum_{j=1}^{M} \text{Credit}_j \quad \forall \text{ JournalEntry}$$
3. **Strict Append-Only Immutability**:
   Current balances are derived projections of immutable debit/credit journal legs. `UPDATE` and `DELETE` operations are completely barred at the database trigger level.

---

## 2. Rules of Debits and Credits

The ledger adheres to the classic double-entry bookkeeping convention:

| Account Category | Normal Balance | Debit Effect ($\text{DEBIT}$) | Credit Effect ($\text{CREDIT}$) | Example Platform Accounts |
| :--- | :--- | :--- | :--- | :--- |
| **Asset** | Debit (+) | **Increases (+)** | Decreases (-) | Bank Settlement Nostro, Cash in Transit, Loan Receivables |
| **Liability** | Credit (+) | Decreases (-) | **Increases (+)** | Merchant Available Balance, User Wallet, Custodian Escrow |
| **Equity** | Credit (+) | Decreases (-) | **Increases (+)** | Retained Earnings, Platform Capital Reserves |
| **Revenue** | Credit (+) | Decreases (-) | **Increases (+)** | Gateway Processing Fees, FX Spread Revenue, Servicing Fees |
| **Expense** | Debit (+) | **Increases (+)** | Decreases (-) | Bank Switch Interchange Fees, Network Assessment Costs |

---

## 3. Standard Chart of Accounts (COA) Hierarchy

The platform defines a standardized 6-digit numeric account hierarchy:

```
100000 - ASSETS
  ├── 110000 - Cash & Bank Settlement (Nostro)
  │     ├── 110100 - Domestic Bank Partner A Settlement (USD)
  │     ├── 110200 - Domestic Bank Partner B Settlement (IDR)
  │     ├── 110300 - Cross-Border Correspondent Clearing (EUR)
  │     └── 110400 - Custodian Bank FBO Trust Escrow Cash
  ├── 120000 - Funds in Transit (Clearing In-Flight)
  └── 130000 - Loans Receivable (P2P Lending Principal Asset)

200000 - LIABILITIES
  ├── 210000 - Merchant Stored Balances
  │     ├── 210100 - Merchant Available Balance
  │     └── 210200 - Merchant Pending / Hold Balance
  ├── 220000 - Customer / User Stored Value
  ├── 230000 - Escrow Accounts
  │     ├── 230100 - P2P Lending Investor Available Escrow
  │     ├── 230200 - P2P Lending Committed Loan Principal Escrow
  │     ├── 230300 - Borrower In-Transit Repayment Escrow
  │     └── 230400 - Statutory Withholding Tax Escrow
  └── 240000 - Payables to Clearing Networks

300000 - EQUITY
  ├── 310000 - Retained Earnings
  └── 320000 - Platform Capital Reserve

400000 - REVENUE
  ├── 410000 - Gateway Processing Fees
  ├── 420000 - FX Spread & Markup Revenue
  └── 430000 - P2P Lending Origination & Servicing Fees

500000 - EXPENSES
  ├── 510000 - Partner Bank Switch Costs
  └── 520000 - Interchange & Card Scheme Assessment Costs
```

---

## 4. Multi-Currency Representation & Minor Unit Convention

To eliminate floating-point rounding errors inherent in IEEE 754 representations (e.g. `0.1 + 0.2 = 0.30000000000000004`):
1. **Unsigned 64-Bit Integers (`BIGINT`)**: All monetary figures are stored in the smallest minor currency unit (cents, pence, satoshis).
   - \$100.50 USD is stored as `10050` (scale 2).
   - ¥10,000 JPY is stored as `10000` (scale 0).
   - 1.50000000 BTC is stored as `150000000` (scale 8).
2. **Multi-Currency Balancing**: Currencies cannot be combined across legs within a single balancing calculation. Multi-currency cross-border transfers are balanced via intermediate **FX Clearing Accounts** (`110500`), creating two independent, balanced journal entries linked by a common correlation ID.

---

## 5. High-Throughput Concurrency Architecture (LMAX Disruptor Model)

Traditional relational database row locking (`SELECT balance FROM accounts FOR UPDATE`) collapses under high transaction volume. When 10,000 TPS attempt to update the same settlement account, database connection pools exhaust, lock wait queues explode, and latencies exceed 2,000ms.

To solve this, the core ledger engine implements the **Single-Writer Partitioned Actor Pattern** (inspired by the LMAX Disruptor architecture and TigerBeetle):

```
                                [ Incoming Balance Transfer Events ]
                                                 │
                                       (Hash on Account ID)
                                                 │
            +------------------------------------+------------------------------------+
            │                                    │                                    │
            ▼                                    ▼                                    ▼
   [ Partition Worker 0 ]               [ Partition Worker 1 ]               [ Partition Worker N-1 ]
   (Accounts 0x00 - 0x3F)               (Accounts 0x40 - 0x7F)               (Accounts 0xC0 - 0xFF)
            │                                    │                                    │
   +------------------------+           +------------------------+           +------------------------+
   | LMAX Disruptor         |           | LMAX Disruptor         |           | LMAX Disruptor         |
   | RingBuffer (Lock-Free) |           | RingBuffer (Lock-Free) |           | RingBuffer (Lock-Free) |
   +------------------------+           +------------------------+           +------------------------+
            │                                    │                                    │
   [ Single Thread Processor ]          [ Single Thread Processor ]          [ Single Thread Processor ]
   - Validate balance in memory         - Validate balance in memory         - Validate balance in memory
   - Mutate state (Zero Lock!)          - Mutate state (Zero Lock!)          - Mutate state (Zero Lock!)
   - Append to Batch WAL buffer         - Append to Batch WAL buffer         - Append to Batch WAL buffer
            │                                    │                                    │
            +------------------------------------+------------------------------------+
                                                 │
                                                 ▼
                               [ Coalesced Group Commit to NVMe WAL ]
                               (Batch fsync 128 entries per write)
                                                 │
                                                 ▼
                               [ Emitted Event Stream to Kafka ]
```

### Architectural Principles:
1. **Mechanical Sympathy**: Each partition worker thread is pinned to an isolated physical CPU core, eliminating OS context switching and maximizing L1/L2 cache locality.
2. **Lock-Free Ring Buffers**: Uses pre-allocated circular ring buffers with memory barriers (`volatile` sequence counters), eliminating thread synchronization locks.
3. **Coalesced Group Commits**: Transactions are validated in microsecond memory space and grouped into batches (up to 128 entries or 1ms window) before issuing a single sequential `fsync` to NVMe disk.
4. **Performance Benchmark**: A single modern CPU core running this architecture processes **> 100,000 balance operations per second** with deterministic sub-millisecond execution.
