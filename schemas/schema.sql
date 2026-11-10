-- =============================================================================
-- Enterprise Payment Gateway Platform - Production PostgreSQL DDL Schema
-- Architecture: Tier-1 Real-Time Processing & Immutable Double-Entry Ledger
-- Standard: PostgreSQL 14+ / ACID Compliant / Minor Currency Units (BIGINT)
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =============================================================================
-- 1. ENUMERATION TYPES
-- =============================================================================

CREATE TYPE account_category AS ENUM (
    'ASSET',
    'LIABILITY',
    'EQUITY',
    'REVENUE',
    'EXPENSE'
);

CREATE TYPE entry_direction AS ENUM (
    'DEBIT',
    'CREDIT'
);

CREATE TYPE payment_intent_status AS ENUM (
    'REQUIRES_PAYMENT_METHOD',
    'REQUIRES_CONFIRMATION',
    'REQUIRES_ACTION',
    'PROCESSING',
    'SUCCEEDED',
    'CANCELLED'
);

CREATE TYPE transaction_status AS ENUM (
    'INITIALIZED',
    'PENDING',
    'AUTHORIZED',
    'CAPTURED',
    'SETTLED',
    'FAILED',
    'REVERSED'
);

CREATE TYPE transaction_type AS ENUM (
    'PAYIN',
    'PAYOUT',
    'REFUND',
    'DISPUTE_REVERSAL',
    'INTERNAL_TRANSFER',
    'FEE'
);

CREATE TYPE payout_status AS ENUM (
    'DRAFT',
    'BENEFICIARY_VALIDATED',
    'SUBMITTED',
    'PENDING_SCREENING',
    'SCREENING_APPROVED',
    'BLOCKED_SANCTIONS',
    'ROUTED',
    'DISPATCHED_TO_RAIL',
    'QUEUED_IN_BATCH',
    'SETTLED',
    'FAILED',
    'CANCELLED',
    'REVERSED'
);

CREATE TYPE payout_type AS ENUM (
    'LOCAL_INSTANT',
    'LOCAL_BATCH',
    'CROSS_BORDER'
);

CREATE TYPE loan_status AS ENUM (
    'APPLICATION',
    'FUNDING',
    'FUNDED',
    'PENDING_DISBURSEMENT',
    'DISBURSED',
    'ACTIVE',
    'DELINQUENT',
    'IN_DEFAULT',
    'CLOSED'
);

CREATE TYPE installment_status AS ENUM (
    'SCHEDULED',
    'DUE',
    'AUTODEBIT_INITIATED',
    'COLLECTED',
    'PAID',
    'PARTIALLY_PAID',
    'DELINQUENT',
    'IN_DEFAULT'
);

CREATE TYPE idempotency_state AS ENUM (
    'STARTED',
    'IN_FLIGHT',
    'COMMITTED',
    'FAILED'
);

CREATE TYPE webhook_delivery_status AS ENUM (
    'PENDING',
    'DELIVERED',
    'FAILED',
    'DEAD_LETTER'
);

-- =============================================================================
-- 2. CORE IDENTITY & MERCHANT TABLES
-- =============================================================================

CREATE TABLE merchants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL,
    legal_name VARCHAR(255) NOT NULL,
    dba_name VARCHAR(255),
    kyc_status VARCHAR(32) NOT NULL DEFAULT 'APPROVED',
    webhook_url TEXT,
    webhook_secret VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    settlement_schedule VARCHAR(32) NOT NULL DEFAULT 'T_PLUS_1', -- T_PLUS_0, T_PLUS_1, T_PLUS_2
    fee_profile_id VARCHAR(64) NOT NULL DEFAULT 'STANDARD',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX idx_merchants_tenant ON merchants(tenant_id);
CREATE INDEX idx_merchants_status ON merchants(status);

CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    external_customer_id VARCHAR(128) NOT NULL,
    email VARCHAR(255) NOT NULL,
    phone VARCHAR(32),
    full_name VARCHAR(255) NOT NULL,
    kyc_level VARCHAR(32) NOT NULL DEFAULT 'STANDARD',
    country VARCHAR(2) NOT NULL, -- ISO 3166-1 alpha-2
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT uq_merchant_customer UNIQUE (merchant_id, external_customer_id)
);

CREATE INDEX idx_customers_merchant ON customers(merchant_id);
CREATE INDEX idx_customers_email ON customers(email);

CREATE TABLE payment_methods (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,
    merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    method_type VARCHAR(32) NOT NULL, -- 'CARD', 'VIRTUAL_ACCOUNT', 'E_WALLET', 'DIRECT_DEBIT', 'QR_CODE'
    provider VARCHAR(64) NOT NULL,    -- 'VISA_MC', 'BCA_VA', 'OVO', 'SEPA_DD', 'QRIS'
    token_vault_id VARCHAR(128) NOT NULL UNIQUE,
    fingerprint VARCHAR(128) NOT NULL,
    last4 VARCHAR(4),
    exp_month SMALLINT CHECK (exp_month BETWEEN 1 AND 12),
    exp_year SMALLINT CHECK (exp_year >= 2024),
    card_brand VARCHAR(32),
    mandate_reference VARCHAR(128),
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX idx_payment_methods_customer ON payment_methods(customer_id);
CREATE INDEX idx_payment_methods_fingerprint ON payment_methods(fingerprint);

-- =============================================================================
-- 3. PAYIN & CHECKOUT ENGINE
-- =============================================================================

CREATE TABLE payment_intents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL,
    merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,
    amount BIGINT NOT NULL CHECK (amount > 0), -- Minor currency units (e.g., cents)
    currency VARCHAR(3) NOT NULL,              -- ISO 4217 code
    capture_method VARCHAR(16) NOT NULL DEFAULT 'AUTOMATIC', -- 'AUTOMATIC' | 'MANUAL'
    allowed_payment_methods JSONB NOT NULL,    -- e.g. ["CARD", "VA_BCA", "QRIS", "E_WALLET"]
    status payment_intent_status NOT NULL DEFAULT 'REQUIRES_PAYMENT_METHOD',
    client_secret VARCHAR(128) NOT NULL UNIQUE,
    idempotency_key VARCHAR(128) NOT NULL,
    description TEXT,
    statement_descriptor VARCHAR(22),
    metadata JSONB DEFAULT '{}',
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT uq_payment_intent_idempotency UNIQUE (merchant_id, idempotency_key)
);

CREATE INDEX idx_payment_intents_merchant_created ON payment_intents(merchant_id, created_at DESC);
CREATE INDEX idx_payment_intents_status ON payment_intents(status);

CREATE TABLE settlement_batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    currency VARCHAR(3) NOT NULL,
    total_gross_amount BIGINT NOT NULL DEFAULT 0,
    total_fee_amount BIGINT NOT NULL DEFAULT 0,
    total_net_amount BIGINT NOT NULL DEFAULT 0,
    transaction_count INT NOT NULL DEFAULT 0,
    cut_off_time TIMESTAMPTZ NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'OPEN', -- 'OPEN', 'PROCESSING', 'SETTLED', 'CLOSED'
    payout_order_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    closed_at TIMESTAMPTZ
);

CREATE INDEX idx_settlement_batches_merchant ON settlement_batches(merchant_id, status);

