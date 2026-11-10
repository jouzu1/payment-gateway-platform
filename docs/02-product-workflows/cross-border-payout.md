# Cross-Border Remittance & Multi-Currency Payout Architecture

## 1. Domain Overview & Operational Complexities

Cross-border remittance enables merchants and enterprise platforms to send funds across international borders and disparate currency jurisdictions (e.g. `USD -> IDR`, `EUR -> PHP`, `SGD -> INR`). Unlike domestic payments, cross-border transactions must solve for:
1. **Foreign Exchange (FX) Volatility**: Exchange rates fluctuate continuously; guaranteed quotes are required to eliminate slippage risk for clients.
2. **Nostro / Vostro Multi-Currency Accounting**: Maintaining pre-funded liquidity pools in foreign banks across global time zones.
3. **Regulatory Sanctions & AML Screening**: Mandatory pre-transaction compliance checks against international watchlists (OFAC SDN, UN Consolidated, EU Financial Sanctions, PEP lists).
4. **Correspondent Banking & ISO 20022 Messaging**: Translating instructions across SWIFT networks (`pacs.008`, `camt.053`) and domestic clearing partners.

---

## 2. Technical Workflow Architecture

```
[ 1. Guaranteed FX Quote ] ──► [ 2. AML Sanctions Check ] ──► [ 3. Treasury Liquidity ] ──► [ 4. Corridor Dispatch ] ──► [ 5. ISO 20022 Recon ]
  Rate Lock with 60s TTL         OFAC / UN / PEP Fuzzy         Nostro Pool Check / Hedge      Last-Mile Partner Bank        camt.053 Ingestion
```

### Stage 1: Guaranteed FX Quote Request & Spread Management
1. The client requests a rate quote via `POST /v1/payouts/quotes` specifying `source_currency`, `target_currency`, and `source_amount`.
2. The `FXQuoteService` queries institutional Liquidity Provider (LP) streaming APIs (e.g., LMAX, Currenxy, partner banks).
3. The platform applies a dynamic spread markup based on the merchant’s contract tier:
   $$\text{ClientRate} = \text{SpotMidRate} \times \left(1 - \frac{\text{SpreadBps}}{10,000}\right)$$
4. A guaranteed quote is generated with a deterministic 60-second expiration (`expires_at = now() + 60s`).
5. The quote is cached in Redis with a 60-second TTL and recorded in PostgreSQL (`fx_quotes`).

### Stage 2: Real-Time Sanctions Screening & Purpose Validation
1. Client accepts the quote and submits the payout order.
2. Prior to any money movement, counterparty details (Sender Name, Beneficiary Name, Country, Address, Date of Birth) are dispatched to the `SanctionsScreeningService`.
3. Evaluates identities using tokenized Levenshtein and Jaro-Winkler fuzzy algorithms against:
   - US Treasury OFAC Specially Designated Nationals (SDN)
   - United Nations Security Council Consolidated Sanctions
   - EU Consolidated Financial Sanctions List
   - Politically Exposed Persons (PEP) Database
4. Evaluation Decision Matrix:
   - Match Score $< 0.30$: Clean $\rightarrow$ Order approved to proceed.
   - Match Score $0.30 \le \text{Score} < 0.85$: Potential Match $\rightarrow$ Routed to L2 Human Compliance Review queue (Hold state).
   - Match Score $\ge 0.85$: Confirmed Match $\rightarrow$ State set to `BLOCKED_SANCTIONS`; payout permanently halted.

### Stage 3: Treasury Liquidity & Nostro/Vostro Allocation
1. The system evaluates whether the target currency nostro pool held at our local partner bank has sufficient pre-funded liquidity.
2. If sufficient: The platform earmarks the target amount from the local foreign currency nostro account.
3. If insufficient: The system triggers an automated spot FX deal execution with the liquidity provider to replenish the nostro pool.

### Stage 4: Corridor Routing (Direct Local Partner vs. SWIFT Correspondent)
The `CorridorRoutingEngine` evaluates the optimal transmission path:
- **Direct Local Payout Partner (Last-Mile)**:
  - If we maintain a pre-funded nostro account in the destination country, the payout order is dispatched directly to the local partner bank via local instant rail (e.g. BI-FAST in Indonesia, UPI in India).
  - Payout settles in $< 5\text{ seconds}$ at minimal interbank cost.
