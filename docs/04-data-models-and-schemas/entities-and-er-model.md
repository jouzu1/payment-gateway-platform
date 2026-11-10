# Entity Descriptions, Relationships & Entity-Relationship (ER) Model

## 1. Data Model Architectural Invariants

The data architecture of the Payment Gateway Platform is designed for strict relational integrity, horizontal scalability, audit compliance, and multi-tenant isolation.

Key design principles:
- **UUIDv7 Primary Keys**: All entity IDs are time-ordered UUIDs (`uuid_generate_v4()` or UUIDv7), preventing B-Tree index fragmentation while enabling distributed client-side ID generation.
- **Strict Foreign Key Constraints with `ON DELETE RESTRICT`**: Financial records cannot be orphaned or cascaded into deletion.
- **Tenant Isolation**: Every query and index is scoped by `tenant_id` and `merchant_id` to enforce strict logical data segregation across tenants.
- **Temporal Versioning & Auditability**: Every mutation updates `updated_at` via timestamp triggers, and financial tables (`journal_entries`, `entry_legs`) are strictly append-only.

---

## 2. Mermaid Entity-Relationship (ER) Diagram

```mermaid
erDiagram
    MERCHANT ||--o{ CUSTOMER : "manages"
    MERCHANT ||--o{ PAYMENT_INTENT : "initiates"
    MERCHANT ||--o{ PAYOUT_ORDER : "dispatches"
    MERCHANT ||--o{ LEDGER_ACCOUNT : "owns"
    MERCHANT ||--o{ SETTLEMENT_BATCH : "settles"
    MERCHANT ||--o{ LOAN_ACCOUNT : "originates"

    CUSTOMER ||--o{ PAYMENT_METHOD : "vaults"
    CUSTOMER ||--o{ PAYMENT_INTENT : "pays"
    CUSTOMER ||--o{ LOAN_ACCOUNT : "borrows"

    PAYMENT_INTENT ||--o{ TRANSACTION : "executes"
    PAYMENT_METHOD ||--o{ TRANSACTION : "authorizes"
    TRANSACTION ||--o{ REFUND : "reverses"
    TRANSACTION ||--o| WATERFALL_SPLIT : "replaces"

    FX_QUOTE ||--o{ PAYOUT_ORDER : "locks_rate"
    AML_SCREENING ||--o{ PAYOUT_ORDER : "clears"

    LOAN_ACCOUNT ||--o{ INSTALLMENT_PLAN : "schedules"
    INSTALLMENT_PLAN ||--o{ WATERFALL_SPLIT : "distributes"

    LEDGER_ACCOUNT ||--o{ ENTRY_LEG : "posts"
    LEDGER_ACCOUNT ||--|| ACCOUNT_BALANCE : "projects"
    JOURNAL_ENTRY ||--|{ ENTRY_LEG : "contains"

    MERCHANT {
        uuid id PK
        string tenant_id
        string legal_name
        string webhook_url
        string webhook_secret
        string status
        string settlement_schedule
    }

    CUSTOMER {
        uuid id PK
        uuid merchant_id FK
        string external_customer_id
        string email
        string full_name
        string country
    }

    PAYMENT_METHOD {
        uuid id PK
        uuid customer_id FK
        uuid merchant_id FK
        string method_type
        string token_vault_id
        string fingerprint
        string last4
    }

    PAYMENT_INTENT {
        uuid id PK
        uuid merchant_id FK
        uuid customer_id FK
        int64 amount
        string currency
        string status
        string client_secret
        string idempotency_key
    }

    TRANSACTION {
        uuid id PK
        uuid payment_intent_id FK
        uuid merchant_id FK
        uuid payment_method_id FK
        string rail_code
        string rail_transaction_ref
        int64 gross_amount
        int64 fee_amount
        int64 net_amount
        string status
    }

    PAYOUT_ORDER {
        uuid id PK
        uuid merchant_id FK
        string payout_type
        int64 source_amount
        string source_currency
        int64 target_amount
        string target_currency
        uuid fx_quote_id FK
        uuid aml_screening_id FK
        string status
        string end_to_end_id
    }

    FX_QUOTE {
        uuid id PK
        uuid merchant_id FK
        string base_currency
        string target_currency
        numeric exchange_rate
        timestamp expires_at
        boolean is_executed
    }

    AML_SCREENING {
        uuid id PK
        string entity_id
        string full_name
        numeric match_score
        string match_status
    }

    LOAN_ACCOUNT {
        uuid id PK
        uuid borrower_id FK
        uuid platform_merchant_id FK
        string loan_reference
        int64 principal_amount
        numeric annual_interest_rate
        int tenor_months
        string status
        uuid escrow_account_id
    }

    INSTALLMENT_PLAN {
        uuid id PK
        uuid loan_id FK
        int installment_sequence
        date due_date
        int64 principal_due
        int64 interest_due
        int64 penalty_due
        int64 total_due
        string status
    }

    WATERFALL_SPLIT {
        uuid id PK
        uuid installment_id FK
        uuid repayment_transaction_id FK
        uuid investor_id
        int64 gross_collected
        int64 principal_allocated
        int64 interest_allocated
        int64 platform_servicing_fee
        int64 withholding_tax
        int64 net_investor_payout
    }

    LEDGER_ACCOUNT {
        uuid id PK
        string account_number
        uuid merchant_id FK
        string category
        string currency
        int precision_scale
        boolean allow_negative_balance
    }

    JOURNAL_ENTRY {
        uuid id PK
        string idempotency_key
        string correlation_id
        string transaction_type
        timestamp posted_at
    }

    ENTRY_LEG {
        uuid id PK
        uuid journal_entry_id FK
        uuid account_id FK
        string direction
        int64 amount
        string currency
        int sequence_order
    }

    ACCOUNT_BALANCE {
        uuid account_id PK, FK
        int64 cleared_balance
        int64 pending_balance
        int64 version
        timestamp updated_at
    }
```