CREATE TABLE transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payment_intent_id UUID NOT NULL REFERENCES payment_intents(id) ON DELETE RESTRICT,
    merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    payment_method_id UUID REFERENCES payment_methods(id) ON DELETE RESTRICT,
    rail_code VARCHAR(32) NOT NULL,         -- 'CYBERSOURCE_CARDS', 'FEDNOW_DIRECT', 'BI_FAST', 'QRIS_ARTAJASA'
    rail_transaction_ref VARCHAR(128),
    transaction_type transaction_type NOT NULL DEFAULT 'PAYIN',
    gross_amount BIGINT NOT NULL CHECK (gross_amount > 0),
    fee_amount BIGINT NOT NULL DEFAULT 0,
    net_amount BIGINT NOT NULL,
    currency VARCHAR(3) NOT NULL,
    status transaction_status NOT NULL DEFAULT 'INITIALIZED',
    error_code VARCHAR(64),
    error_message TEXT,
    auth_code VARCHAR(32),
    three_ds_trans_status VARCHAR(8),      -- 'Y', 'A', 'C', 'N', 'U', 'R'
    settlement_batch_id UUID REFERENCES settlement_batches(id) ON DELETE SET NULL,
    raw_response JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX idx_transactions_intent ON transactions(payment_intent_id);
CREATE INDEX idx_transactions_rail_ref ON transactions(rail_code, rail_transaction_ref);
CREATE INDEX idx_transactions_status ON transactions(status);
CREATE INDEX idx_transactions_batch ON transactions(settlement_batch_id);

CREATE TABLE refunds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id UUID NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT,
    merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    amount BIGINT NOT NULL CHECK (amount > 0),
    currency VARCHAR(3) NOT NULL,
    reason VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING', -- 'PENDING', 'SUCCEEDED', 'FAILED'
    rail_refund_ref VARCHAR(128),
    idempotency_key VARCHAR(128) NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT uq_refund_idempotency UNIQUE (merchant_id, idempotency_key)
);

CREATE INDEX idx_refunds_tx ON refunds(transaction_id);

-- =============================================================================
-- 4. TREASURY, FX & AML SANCTIONS
-- =============================================================================

CREATE TABLE fx_quotes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    base_currency VARCHAR(3) NOT NULL,
    target_currency VARCHAR(3) NOT NULL,
    base_amount BIGINT NOT NULL CHECK (base_amount > 0),
    target_amount BIGINT NOT NULL CHECK (target_amount > 0),
    exchange_rate NUMERIC(18, 8) NOT NULL CHECK (exchange_rate > 0),
    spread_bps INT NOT NULL DEFAULT 50, -- Basis points
    provider_id VARCHAR(64) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    is_executed BOOLEAN NOT NULL DEFAULT FALSE,
    executed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX idx_fx_quotes_merchant_exp ON fx_quotes(merchant_id, expires_at);

CREATE TABLE aml_screenings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type VARCHAR(32) NOT NULL, -- 'SENDER', 'BENEFICIARY', 'MERCHANT', 'BORROWER'
    entity_id VARCHAR(128) NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    date_of_birth DATE,
    country VARCHAR(2) NOT NULL,
    match_score NUMERIC(5, 4) NOT NULL DEFAULT 0.0000,
    match_status VARCHAR(32) NOT NULL DEFAULT 'CLEAN', -- 'CLEAN', 'POTENTIAL_MATCH', 'CONFIRMED_SANCTION', 'FALSE_POSITIVE'
    matched_lists JSONB DEFAULT '[]',                  -- OFAC_SDN, UN_CONSOLIDATED, PEP, EU_TERROR
    reviewed_by VARCHAR(64),
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX idx_aml_screening_name ON aml_screenings(full_name);
CREATE INDEX idx_aml_screening_status ON aml_screenings(match_status);

-- =============================================================================
-- 5. LOCAL & CROSS-BORDER DISBURSEMENT / PAYOUTS
-- =============================================================================

