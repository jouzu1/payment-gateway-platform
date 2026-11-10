# P2P Lending Escrow, Loan Origination & Waterfall Engine

## 1. Regulatory Context & Fund Segregation Mandates

Peer-to-Peer (P2P) lending platforms operate under strict regulatory supervision globally (e.g. SEC in the US, OJK Regulation POJK 10/2022 in Indonesia, FCA in the UK). Financial regulations strictly prohibit P2P lending operators from acting as deposit-taking institutions or touching borrower and investor capital directly.

### The For-Benefit-Of (FBO) Escrow Trust Mandate
All investor capital deposits and borrower repayments **MUST** reside inside a **Custodian Bank For-Benefit-Of (FBO) Trust Escrow Account**.
- The platform maintains legal authority to instruct fund movements via API, but possesses zero ownership of the funds.
- In the event of platform insolvency, FBO escrow funds are legally ring-fenced and shielded from platform creditors.
- Corporate operational accounts (payroll, office rent, corporate cash) are completely isolated from the escrow trust ledger.

```
+--------------------------------------------------------------------------------------------------+
|                            REGULATED CUSTODIAN BANK OMNIBUS TRUST                                |
|                                                                                                  |
|   +------------------------------------------------------------------------------------------+   |
|   |                      Bank Custodian FBO Escrow Account (Omnibus Pool)                    |   |
|   |                      Account Number: 110400-ESCROW-CUSTODIAN-BANK                        |   |
|   +---------------------------------------------+--------------------------------------------+   |
+-------------------------------------------------|------------------------------------------------+
                                                  | Shadow Sub-Ledger Accounting
                                                  v
+--------------------------------------------------------------------------------------------------+
|                         INTERNAL IMMUTABLE DOUBLE-ENTRY LEDGER SUB-ACCOUNTS                      |
|                                                                                                  |
|   +--------------------------+  +--------------------------+  +------------------------------+   |
|   | Investor Available Pool  |  | Committed Escrow Capital |  | Borrower In-Transit Repay    |   |
|   | (Account: 230100)        |  | (Account: 230200)        |  | (Account: 230300)            |   |
|   +--------------------------+  +--------------------------+  +------------------------------+   |
|                                                                                                  |
|   +--------------------------------------------------------+  +------------------------------+   |
|   | Statutory Tax Escrow (WHT Payable to Tax Authority)    |  | Platform Fee Liability       |   |
|   | (Account: 230400)                                      |  | (Account: 210500)            |   |
|   +--------------------------------------------------------+  +------------------------------+   |
+--------------------------------------------------------------------------------------------------+
```

---

## 2. End-to-End P2P Lending Technical Lifecycles

The lending engine coordinates four synchronized financial operations:
1. **Investor Capital Pooling**: Individual or institutional investors deposit funds into the FBO escrow.
2. **Borrower Loan Disbursement**: Capital is remitted from escrow directly to the borrower's verified bank account once a loan is 100% funded.
3. **Borrower Repayment Collection**: Inbound collections captured via Direct Debit mandates or dedicated Virtual Accounts.
4. **Mathematical Waterfall Split Engine**: Collects repayment installments and executes deterministic, fractional pro-rata distributions to investors.

---

## 3. The 4 Operational Lifecycles in Detail

### 3.1 Investor Capital Pooling Flow
1. An investor deposits \$10,000.00 via Payin (Virtual Account or Direct Debit).
2. Funds settle into the **Bank Custodian FBO Escrow Account**.
3. Ledger books a balanced liability:
   - `DEBIT: 110400 - Custodian Bank Escrow Cash Asset` (+\$10,000.00)
   - `CREDIT: 230100 - Investor Available Cash Balance Liability` (+\$10,000.00)
4. When the investor commits \$1,000.00 to a specific loan:
   - `DEBIT: 230100 - Investor Available Cash Balance Liability` (-\$1,000.00)
   - `CREDIT: 230200 - Investor Committed Loan Principal Liability` (+\$1,000.00)

### 3.2 Borrower Loan Disbursement Flow
1. The loan attains status `FUNDED` when committed capital reaches 100% of the loan principal ($\sum \text{Investments} \equiv \text{Principal}$).
2. The platform generates an internal payout instruction from the FBO Escrow bank account to the borrower's validated bank account.
3. The `PayoutOrderService` executes a domestic instant payout (FedNow / BI-FAST).
4. Upon settlement confirmation (`pacs.002`), the ledger posts:
   - `DEBIT: 230200 - Investor Committed Loan Principal Liability` (-\$50,000.00)
   - `CREDIT: 110400 - Custodian Bank Escrow Cash Asset` (-\$50,000.00)
   - `DEBIT: 130000 - Loan Receivable Asset (Borrower ID)` (+\$50,000.00)
   - `CREDIT: 230500 - Investor Active Note Liability` (+\$50,000.00)

### 3.3 Borrower Repayment Collection Flow
1. Installment schedules are generated at disbursement ($N$ monthly installments).
2. Two days prior to due date, the platform issues an automated Direct Debit pull or activates the borrower's dynamic Virtual Account.
3. When repayment lands in the custodian bank escrow:
   - `DEBIT: 110400 - Custodian Bank Escrow Cash Asset` (+\$4,500.00)
   - `CREDIT: 230300 - Borrower In-Transit Repayment Liability` (+\$4,500.00)
4. State transitions to `COLLECTED`, immediately triggering the **Waterfall Split Engine**.

---

## 4. Mathematical Waterfall Split Engine

When an installment repayment $R$ is collected, the funds must be mathematically divided across stakeholders according to strict regulatory priority:

