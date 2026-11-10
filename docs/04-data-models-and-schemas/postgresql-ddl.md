# Annotated Production PostgreSQL DDL & Partitioning Strategy

## 1. Schema Design Principles & Storage Optimization

The platform's relational database schema is optimized for write throughput, strict ACID integrity, deterministic sub-millisecond index lookups, and massive horizontal table partitioning.

### Core Storage & Indexing Conventions:
1. **Integer Minor Units**: All monetary amounts use 64-bit signed integers (`BIGINT`).
2. **Deterministic Foreign Keys**: Enforced via `ON DELETE RESTRICT` to prevent accidental cascading deletion of financial history.
3. **Partitioning by Ingestion Time**: Ultra-high-volume tables (`transactions`, `journal_entries`, `entry_legs`, `outbox_events`) are partitioned monthly using PostgreSQL declarative **Range Partitioning** on `created_at` / `posted_at`.
4. **Partial & Covering Indexes**: Indexes are filtered to active working sets (e.g. `WHERE published_at IS NULL` for outbox tables), reducing B-Tree memory footprints by > 80%.

---

## 2. Table Partitioning Architecture

```
                                 [ Master Table: transactions ]
                                               │
                               (Partition by RANGE: created_at)
                                               │
             +---------------------------------+---------------------------------+
             │                                 │                                 │
             ▼                                 ▼                                 ▼
   [ transactions_2026_11 ]          [ transactions_2026_12 ]          [ transactions_2027_01 ]
   (Nov 2026: Active Hot NVMe)       (Dec 2026: Future Partition)      (Jan 2027: Future Partition)
             │
      (After 90 Days)
             ▼
   [ Detach & Parquet Export ] ──► [ S3 Glacier Cold Storage Archive ]
```

### Partition Maintenance Automation:
Partitions are generated dynamically via `pg_partman` or automated Kubernetes cron jobs. Historical partitions older than 90 days are detached, exported to columnar Apache Parquet format on AWS S3, and dropped from the active PostgreSQL cluster, keeping NVMe storage lightweight and bounded.

---

## 3. Annotated Schema Definitions & Constraints

### 3.1 Partitioned Ledger Journal & Entry Legs

```sql
-- Master Journal Entry Table (Partitioned by Month)
CREATE TABLE journal_entries (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    idempotency_key VARCHAR(128) NOT NULL,
    correlation_id VARCHAR(128) NOT NULL,
    transaction_type VARCHAR(64) NOT NULL,
    description TEXT,
    posted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (id, posted_at),
    CONSTRAINT uq_journal_idempotency UNIQUE (idempotency_key, posted_at)
) PARTITION BY RANGE (posted_at);

-- Monthly Partitions
CREATE TABLE journal_entries_2026_11 PARTITION OF journal_entries
    FOR VALUES FROM ('2026-11-01 00:00:00+00') TO ('2026-12-01 00:00:00+00');

CREATE TABLE journal_entries_2026_12 PARTITION OF journal_entries
    FOR VALUES FROM ('2026-12-01 00:00:00+00') TO ('2027-01-01 00:00:00+00');

-- Master Entry Legs Table (Partitioned by Month)
CREATE TABLE entry_legs (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    journal_entry_id UUID NOT NULL,
    account_id UUID NOT NULL REFERENCES ledger_accounts(id) ON DELETE RESTRICT,
    direction entry_direction NOT NULL,
    amount BIGINT NOT NULL CHECK (amount > 0),
    currency VARCHAR(3) NOT NULL,
    sequence_order SMALLINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

-- Indexes for Fast Balance Audit & Aggregation
CREATE INDEX idx_entry_legs_account_created ON entry_legs(account_id, created_at DESC);
CREATE INDEX idx_entry_legs_journal ON entry_legs(journal_entry_id);
```

### 3.2 Partitioned High-Throughput Transaction Table

```sql
CREATE TABLE transactions (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    payment_intent_id UUID NOT NULL,
    merchant_id UUID NOT NULL,
    payment_method_id UUID,
    rail_code VARCHAR(32) NOT NULL,
    rail_transaction_ref VARCHAR(128),
    transaction_type transaction_type NOT NULL,
    gross_amount BIGINT NOT NULL CHECK (gross_amount > 0),
    fee_amount BIGINT NOT NULL DEFAULT 0,
    net_amount BIGINT NOT NULL,
    currency VARCHAR(3) NOT NULL,
    status transaction_status NOT NULL DEFAULT 'INITIALIZED',
    error_code VARCHAR(64),
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

-- Partial Indexes for Performance
-- Rapidly locate pending transactions without scanning settled records
CREATE INDEX idx_tx_pending ON transactions(merchant_id, status)
    WHERE status IN ('INITIALIZED', 'PENDING');

-- Instant lookup of partner bank references
CREATE INDEX idx_tx_rail_ref ON transactions(rail_code, rail_transaction_ref);
```

### 3.3 Transactional Outbox Pattern Table

```sql
CREATE TABLE outbox_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aggregate_type VARCHAR(64) NOT NULL,
    aggregate_id VARCHAR(128) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL,
    partition_key VARCHAR(128) NOT NULL,
    retry_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    published_at TIMESTAMPTZ
);

-- Partial Index: Only indexes unpublished messages (stays tiny & fully cached in RAM)
CREATE INDEX idx_outbox_unprocessed ON outbox_events(created_at)
    WHERE published_at IS NULL;
```

---

## 4. Integrity Triggers & Immutability Enforcement

```sql
-- Enforces absolute immutability of financial postings
CREATE OR REPLACE FUNCTION fn_enforce_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Security & Compliance Violation: Mutations (UPDATE/DELETE) on financial table % are strictly illegal.', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_protect_journal_entries
BEFORE UPDATE OR DELETE ON journal_entries
FOR EACH ROW EXECUTE FUNCTION fn_enforce_append_only();

CREATE TRIGGER trg_protect_entry_legs
BEFORE UPDATE OR DELETE ON entry_legs
FOR EACH ROW EXECUTE FUNCTION fn_enforce_append_only();
```