- **Correspondent Banking Network (SWIFT / ISO 20022)**:
  - If no direct local rail is available, the transaction is formatted as an ISO 20022 `pacs.008.001.08` or SWIFT MT103 credit transfer.
  - Dispatched via SWIFT Alliance Gateway over correspondent networks (e.g., JP Morgan, Citi).
  - Fee allocation rules:
    - `OUR`: Sender pays all correspondent charges.
    - `BEN`: Beneficiary absorbs correspondent intermediary charges.
    - `SHA`: Shared costs (Sender pays originating bank fee, beneficiary absorbs intermediary fee).

### Stage 5: ISO 20022 Reconciliation (`camt.053`)
At the close of business, our partner banks emit ISO 20022 `camt.053` (Bank-to-Customer Statement) files. The settlement engine ingests these XML statements, parses the unique `EndToEndId`, and marks the payout order as definitively settled in the double-entry ledger.

---

## 3. Cross-Border State Machine

```
               +----------------------+
               |      QUOTE_HELD      |
               +----------+-----------+
                          | accept_quote() [Within 60s TTL]
               +----------v-----------+
               |   PENDING_SCREENING  |
               +----------+-----------+
                          |
               +----------+----------+
               | Clean               | Confirmed Sanction Match
    +----------v-----------+  +------v--------------+
    |  SCREENING_APPROVED  |  |  BLOCKED_SANCTIONS   | (Terminal: Blocked)
    +----------+-----------+  +---------------------+
               |
               | check_nostro_liquidity()
    +----------v-----------+
    |   RESERVED_TREASURY  |
    +----------+-----------+
               | dispatch_corridor()
    +----------v-----------+
    |  DISPATCHED_TO_CORR  |
    +----------+-----------+
               |
         +-----+----------------+
         |                      |
+--------v------+      +--------v------+
|    SETTLED    |      |     FAILED    | (Corridor Reject)
+---------------+      +--------+------+
                                | auto_refund_fx()
                       +--------v------+
                       |    REFUNDED   | (Terminal: Funds Returned)
                       +---------------+
```

---

## 4. Nostro / Vostro Accounting & Multi-Currency Journaling

Cross-border payouts require exchanging currencies while preserving the fundamental double-entry invariant:

$$\sum \text{Debits} \equiv \sum \text{Credits}$$

Because currencies differ (e.g., debiting USD and crediting IDR), the ledger connects the two currency sides using an intermediate **FX Clearing Account**.

### Double-Entry Accounting Legs for USD -> IDR Cross-Border Payout:
Assume client sends \$1,000.00 USD converted to IDR 15,650,000 at rate 15,650.00, with platform FX markup fee of \$10.00:

#### Leg 1: Source Currency (USD Journal Entry)
| Leg | Account Number | Account Name | Direction | Amount (USD Minor) |
| :--- | :--- | :--- | :--- | :--- |
| 1 | `210100-USD` | Merchant Available Balance USD | **DEBIT** | `100000` (\$1,000.00) |
| 2 | `110500-USD` | Platform FX Clearing Account USD | **CREDIT** | `99000` (\$990.00) |
| 3 | `420000-USD` | Platform FX Spread Fee Revenue | **CREDIT** | `1000` (\$10.00) |
| **Sum**| | | | **\$1,000.00 == \$1,000.00** |

#### Leg 2: Destination Currency (IDR Journal Entry)
| Leg | Account Number | Account Name | Direction | Amount (IDR Minor) |
| :--- | :--- | :--- | :--- | :--- |
| 1 | `110500-IDR` | Platform FX Clearing Account IDR | **DEBIT** | `1549350000` (IDR 15,493,500) |
| 2 | `110100-IDR` | Nostro Bank Partner Jakarta Account | **CREDIT** | `1549350000` (IDR 15,493,500) |
| **Sum**| | | | **IDR 15,493,500 == IDR 15,493,500** |

The FX Clearing accounts (`110500-USD` and `110500-IDR`) are audited daily to assess the platform’s net realized FX trading gain or loss.

---

## 5. ISO 20022 Cross-Border Messaging Standards

| Message Type | XML Schema Name | Business Function in Cross-Border Flow |
| :--- | :--- | :--- |
| `pain.001.001.09` | CustomerCreditTransferInitiation | Batch payment instruction file submitted to correspondent banks. |
| `pacs.008.001.08` | FinancialInstitutionalCustomerCreditTransfer | Interbank real-time credit transfer instruction carrying remittance details. |
| `pacs.002.001.10` | PaymentStatusReport | Bank clearing status confirmation (`ACCP` accepted, `RJCT` rejected). |
| `camt.053.001.08` | BankToCustomerStatement | End-of-day electronic bank statement listing all settled debits and credits. |
| `camt.054.001.08` | BankToCustomerDebitCreditNotification | Real-time debit or credit event notification on our foreign nostro account. |