CREATE TABLE payout_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    payout_type payout_type NOT NULL,
    corridor_id VARCHAR(16),                           -- e.g. 'USD-IDR', 'DOMESTIC_USD'
    source_currency VARCHAR(3) NOT NULL,
    source_amount BIGINT NOT NULL CHECK (source_amount > 0),
    target_currency VARCHAR(3) NOT NULL,
    target_amount BIGINT NOT NULL CHECK (target_amount > 0),
    fx_quote_id UUID REFERENCES fx_quotes(id) ON DELETE RESTRICT,
    locked_exchange_rate NUMERIC(18, 8),
    beneficiary_account_name VARCHAR(255) NOT NULL,
    beneficiary_account_number VARCHAR(64) NOT NULL,
    beneficiary_bank_code VARCHAR(32) NOT NULL,
    beneficiary_country VARCHAR(2) NOT NULL,
    purpose_code VARCHAR(16),                          -- ISO 20022 Purpose Code (e.g. 'SALA', 'TREA')
    aml_screening_id UUID REFERENCES aml_screenings(id) ON DELETE RESTRICT,
    status payout_status NOT NULL DEFAULT 'SUBMITTED',
    rail_code VARCHAR(32),
    rail_payout_ref VARCHAR(128),
    end_to_end_id VARCHAR(64) NOT NULL UNIQUE,          -- ISO 20022 pacs.008 EndToEndId
    idempotency_key VARCHAR(128) NOT NULL,
    error_code VARCHAR(64),
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT uq_payout_idempotency UNIQUE (merchant_id, idempotency_key)
);

CREATE INDEX idx_payout_orders_merchant_created ON payout_orders(merchant_id, created_at DESC);
CREATE INDEX idx_payout_orders_status ON payout_orders(status);
CREATE INDEX idx_payout_orders_end_to_end ON payout_orders(end_to_end_id);

-- =============================================================================
-- 6. P2P LENDING & REGULATORY ESCROW WATERFALL
-- =============================================================================

CREATE TABLE loan_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    borrower_id UUID NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    platform_merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    loan_reference VARCHAR(64) NOT NULL UNIQUE,
    principal_amount BIGINT NOT NULL CHECK (principal_amount > 0),
    annual_interest_rate NUMERIC(5, 2) NOT NULL CHECK (annual_interest_rate > 0),
    tenor_months INT NOT NULL CHECK (tenor_months > 0),
    status loan_status NOT NULL DEFAULT 'APPLICATION',
    escrow_account_id UUID NOT NULL,                   -- Ledger Account ID for FBO Escrow
    disbursement_payout_id UUID REFERENCES payout_orders(id) ON DELETE SET NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    disbursed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX idx_loans_borrower ON loan_accounts(borrower_id);
CREATE INDEX idx_loans_platform ON loan_accounts(platform_merchant_id);
CREATE INDEX idx_loans_status ON loan_accounts(status);

CREATE TABLE installment_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    loan_id UUID NOT NULL REFERENCES loan_accounts(id) ON DELETE RESTRICT,
    installment_sequence INT NOT NULL CHECK (installment_sequence > 0),
    due_date DATE NOT NULL,
    principal_due BIGINT NOT NULL CHECK (principal_due >= 0),
    interest_due BIGINT NOT NULL CHECK (interest_due >= 0),
    penalty_due BIGINT NOT NULL DEFAULT 0 CHECK (penalty_due >= 0),
    total_due BIGINT NOT NULL CHECK (total_due > 0),
    amount_paid BIGINT NOT NULL DEFAULT 0 CHECK (amount_paid >= 0),
    status installment_status NOT NULL DEFAULT 'SCHEDULED',
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT uq_loan_installment UNIQUE (loan_id, installment_sequence)
);

CREATE INDEX idx_installment_loan_due ON installment_plans(loan_id, due_date);

