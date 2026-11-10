# Settlement & Automated Three-Way Reconciliation Engine

## 1. Subsystem Overview & Financial Settlement Models

The Settlement Engine governs the post-authorization clearing, netting, and funds payout processes for merchants, while ensuring zero financial discrepancies through automated three-way reconciliation against external banking statements.

### Core Objectives:
1. **Net Settlement Calculation**: Aggregates gross captured transactions, subtracts interchange and gateway processing fees, deducts refunds and chargeback reserves, and calculates net merchant payouts.
2. **Settlement Schedule Management**: Enforces contractual merchant settlement cycles ($T+0$, $T+1$, $T+2$, or rolling reserves for high-risk tiers).
3. **Automated Three-Way Matching**: Reconciles internal transaction event logs, double-entry ledger postings, and external bank clearing files (`camt.053`, MT940, BAI2) to detect and isolate financial breaks.

---

## 2. Merchant Settlement Schedules & Reserve Accounting

```
+--------------------------------------------------------------------------------------------------+
|                                    SETTLEMENT SCHEDULE TIERS                                     |
|                                                                                                  |
|   Tier 1: T+0 (Same-Day Real-Time Sweep)                                                         |
|   - Transactions captured before 14:00 UTC settle via instant rail at 17:00 UTC same day.        |
|                                                                                                  |
|   Tier 2: T+1 (Standard Business Day Settlement - Default)                                       |
|   - Daily transactions cut off at 23:59:59 UTC; net payout dispatched at 09:00 UTC on T+1.       |
|                                                                                                  |
|   Tier 3: T+2 / High-Risk Rolling Reserve                                                        |
|   - Standard settlement on T+2, with a 5% to 10% rolling reserve held for 90 to 180 days         |
|     to buffer against disputed chargebacks and refunds.                                          |
+--------------------------------------------------------------------------------------------------+
```

### Net Settlement Mathematical Formulation
For a merchant settlement batch $B$ over cut-off window $[t_{\text{start}}, t_{\text{end}}]$:

$$\text{NetPayout} = \sum \text{GrossCaptured} - \sum \text{GatewayFees} - \sum \text{Refunds} - \sum \text{Chargebacks} - \text{RollingReserveAddition} + \text{RollingReserveRelease}$$

Where:
- $\text{RollingReserveAddition} = \sum \text{GrossCaptured} \times \text{ReserveRate}$ (held in account `210200 - Merchant Pending Reserve`).
- $\text{RollingReserveRelease}$ = Funds maturing past the 90-day reserve hold window.

---

## 3. Automated Three-Way Reconciliation Architecture

Naive payment platforms assume external banks process transactions exactly as instructed. In production banking, discrepancies arise continually due to bank cut-off drifts, unannounced interchange fee adjustments, network drops, and delayed returns.

To guarantee zero audit discrepancies, the **Three-Way Reconciliation Engine** matches three independent sources of truth:

```
    [ Source 1: Gateway DB ]        [ Source 2: Double-Entry Ledger ]      [ Source 3: Bank Statement ]
   (Raw Transaction Event Logs)           (Journal Entry Legs)              (camt.053 / MT940 / BAI2)
                 \                                 |                                 /
                  \                                |                                /
                   v                               v                                v
            +-------------------------------------------------------------------------------+
            |                     Automated 3-Way Reconciliation Engine                     |
            +--------------------------------------+----------------------------------------+
                                                   |
                         +-------------------------+-------------------------+
                         |                                                   |
                         v                                                   v
             [ 100% Matched Records ]                              [ Discrepancy Break ]
             - Unique EndToEndId Matches                           - Break Type Identified
             - Amounts & Currencies Match                          - Isolated to Audit Queue
             - Fees Categorized to Sub-Accounts                    - Auto-Adjustment or Ops Ticket
                         |                                                   |
                         v                                                   v
             [ Finalize Daily Batch ]                              [ Dispute Resolution ]
             - Release Merchant Payout                             - Raise Bank Investigation
```

---

## 4. Statement Ingestion & Parsing Matrix

The engine features native streaming parsers for standard global banking clearing files:

| Bank Statement Standard | Format | Primary Usage / Jurisdictions | Key Extracted Fields |
| :--- | :--- | :--- | :--- |
| **ISO 20022 `camt.053`** | XML | Global / Modern Central Banks | `Ntry/Amt`, `NtryDtls/TxDtls/Refs/EndToEndId`, `BkTxCd` |
| **SWIFT MT940** | Structured Text | International Correspondent Banks | `:61:` (Value Date, Debit/Credit, Amount), `:86:` (Info) |
| **BAI2** | Comma-delimited | US Corporate Banking / Commercial Banks | Code `16` (Transaction detail), `495` (Total credits) |
| **NACHA Return File** | 94-char fixed | US ACH Automated Clearing House | Return Reason Codes (`R01` NSF, `R02` Account Closed) |

---

## 5. Discrepancy Classification & Automated Exception Handling

When reconciliation detects a non-matching record, it categorizes the discrepancy into one of four deterministic break classes:

### Break Class 1: Unmatched Internal Transaction (Missing Bank Settlement)
- *Condition*: Internal gateway shows `CAPTURED` or `DISPATCHED`, but the external bank statement contains no matching credit or debit after 24 hours.
- *Root Cause*: Bank drop, switch network timeout, or queue stalls.
- *Action*: Triggers automated bank inquiry API; if unconfirmed, reverses ledger hold and alerts merchant.

### Break Class 2: Orphan External Transaction (Unmatched Bank Entry)
- *Condition*: A credit or debit appears on the bank statement with no corresponding internal transaction reference.
- *Root Cause*: Payer sent direct bank transfer without using virtual account, or manual bank fee adjustment.
- *Action*: Posts to `Suspense / Unallocated Holding Account` (`230900`); creates priority operations ticket.

### Break Class 3: Fee Discrepancy
- *Condition*: Transaction ID matches, but net settlement amount diverges by a small variance ($\Delta < \$5.00$).
- *Root Cause*: Acquirer interchange tier adjustment or international assessment fee mismatch.
- *Action*: Automatically writes off variance to `510000 - Bank Switch Fee Variance Expense`, ensuring merchant payout is not stalled.

### Break Class 4: Timing Window Drift (Cut-off Skew)
- *Condition*: Transaction dispatched at 23:59:50 settles on the following day's bank statement.
- *Action*: System automatically tags the record as `TRANSIT_PENDING_NEXT_CYCLE` without flagging an error.