---

## 3. Detailed Entity Catalog & Field Specifications

### 3.1 Merchant Context Entities
- **`Merchant`**: Root multi-tenant entity representing business clients. Defines settlement schedules ($T+0, T+1, T+2$), risk tiers, and webhook cryptographic keys.
- **`Customer`**: End-consumer entity associated with a merchant. Stores vaulted payment methods, KYC tier status, and billing identities.
- **`PaymentMethod`**: Tokenized representation of payment instruments (credit/debit cards, bank accounts, virtual accounts). Raw card data is represented solely by a reference pointer (`token_vault_id`) into the isolated PCI vault.

### 3.2 Acquiring Context Entities
- **`PaymentIntent`**: High-level intent to collect payment. Encapsulates amount, allowed rails, capture mode (Automatic vs Manual), and lifecycle status.
- **`Transaction`**: Physical execution attempt against an external payment rail adapter (e.g. CyberSource, FedNow). Links to raw network response and settlement batch.
- **`Refund`**: Reversal record tied to a settled transaction. Encapsulates partial or full refund amount and rail refund reference.

### 3.3 Remittance & FX Entities
- **`PayoutOrder`**: Instruction to remit funds locally or cross-border. Encapsulates beneficiary account details, ISO 20022 `EndToEndId`, and status.
- **`FXQuote`**: Binding foreign exchange rate agreement locked with a 60-second TTL.
- **`AMLScreening`**: Audit record of fuzzy sanctions and PEP screening executed against international watchlists.

### 3.4 P2P Lending Entities
- **`LoanAccount`**: Lending contract connecting borrower, funding platform, and custodian bank FBO escrow account.
- **`InstallmentPlan`**: Amortization schedule defining monthly principal, interest, overdue penalties, and collection states.
- **`WaterfallSplit`**: Mathematical distribution record documenting the pro-rata allocation of collected repayments to fractional investors, platform fees, and statutory withholding taxes.

### 3.5 Core Ledger Entities
- **`LedgerAccount`**: Standardized Chart of Accounts record (Asset, Liability, Equity, Revenue, Expense) denominated in a specific ISO 4217 currency.
- **`JournalEntry`**: Header record for an atomic double-entry transaction.
- **`EntryLeg`**: Individual debit or credit posting leg linked to a ledger account, stored strictly in positive integer minor currency units.
- **`AccountBalance`**: Atomically updated balance projection storing current cleared and pending balances with monotonic version sequencing.