CREATE TABLE waterfall_splits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    installment_id UUID NOT NULL REFERENCES installment_plans(id) ON DELETE RESTRICT,
    repayment_transaction_id UUID NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT,
    investor_id UUID NOT NULL,
    gross_collected BIGINT NOT NULL CHECK (gross_collected > 0),
    principal_allocated BIGINT NOT NULL DEFAULT 0 CHECK (principal_allocated >= 0),
    interest_allocated BIGINT NOT NULL DEFAULT 0 CHECK (interest_allocated >= 0),
    platform_servicing_fee BIGINT NOT NULL DEFAULT 0 CHECK (platform_servicing_fee >= 0),
    withholding_tax BIGINT NOT NULL DEFAULT 0 CHECK (withholding_tax >= 0),
    net_investor_payout BIGINT NOT NULL CHECK (net_investor_payout >= 0),
    status VARCHAR(32) NOT NULL DEFAULT 'COMMITTED',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT chk_waterfall_math CHECK (
        gross_collected = principal_allocated + interest_allocated + platform_servicing_fee + withholding_tax
    )
);

CREATE INDEX idx_waterfall_installment ON waterfall_splits(installment_id);
CREATE INDEX idx_waterfall_investor ON waterfall_splits(investor_id);

-- =============================================================================
-- 7. IMMUTABLE DOUBLE-ENTRY LEDGER
-- =============================================================================

CREATE TABLE ledger_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_number VARCHAR(64) UNIQUE NOT NULL,
    merchant_id UUID REFERENCES merchants(id) ON DELETE RESTRICT,
    tenant_id VARCHAR(64) NOT NULL,
    category account_category NOT NULL,
    currency VARCHAR(3) NOT NULL,                      -- ISO 4217
    precision_scale SMALLINT NOT NULL DEFAULT 2,       -- Decimal scale
    allow_negative_balance BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX idx_ledger_accounts_number ON ledger_accounts(account_number);
CREATE INDEX idx_ledger_accounts_merchant ON ledger_accounts(merchant_id, currency);

CREATE TABLE journal_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key VARCHAR(128) UNIQUE NOT NULL,
    correlation_id VARCHAR(128) NOT NULL,
    transaction_type VARCHAR(64) NOT NULL,             -- 'PAYIN', 'PAYOUT', 'P2P_WATERFALL', 'FEE'
    description TEXT,
    previous_entry_hash VARCHAR(64),                   -- SHA-256 hash of previous journal entry in audit chain
    entry_hash VARCHAR(64),                            -- SHA-256 tamper-evident hash of current entry
    posted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX idx_journal_entries_corr ON journal_entries(correlation_id);
CREATE INDEX idx_journal_entries_posted ON journal_entries(posted_at DESC);
CREATE INDEX idx_journal_entries_entry_hash ON journal_entries(entry_hash);

CREATE TABLE entry_legs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    journal_entry_id UUID NOT NULL REFERENCES journal_entries(id) ON DELETE RESTRICT,
    account_id UUID NOT NULL REFERENCES ledger_accounts(id) ON DELETE RESTRICT,
    direction entry_direction NOT NULL,
    amount BIGINT NOT NULL CHECK (amount > 0),          -- Minor units, strictly positive
    currency VARCHAR(3) NOT NULL,
    sequence_order SMALLINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT uq_entry_leg_seq UNIQUE (journal_entry_id, sequence_order)
);

CREATE INDEX idx_entry_legs_account ON entry_legs(account_id, created_at DESC);
CREATE INDEX idx_entry_legs_journal ON entry_legs(journal_entry_id);