```
[ Collected Repayment R ]
           │
           ├─► 1. Late Penalty Fees (If delinquent) ──────► Platform Fee Revenue / Loss Reserve
           │
           ├─► 2. Platform Servicing / Origination Fee ───► Platform Revenue
           │
           ├─► 3. Statutory Withholding Tax (WHT) ────────► Tax Escrow (Govt Remittance)
           │
           ├─► 4. Principal Amortization ─────────────────► Fractional Investors (Pro-Rata)
           │
           └─► 5. Net Interest Due ───────────────────────► Fractional Investors (Pro-Rata)
```

### 4.1 Formal Mathematical Formulation
Let:
- $R$: Total gross collected repayment amount (in integer minor units).
- $k$: Current installment index ($1 \le k \le N$).
- $\text{PenaltyDue}_k$: Accrued late penalties for installment $k$.
- $\text{PlatformFee}_k$: Agreed servicing fee deducted by platform.
- $\text{GrossInterestDue}_k$: Total contractual interest for installment $k$.
- $\text{PrincipalDue}_k$: Contractual principal amortization for installment $k$.
- $\tau_{\text{wht}}$: Statutory Withholding Tax rate on interest income (e.g. 15% or 0.1500).
- $M$: Total number of participating investors.
- $\alpha_i$: Ownership share of investor $i$, defined as:
  $$\alpha_i = \frac{P_i}{\sum_{j=1}^{M} P_j} \quad \text{where } \sum_{i=1}^{M} \alpha_i \equiv 1.0$$

#### Sequential Allocation Steps:

1. **Late Penalty Deductions**:
   $$R_{\text{penalty}} = \min(R, \text{PenaltyDue}_k)$$
   $$R_1 = R - R_{\text{penalty}}$$

2. **Platform Servicing Fee Allocation**:
   $$R_{\text{fee}} = \min(R_1, \text{PlatformFee}_k)$$
   $$R_2 = R_1 - R_{\text{fee}}$$

3. **Interest & Principal Deductions**:
   $$R_{\text{gross\_interest}} = \min(R_2, \text{GrossInterestDue}_k)$$
   $$R_{\text{principal}} = \min(R_2 - R_{\text{gross\_interest}}, \text{PrincipalDue}_k)$$

4. **Tax Withholding & Net Interest Allocation**:
   $$\text{Total WHT} = \lfloor R_{\text{gross\_interest}} \times \tau_{\text{wht}} \rfloor$$
   $$R_{\text{net\_interest}} = R_{\text{gross\_interest}} - \text{Total WHT}$$

5. **Fractional Investor Distribution (Pro-Rata)**:
   For each investor $i \in \{1, \dots, M\}$:
   $$\text{Principal}_i = \lfloor \alpha_i \times R_{\text{principal}} \rfloor$$
   $$\text{GrossInterest}_i = \lfloor \alpha_i \times R_{\text{gross\_interest}} \rfloor$$
   $$\text{WHT}_i = \lfloor \text{GrossInterest}_i \times \tau_{\text{wht}} \rfloor$$
   $$\text{NetInterest}_i = \text{GrossInterest}_i - \text{WHT}_i$$
   $$\text{Total Payout}_i = \text{Principal}_i + \text{NetInterest}_i$$

*Note: Integer minor-unit rounding remainders ($\Delta = R_{\text{principal}} - \sum \text{Principal}_i$) are deterministically credited to the largest investor position or platform reserve account.*

---

## 5. Delinquency Handling & Partial Repayment Rules

If a borrower submits a partial payment where $R < \text{TotalDue}_k$:
1. The engine strictly preserves the waterfall sequence: penalties and platform fees are satisfied first, followed by interest, and finally principal.
2. The remaining unpaid balance stays recorded in `installment_plans` as `amount_paid < total_due` with status `PARTIALLY_PAID`.
3. If payment is not cured within the contractual grace period (e.g. 7 days):
   - Loan transitions to `DELINQUENT`.
   - Automated daily penalty accrual begins:
     $$\text{DailyPenalty} = \text{PrincipalOutstanding} \times \frac{\text{PenaltyRateAnnual}}{365}$$
4. If delinquency reaches 90 days past due (DPD $\ge 90$):
   - Loan status transitions to `IN_DEFAULT`.
   - The platform freezes further automated debits and routes the note to the third-party debt recovery subsystem.

---

## 6. P2P Lending State Machines

```
[ LOAN ORIGINATION & DISBURSEMENT LIFECYCLE ]
  +-------------+     +----------+     +----------------------+     +-------------+     +----------+
  | APPLICATION +---->| FUNDING  +---->| FUNDED               +---->| DISBURSED   +---->| ACTIVE   |
  +-------------+     +----+-----+     +----------------------+     +-------------+     +----+-----+
                           |                                                                 |
                           v (Funding Expired)                                               v (Default)
                      +----+-----+                                                      +----+-----+
                      | CANCELLED|                                                      |IN_DEFAULT|
                      +----------+                                                      +----------+

[ INSTALLMENT REPAYMENT LIFECYCLE ]
  +-------------+     +----------+     +----------------------+     +-------------+     +----------+
  |  SCHEDULED  +---->|   DUE    +---->| AUTODEBIT_INITIATED  +---->|  COLLECTED  +---->|   PAID   |
  +-------------+     +----+-----+     +----------------------+     +------+------+     +----------+
                           |                                               | (Partial)
                           v (Grace Period Expired)                        v
                      +----+-----+                                  +------+------+
                      |DELINQUENT|                                  |PARTIALLY_PD |
                      +----------+                                  +-------------+
```