CREATE TABLE account_balances (
    account_id UUID PRIMARY KEY REFERENCES ledger_accounts(id) ON DELETE RESTRICT,
    cleared_balance BIGINT NOT NULL DEFAULT 0,
    pending_balance BIGINT NOT NULL DEFAULT 0,
    version BIGINT NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

-- =============================================================================
-- 8. DISTRIBUTED IDEMPOTENCY, OUTBOX & WEBHOOK DISPATCH
-- =============================================================================

CREATE TABLE idempotency_records (
    key VARCHAR(128) PRIMARY KEY,
    merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    scope VARCHAR(64) NOT NULL,                        -- e.g. 'PAYMENT_INTENT', 'PAYOUT_ORDER'
    state idempotency_state NOT NULL DEFAULT 'STARTED',
    request_hash VARCHAR(64) NOT NULL,                 -- SHA-256 of request payload
    response_status_code INT,
    response_headers JSONB,
    response_body JSONB,
    locked_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX idx_idempotency_expires ON idempotency_records(expires_at);

CREATE TABLE outbox_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aggregate_type VARCHAR(64) NOT NULL,               -- 'PAYMENT_INTENT', 'PAYOUT_ORDER', 'JOURNAL_ENTRY'
    aggregate_id VARCHAR(128) NOT NULL,
    event_type VARCHAR(64) NOT NULL,                   -- 'PaymentCaptured', 'PayoutDispatched', 'HoldCommitted'
    payload JSONB NOT NULL,
    partition_key VARCHAR(128) NOT NULL,
    published_at TIMESTAMPTZ,
    retry_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX idx_outbox_unpublished ON outbox_events(created_at) WHERE published_at IS NULL;

CREATE TABLE webhook_deliveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    event_id UUID NOT NULL REFERENCES outbox_events(id) ON DELETE CASCADE,
    endpoint_url TEXT NOT NULL,
    request_payload JSONB NOT NULL,
    signature VARCHAR(128) NOT NULL,
    attempt_number INT NOT NULL DEFAULT 1,
    response_status INT,
    response_body TEXT,
    next_retry_at TIMESTAMPTZ,
    status webhook_delivery_status NOT NULL DEFAULT 'PENDING',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX idx_webhook_retry ON webhook_deliveries(next_retry_at) WHERE status = 'PENDING';

-- =============================================================================
-- 9. AUDIT TRIGGERS & FINANCIAL INTEGRITY FUNCTIONS
-- =============================================================================

-- Immutability Protection for Ledger Journal Entries & Legs
CREATE OR REPLACE FUNCTION fn_prevent_ledger_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Financial Ledger Violation: UPDATE or DELETE is strictly prohibited on table %', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_immutable_journal_entries
BEFORE UPDATE OR DELETE ON journal_entries
FOR EACH ROW EXECUTE FUNCTION fn_prevent_ledger_mutation();

CREATE TRIGGER trg_immutable_entry_legs
BEFORE UPDATE OR DELETE ON entry_legs
FOR EACH ROW EXECUTE FUNCTION fn_prevent_ledger_mutation();

-- Automated Balance Projection Updates upon Entry Leg Posting
CREATE OR REPLACE FUNCTION fn_update_account_balance()
RETURNS TRIGGER AS $$
DECLARE
    v_category account_category;
    v_allow_negative BOOLEAN;
    v_delta BIGINT;
    v_current_cleared BIGINT;
BEGIN
    SELECT category, allow_negative_balance
    INTO v_category, v_allow_negative
    FROM ledger_accounts
    WHERE id = NEW.account_id;

    -- Asset & Expense: Debit is positive (+), Credit is negative (-)
    -- Liability, Equity & Revenue: Credit is positive (+), Debit is negative (-)
    IF v_category IN ('ASSET', 'EXPENSE') THEN
        IF NEW.direction = 'DEBIT' THEN
            v_delta := NEW.amount;
        ELSE
            v_delta := -NEW.amount;
        END IF;
    ELSE
        IF NEW.direction = 'CREDIT' THEN
            v_delta := NEW.amount;
        ELSE
            v_delta := -NEW.amount;
        END IF;
    END IF;

    -- Upsert Account Balance
    INSERT INTO account_balances (account_id, cleared_balance, pending_balance, version, updated_at)
    VALUES (NEW.account_id, v_delta, 0, 1, clock_timestamp())
    ON CONFLICT (account_id) DO UPDATE
    SET cleared_balance = account_balances.cleared_balance + v_delta,
        version = account_balances.version + 1,
        updated_at = clock_timestamp()
    RETURNING cleared_balance INTO v_current_cleared;

    -- Enforce Overdraft / Negative Balance Prevention
    IF NOT v_allow_negative AND v_current_cleared < 0 THEN
        RAISE EXCEPTION 'Ledger Balance Violation: Account % cannot have negative cleared balance (attempted balance: %)',
            NEW.account_id, v_current_cleared;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_update_balance_after_leg
AFTER INSERT ON entry_legs
FOR EACH ROW EXECUTE FUNCTION fn_update_account_balance();

-- Tamper-Evident SHA-256 Hash Chaining Function
CREATE OR REPLACE FUNCTION fn_compute_journal_entry_hash()
RETURNS TRIGGER AS $$
DECLARE
    v_prev_hash VARCHAR(64);
    v_payload TEXT;
BEGIN
    IF NEW.previous_entry_hash IS NULL THEN
        SELECT entry_hash INTO v_prev_hash
        FROM journal_entries
        WHERE id <> NEW.id
        ORDER BY posted_at DESC, id DESC
        LIMIT 1;

        NEW.previous_entry_hash := COALESCE(v_prev_hash, '0000000000000000000000000000000000000000000000000000000000000000');
    END IF;

    v_payload := NEW.previous_entry_hash || ':' || NEW.id::TEXT || ':' || NEW.idempotency_key || ':' || NEW.transaction_type;
    NEW.entry_hash := encode(digest(v_payload, 'sha256'), 'hex');

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_compute_journal_entry_hash
BEFORE INSERT ON journal_entries
FOR EACH ROW
EXECUTE FUNCTION fn_compute_journal_entry_hash();

-- Standalone Verification Function: Ensure Multi-Currency Zero-Sum Balance
CREATE OR REPLACE FUNCTION fn_verify_journal_entry_balance(p_journal_entry_id UUID)
RETURNS BOOLEAN AS $$
DECLARE
    v_unbalanced_currency VARCHAR(3);
    v_debit_sum BIGINT;
    v_credit_sum BIGINT;
    v_leg_count INT;
BEGIN
    SELECT COUNT(*) INTO v_leg_count
    FROM entry_legs
    WHERE journal_entry_id = p_journal_entry_id;

    IF v_leg_count < 2 THEN
        RAISE EXCEPTION 'Double-Entry Invariant Failed for Journal Entry %: Must have at least 2 entry legs (found %)',
            p_journal_entry_id, v_leg_count;
    END IF;

    -- Partition by currency and check that sum(DEBIT) == sum(CREDIT) for each currency
    SELECT currency,
           COALESCE(SUM(CASE WHEN direction = 'DEBIT' THEN amount ELSE 0 END), 0) AS debits,
           COALESCE(SUM(CASE WHEN direction = 'CREDIT' THEN amount ELSE 0 END), 0) AS credits
    INTO v_unbalanced_currency, v_debit_sum, v_credit_sum
    FROM entry_legs
    WHERE journal_entry_id = p_journal_entry_id
    GROUP BY currency
    HAVING COALESCE(SUM(CASE WHEN direction = 'DEBIT' THEN amount ELSE 0 END), 0) <>
           COALESCE(SUM(CASE WHEN direction = 'CREDIT' THEN amount ELSE 0 END), 0)
    LIMIT 1;

    IF v_unbalanced_currency IS NOT NULL THEN
        RAISE EXCEPTION 'Double-Entry Invariant Failed for Journal Entry %: Currency % unbalanced. Total Debits (%) != Total Credits (%)',
            p_journal_entry_id, v_unbalanced_currency, v_debit_sum, v_credit_sum;
    END IF;

    RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- Trigger Function for Deferred Constraint Trigger on Entry Legs
CREATE OR REPLACE FUNCTION fn_trg_verify_journal_entry_balance()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM fn_verify_journal_entry_balance(NEW.journal_entry_id);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Hook Deferred Constraint Trigger on entry_legs (Deferred until COMMIT)
CREATE CONSTRAINT TRIGGER trg_verify_journal_entry_balance
AFTER INSERT OR UPDATE ON entry_legs
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION fn_trg_verify_journal_entry_balance();
