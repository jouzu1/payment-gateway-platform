# Pluggable Extensibility SPI & Product Orchestration Engine

## 1. Architectural Philosophy: Zero-Refactoring Additions

A foundational design requirement of the platform is **Extensibility**: new payment methods, partner banks, regional clearing switches, and transactional products (e.g., Buy Now Pay Later, Enterprise Mass Payroll, Crypto-Fiat On/Off Ramps) must be deployable into production without refactoring core transaction routing, double-entry ledger, or accounting engines.

To achieve this, the architecture bifurcates system capability into two orthogonal abstractions:
1. **The Rail Service Provider Interface (SPI)**: Decouples the low-level protocols of external banking switches, card networks, and wallet aggregators from internal business logic.
2. **The Product Orchestration Engine (Workflow DAG)**: Allows new business offerings to be defined declaratively as Directed Acyclic Graphs (DAGs) chaining reusable domain primitives (`Screen`, `Authorize`, `HoldLedger`, `RouteRail`, `Disburse`, `WaterfallSplit`, `Notify`).

```
+--------------------------------------------------------------------------------------------------+
|                            PRODUCT ORCHESTRATION LAYER (WORKFLOW DAG)                            |
|                                                                                                  |
|   [ Core Products ]                     [ Future Products (Zero-Refactoring Additions) ]         |
|   - Local Remittance                    - Buy Now Pay Later (BNPL 4-Pay Installments)            |
|   - Cross-Border FX Payout              - Mass Payroll Batch Disbursals                          |
|   - Card & APM Acquiring                - Crypto-Fiat Stablecoin On/Off Ramps                    |
|   - P2P Lending Escrow                  - Instant Merchant Capital Advance (MCA)                 |
+-------------------------------------------------+------------------------------------------------+
                                                  |
                                                  v Chains Domain Primitives
+--------------------------------------------------------------------------------------------------+
|                            DOMAIN PRIMITIVES & LEDGER CONSTRAINTS                                |
|   - Idempotency Lock      - Real-Time Sanctions Screening      - Hold Ledger Balance             |
|   - Smart Route Selection - Commit Journal Entry               - Dispatch CloudEvent Webhook     |
+-------------------------------------------------+------------------------------------------------+
                                                  |
                                                  v Invokes Standard SPI
+----------------------------------------------------------------------------------------------------------------+
|                             PLUGGABLE SERVICE PROVIDER INTERFACE (SPI) ADAPTERS                                |
|                                                                                                                |
|   +--------------------+  +--------------------+  +--------------------+  +--------------------------------+   |
|   | PaymentRailAdapter |  | PayoutRailAdapter  |  | FXRateProvider     |  | ReconciliationRailAdapter      |   |
|   | (Cards, Wallets)   |  | (Instant & Batch)  |  | (Spot FX Quotes)   |  | (Bank Statements & Netting)    |   |
|   +--------------------+  +--------------------+  +--------------------+  +--------------------------------+   |
+----------------------------------------------------------------------------------------------------------------+
```

---

## 2. Pluggable Service Provider Interface (SPI) Contracts

All external rail integrations implement standardized, strongly-typed SPI contracts. Adapters are deployed as pluggable plugins or independent microservices adhering to standard gRPC / interface contracts.

### 2.1 TypeScript SPI Specification

```typescript
import { Context } from './context';
import { BigNumber } from 'bignumber.js';

// =============================================================================
// 1. PAYMENT RAIL ADAPTER (ACQUIRING & INBOUND PAYIN)
// =============================================================================

export interface PaymentRailAdapter {
  readonly railCode: string;
  readonly supportedPaymentMethods: PaymentMethodType[];
  readonly capabilities: RailCapabilities;

  // Authorization & Capture Lifecycle
  authorize(ctx: Context, req: AuthorizeRequest): Promise<AuthorizeResponse>;
  capture(ctx: Context, req: CaptureRequest): Promise<CaptureResponse>;
  sale(ctx: Context, req: SaleRequest): Promise<SaleResponse>; // Single-message
  cancel(ctx: Context, req: CancelRequest): Promise<CancelResponse>;
  refund(ctx: Context, req: RefundRequest): Promise<RefundResponse>;

  // Webhook Ingestion & Signature Verification
  verifyWebhookSignature(headers: Record<string, string>, rawPayload: Buffer): boolean;
  parseWebhookEvent(headers: Record<string, string>, rawPayload: Buffer): NormalizedRailEvent;

  // Active Health Probing & Inquiry
  queryTransactionStatus(ctx: Context, railTxRef: string): Promise<RailTransactionStatus>;
  checkHealth(ctx: Context): Promise<RailHealthReport>;
}

// =============================================================================
// 2. PAYOUT RAIL ADAPTER (DOMESTIC & CROSS-BORDER REMITTANCE)
// =============================================================================

export interface PayoutRailAdapter {
  readonly railCode: string;
  readonly supportedCurrencies: string[];
  readonly settlementModel: 'INSTANT_GROSS' | 'DEFERRED_NET' | 'BATCH';

  // Counterparty Verification
  validateBeneficiary(ctx: Context, req: BeneficiaryProbeRequest): Promise<BeneficiaryProbeResponse>;

  // Payout Dispatch & Tracking
  submitPayout(ctx: Context, req: PayoutSubmissionRequest): Promise<PayoutSubmissionResponse>;
  queryPayoutStatus(ctx: Context, railPayoutRef: string): Promise<PayoutStatusReport>;
  cancelPayout(ctx: Context, railPayoutRef: string): Promise<CancelPayoutResponse>;
  getSettlementBalance(ctx: Context, currency: string): Promise<RailBalanceReport>;
}

// =============================================================================
// 3. FX RATE PROVIDER SPI
// =============================================================================

export interface FXRateProvider {
  readonly providerId: string;
  getSpotRate(ctx: Context, baseCurrency: string, targetCurrency: string): Promise<SpotRate>;
  lockGuaranteedRate(ctx: Context, req: LockRateRequest): Promise<GuaranteedQuote>;
  executeFXDeal(ctx: Context, quoteId: string, baseAmount: BigNumber): Promise<FXDealConfirmation>;
}

// =============================================================================
// 4. AML & SANCTIONS SCREENING PROVIDER SPI
// =============================================================================

export interface AMLScreeningProvider {
  readonly providerId: string;
  screenParty(ctx: Context, req: PartyScreeningRequest): Promise<ScreeningResult>;
  screenTransaction(ctx: Context, req: TransactionScreeningRequest): Promise<ScreeningResult>;
}

// =============================================================================
// 5. RECONCILIATION RAIL ADAPTER SPI (UNIVERSAL PLUGGABLE RECONCILIATION)
// =============================================================================

export type MatchingTopologyType =
  | 'DIRECT_1_TO_1'
  | 'NETTING_N_TO_M'
  | 'TRIPARTITE_ESCROW'
  | 'STREAMING_INTRADAY'
  | 'CRYPTOGRAPHIC_ON_CHAIN';

export type StatementFormat =
  | 'CAMT_053'
  | 'CAMT_052'
  | 'CAMT_054'
  | 'MT940'
  | 'MT942'
  | 'BAI2'
  | 'NACHA'
  | 'CSV'
  | 'JSON'
  | 'ON_CHAIN_RECEIPT';

export interface StatementFetchCriteria {
  readonly accountId?: string;
  readonly currency?: string;
  readonly fromDate: string; // ISO 8601 UTC
  readonly toDate: string;   // ISO 8601 UTC
  readonly statementFormat?: StatementFormat;
  readonly maxStatements?: number;
  readonly paginationToken?: string;
}

export interface RawStatementPayload {
  readonly statementReference: string;
  readonly railCode: string;
  readonly format: StatementFormat;
  readonly fileHashSha256: string;
  readonly rawData: Buffer;
  readonly remoteStorageUrl?: string;
  readonly fetchedAt: string;
  readonly metadata?: Record<string, string>;
}

export interface NormalizedStatementLine {
  readonly lineSequence: number;
  readonly bankReference: string;
  readonly endToEndId?: string;
  readonly railTransactionRef?: string;
  readonly batchReference?: string;
  readonly accountId: string;
  readonly amountCents: BigNumber; // Minor currency units (BIGINT)
  readonly currency: string;
  readonly direction: 'DEBIT' | 'CREDIT';
  readonly valueDate: string;   // YYYY-MM-DD
  readonly bookingDate: string; // YYYY-MM-DD (Partition Key)
  readonly proprietaryCode?: string;
  readonly narrative?: string;
  readonly lineHash: string; // SHA-256 composite line hash
  readonly onChainMetadata?: {
    readonly txHash: string;
    readonly blockNumber: number;
    readonly contractAddress: string;
    readonly logIndex?: number;
  };
}

export interface ReconciliationRailCapabilities {
  readonly railCode: string;
  readonly supportedFormats: StatementFormat[];
  readonly supportedTopologies: MatchingTopologyType[];
  readonly supportsStreaming: boolean;
  readonly supportsIntraday: boolean;
  readonly supportsOnChainProofs: boolean;
  readonly autoRemediationSupported: boolean;
  readonly maxBatchLineCount: number;
  readonly hashAlgorithm: 'SHA256' | 'SHA512';
}

export interface ReconciliationRailAdapter {
  readonly railCode: string;

  // 1. Discovery & Capabilities
  getCapabilities(): ReconciliationRailCapabilities;

  // 2. Statement Ingestion & Parsing
  fetchStatements(ctx: Context, criteria: StatementFetchCriteria): Promise<RawStatementPayload[]>;
  parseStatementLines(ctx: Context, rawPayload: Buffer, format: StatementFormat): Promise<NormalizedStatementLine[]>;
  streamStatementLines?(ctx: Context, rawStream: NodeJS.ReadableStream): AsyncIterable<NormalizedStatementLine>;

  // 3. Declarative Profile Retrieval
  getReconciliationProfile(ctx: Context): Promise<ReconciliationProfile>;

  // 4. Break Notification & Feedback
  notifyDiscrepancyBreak?(ctx: Context, notification: DiscrepancyBreakNotification): Promise<BreakAcknowledgeReport>;
}

export interface DiscrepancyBreakNotification {
  readonly breakId: string;
  readonly railCode: string;
  readonly bankReference?: string;
  readonly endToEndId?: string;
  readonly discrepancyClass: 'UNMATCHED_INTERNAL' | 'UNMATCHED_EXTERNAL' | 'AMOUNT_DISCREPANCY' | 'TIMING_DRIFT';
  readonly varianceAmountCents: BigNumber;
  readonly detectedAt: string;
  readonly narrative: string;
}

export interface BreakAcknowledgeReport {
  readonly acknowledged: boolean;
  readonly externalDisputeId?: string;
  readonly status: 'ACCEPTED' | 'REJECTED' | 'UNDER_REVIEW';
}
```

### 2.2 Go SPI Specification

```go
package spi

import (
	"context"
	"io"
	"time"
)

// PaymentRailAdapter defines the interface for inbound acquiring rails
type PaymentRailAdapter interface {
	RailCode() string
	Capabilities() RailCapabilities

	Authorize(ctx context.Context, req *AuthorizeRequest) (*AuthorizeResponse, error)
	Capture(ctx context.Context, req *CaptureRequest) (*CaptureResponse, error)
	Sale(ctx context.Context, req *SaleRequest) (*SaleResponse, error)
	Cancel(ctx context.Context, req *CancelRequest) (*CancelResponse, error)
	Refund(ctx context.Context, req *RefundRequest) (*RefundResponse, error)

	VerifyWebhook(headers map[string]string, payload []byte) bool
	ParseWebhook(headers map[string]string, payload []byte) (*NormalizedRailEvent, error)
	QueryStatus(ctx context.Context, railTxRef string) (*RailStatusReport, error)
}

// PayoutRailAdapter defines the interface for outbound disbursement rails
type PayoutRailAdapter interface {
	RailCode() string
	SettlementModel() SettlementType

	ValidateBeneficiary(ctx context.Context, req *BeneficiaryProbeRequest) (*BeneficiaryProbeResponse, error)
	SubmitPayout(ctx context.Context, req *PayoutSubmissionRequest) (*PayoutSubmissionResponse, error)
	QueryPayoutStatus(ctx context.Context, railRef string) (*PayoutStatusReport, error)
	CancelPayout(ctx context.Context, railRef string) (*CancelPayoutResponse, error)
}

// FXProvider defines the interface for liquidity providers
type FXProvider interface {
	GetSpotRate(ctx context.Context, base, target string) (*SpotRate, error)
	LockQuote(ctx context.Context, req *QuoteRequest) (*GuaranteedQuote, error)
	ExecuteDeal(ctx context.Context, quoteID string, amount int64) (*DealConfirmation, error)
}

// StatementFormat defines the supported bank statement or feed format
type StatementFormat string

const (
	FormatCamt053        StatementFormat = "CAMT_053"
	FormatCamt052        StatementFormat = "CAMT_052"
	FormatCamt054        StatementFormat = "CAMT_054"
	FormatMT940          StatementFormat = "MT940"
	FormatMT942          StatementFormat = "MT942"
	FormatBAI2           StatementFormat = "BAI2"
	FormatNACHA          StatementFormat = "NACHA"
	FormatCSV            StatementFormat = "CSV"
	FormatJSON           StatementFormat = "JSON"
	FormatOnChainReceipt StatementFormat = "ON_CHAIN_RECEIPT"
)

// MatchingTopology defines the active reconciliation matching model
type MatchingTopology string

const (
	TopologyDirect1To1           MatchingTopology = "DIRECT_1_TO_1"
	TopologyNettingNToM          MatchingTopology = "NETTING_N_TO_M"
	TopologyTripartiteEscrow     MatchingTopology = "TRIPARTITE_ESCROW"
	TopologyStreamingIntraday    MatchingTopology = "STREAMING_INTRADAY"
	TopologyCryptographicOnChain MatchingTopology = "CRYPTOGRAPHIC_ON_CHAIN"
)

// StatementFetchCriteria specifies search parameters for retrieving clearing statements
type StatementFetchCriteria struct {
	AccountID       string
	Currency        string
	FromDate        time.Time
	ToDate          time.Time
	StatementFormat StatementFormat
	MaxStatements   int
	PaginationToken string
}

// RawStatementPayload holds unparsed statement content and Level-1 idempotency hash
type RawStatementPayload struct {
	StatementReference string
	RailCode           string
	Format             StatementFormat
	FileHashSha256     string
	RawData            []byte
	RemoteStorageURL   string
	FetchedAt          time.Time
	Metadata           map[string]string
}

// NormalizedStatementLine represents a canonical statement record
type NormalizedStatementLine struct {
	LineSequence       int64
	BankReference      string
	EndToEndID         string
	RailTransactionRef string
	BatchReference     string
	AccountID          string
	AmountCents        int64 // Minor currency units (BIGINT)
	Currency           string
	Direction          string // "DEBIT" or "CREDIT"
	ValueDate          string // YYYY-MM-DD
	BookingDate        string // YYYY-MM-DD (Partition Key)
	ProprietaryCode    string
	Narrative          string
	LineHash           string // SHA-256 composite line hash
	TxHash             string // Web3 blockchain tx hash (optional)
	BlockNumber        int64  // Web3 block number (optional)
}

// ReconciliationRailCapabilities details the feature flags and limits of the rail adapter
type ReconciliationRailCapabilities struct {
	RailCode                 string
	SupportedFormats         []StatementFormat
	SupportedTopologies      []MatchingTopology
	SupportsStreaming        bool
	SupportsIntraday         bool
	SupportsOnChainProofs    bool
	AutoRemediationSupported bool
	MaxBatchLineCount        int
	HashAlgorithm            string
}

// ReconciliationRailAdapter defines the SPI for bank statement ingestion and recon
type ReconciliationRailAdapter interface {
	RailCode() string
	GetCapabilities() ReconciliationRailCapabilities

	FetchStatements(ctx context.Context, criteria *StatementFetchCriteria) ([]*RawStatementPayload, error)
	ParseStatementLines(ctx context.Context, raw []byte, format StatementFormat) ([]*NormalizedStatementLine, error)
	GetReconciliationProfile(ctx context.Context) (*ReconciliationProfile, error)

	StreamStatementLines(ctx context.Context, r io.Reader) (<-chan *NormalizedStatementLine, <-chan error)
	NotifyDiscrepancyBreak(ctx context.Context, req *BreakNotificationRequest) (*BreakAcknowledgeResponse, error)
}
```

---

## 3. Dynamic Capability Discovery & Declarative Rail Profiles

The platform decouples business orchestration from partner rail specificities using two declarative registration mechanisms:
1. **The Adapter Capability Registry**: Registers runtime execution features for payment and payout rails used by the Smart Routing Engine.
2. **The Declarative Reconciliation Profile**: Defines bank statement ingestion, matching topologies, multi-tier composite keys, tolerance rules, and break routing used by the Universal Pluggable Reconciliation Engine (see [Universal Reconciliation Engine](../03-core-subsystems/universal-reconciliation-engine.md)).

### 3.1 Adapter Capability Registry (Execution Rails)

At service bootstrap, every payment and payout adapter registers its metadata and operating constraints into the central **Adapter Capability Registry**:

```json
{
  "rail_code": "FEDNOW_DIRECT",
  "protocol": "ISO20022_PACS008",
  "settlement_type": "INSTANT_GROSS",
  "operating_hours": "24x7x365",
  "capabilities": {
    "supports_beneficiary_probe": true,
    "supports_partial_capture": false,
    "supports_cancellation": false,
    "supports_recurring_mandates": false,
    "max_amount_per_transaction": 50000000,
    "currency": "USD",
    "typical_latency_ms": 1200,
    "cut_off_windows": []
  }
}
```

The Smart Routing Engine inspects these registered capabilities dynamically. When routing an outbound transaction, rails lacking required capabilities (e.g., instant clearing or account probing) are pruned from the candidate routing set automatically.

### 3.2 Declarative Reconciliation Profile Specification (`ReconciliationProfile`)

Each external clearing switch or banking partner defines a declarative `ReconciliationProfile` JSON descriptor. This enables zero-code onboarding of new bank statement layouts, custom matching strategies, and multi-rail settlement topologies without refactoring core reconciliation logic.

#### 3.2.1 Specification Schema & Core Fields

The profile specification encompasses 7 mandatory dimensions:

```json
{
  "$schema": "https://schemas.gateway.internal/v1/reconciliation-profile.schema.json",
  "profile_version": "1.0.0",
  "rail_id": "STRING (Unique identifier for clearing switch / rail)",
  "topology_type": "ENUM (DIRECT_1_TO_1 | NETTING_N_TO_M | TRIPARTITE_ESCROW | STREAMING_INTRADAY | CRYPTOGRAPHIC_ON_CHAIN)",
  "matching_strategy": {
    "strategy_mode": "ENUM (DETERMINISTIC | HEURISTIC_SCORE | NET_BATCH_AGGREGATE | WATERFALL_TRI_PARTY | BLOCKCHAIN_FINALITY)",
    "primary_key": "STRING (Field used for primary deterministic pairing)",
    "fallback_chain": ["ARRAY OF ENUM (PRIMARY_KEY, COMPOSITE_TIER_1, COMPOSITE_TIER_2, SLIDING_WINDOW)"],
    "confidence_threshold": "FLOAT (0.0 to 1.0, minimum score required for auto-pairing)"
  },
  "composite_keys": [
    {
      "tier": "INTEGER (1, 2, or 3)",
      "name": "STRING (Human readable composite key name)",
      "fields": ["ARRAY OF STRINGS (Field names combined to form hash key)"],
      "case_sensitive": "BOOLEAN",
      "strip_whitespace": "BOOLEAN"
    }
  ],
  "tolerance_rules": {
    "amount_cents": "INTEGER (Maximum minor-unit discrepancy allowed for auto-write-off)",
    "basis_points": "INTEGER (Maximum relative fee deviation in basis points, e.g. 15 = 0.15%)",
    "time_drift_seconds": "INTEGER (Maximum allowable value-date/timestamp variance)",
    "auto_write_off": "BOOLEAN (True to auto-post variance to write_off_ledger_account)",
    "write_off_ledger_account": "STRING (GL account number, e.g. 510000)"
  },
  "cut_off_schedule": {
    "timezone": "STRING (IANA timezone, e.g. UTC, America/New_York, Asia/Jakarta)",
    "cycles": ["ARRAY OF STRINGS (Cut-off times in HH:MM format)"],
    "grace_period_minutes": "INTEGER (Minutes after cut-off before alerting for missing file)",
    "statement_expected_offset_hours": "INTEGER (Hours after cut-off when statement file must be available)",
    "weekend_settlement_lag_days": "INTEGER (Additional value-date lag over banking weekends)"
  },
  "break_routing": {
    "unmatched_internal": {
      "escalation_queue": "STRING (Operations queue name)",
      "auto_action": "STRING (POLL_RAIL_STATUS | CANCEL_RESERVATION | DISPUTE_ALERT)",
      "suspense_account": "STRING (GL account number, e.g. 110900 In-Transit)",
      "sla_hours": "INTEGER (Target resolution time)"
    },
    "unmatched_external": {
      "escalation_queue": "STRING (Operations queue name)",
      "auto_action": "STRING (POST_TO_SUSPENSE | AUTO_REFUND | HOLD_FOR_INQUIRY)",
      "suspense_account": "STRING (GL account number, e.g. 240900 Unallocated)",
      "sla_hours": "INTEGER (Target resolution time)"
    },
    "amount_mismatch": {
      "escalation_queue": "STRING (Operations queue name)",
      "maker_checker_threshold_cents": "INTEGER (Discrepancy cents requiring 4-eyes approval)",
      "suspense_account": "STRING (GL account number, e.g. 510000 Fee Variance)",
      "sla_hours": "INTEGER (Target resolution time)"
    },
    "aging_sla_tiers": {
      "tier1_warning_hours": "INTEGER",
      "tier2_escalation_hours": "INTEGER",
      "tier3_executive_hours": "INTEGER"
    }
  }
}
```

The profile specifies:
1. **`rail_id`**: The canonical clearing switch identifier (`FEDNOW_DIRECT`, `CARD_VISA_INTERCHANGE`, `P2P_ESCROW_CUSTODIAN`, `QRIS_ARTAJASA`, `CRYPTO_USDC_POLYGON`).
2. **`topology_type`**: The active matching model (`DIRECT_1_TO_1`, `NETTING_N_TO_M`, `TRIPARTITE_ESCROW`, `STREAMING_INTRADAY`, `CRYPTOGRAPHIC_ON_CHAIN`).
3. **`matching_strategy`**: Directs whether matching executes deterministically on a primary key (e.g. `end_to_end_id`), via heuristic confidence scoring, or batch gross-to-net aggregation.
4. **`composite_keys`**: Multi-tiered fallback keys computed when primary references are truncated or missing in external statements.
5. **`tolerance_rules`**: Quantifiable variance limits ($\le \$2.00$ or $\le 15\text{ bps}$) permitted for automated resolution, with the destination general ledger account (`510000 - Bank Switch Fee Variance Expense`).
6. **`cut_off_schedule`**: Banking cut-off schedules, timezones, and statement delivery SLA windows.
7. **`break_routing`**: Operational exception routing, automated actions, designated suspense accounts (`110900`, `240900`, `510000`), and Maker-Checker dual authorization thresholds (\$500.00).

#### 3.2.2 Five Production Profile Configurations

##### Profile 1: Local Remittance Instant Rails (1:1 Direct Matching)
```json
{
  "profile_version": "1.0.0",
  "rail_id": "FEDNOW_DIRECT",
  "topology_type": "DIRECT_1_TO_1",
  "matching_strategy": {
    "strategy_mode": "DETERMINISTIC",
    "primary_key": "end_to_end_id",
    "fallback_chain": ["PRIMARY_KEY", "COMPOSITE_TIER_1"],
    "confidence_threshold": 1.0
  },
  "composite_keys": [
    {
      "tier": 1,
      "name": "fednow_composite_ref_amt",
      "fields": ["bank_reference", "booking_date", "amount_cents", "direction"],
      "case_sensitive": false,
      "strip_whitespace": true
    }
  ],
  "tolerance_rules": {
    "amount_cents": 200,
    "basis_points": 10,
    "time_drift_seconds": 3600,
    "auto_write_off": true,
    "write_off_ledger_account": "510000"
  },
  "cut_off_schedule": {
    "timezone": "America/New_York",
    "cycles": ["23:59"],
    "grace_period_minutes": 30,
    "statement_expected_offset_hours": 1,
    "weekend_settlement_lag_days": 0
  },
  "break_routing": {
    "unmatched_internal": {
      "escalation_queue": "instant-recon-tier1",
      "auto_action": "POLL_RAIL_STATUS",
      "suspense_account": "110900",
      "sla_hours": 24
    },
    "unmatched_external": {
      "escalation_queue": "treasury-unallocated",
      "auto_action": "POST_TO_SUSPENSE",
      "suspense_account": "240900",
      "sla_hours": 24
    },
    "amount_mismatch": {
      "escalation_queue": "ops-dispute",
      "maker_checker_threshold_cents": 50000,
      "suspense_account": "510000",
      "sla_hours": 12
    },
    "aging_sla_tiers": {
      "tier1_warning_hours": 12,
      "tier2_escalation_hours": 24,
      "tier3_executive_hours": 72
    }
  }
}
```

##### Profile 2: Card Acquiring Scheme Settlements (N:M Netting Matching)
```json
{
  "profile_version": "1.0.0",
  "rail_id": "CARD_VISA_INTERCHANGE",
  "topology_type": "NETTING_N_TO_M",
  "matching_strategy": {
    "strategy_mode": "NET_BATCH_AGGREGATE",
    "primary_key": "settlement_batch_id",
    "fallback_chain": ["PRIMARY_KEY", "COMPOSITE_TIER_1"],
    "confidence_threshold": 0.99
  },
  "composite_keys": [
    {
      "tier": 1,
      "name": "card_clearing_cycle_rollup",
      "fields": ["rail_id", "booking_date", "currency", "direction"],
      "case_sensitive": false,
      "strip_whitespace": true
    }
  ],
  "tolerance_rules": {
    "amount_cents": 5000,
    "basis_points": 25,
    "time_drift_seconds": 86400,
    "auto_write_off": false,
    "write_off_ledger_account": "510000"
  },
  "cut_off_schedule": {
    "timezone": "UTC",
    "cycles": ["06:00", "14:00", "22:00"],
    "grace_period_minutes": 120,
    "statement_expected_offset_hours": 4,
    "weekend_settlement_lag_days": 2
  },
  "break_routing": {
    "unmatched_internal": {
      "escalation_queue": "card-ops-interchange",
      "auto_action": "DISPUTE_ALERT",
      "suspense_account": "110900",
      "sla_hours": 48
    },
    "unmatched_external": {
      "escalation_queue": "card-scheme-unmatched",
      "auto_action": "POST_TO_SUSPENSE",
      "suspense_account": "240900",
      "sla_hours": 48
    },
    "amount_mismatch": {
      "escalation_queue": "interchange-auditors",
      "maker_checker_threshold_cents": 100000,
      "suspense_account": "510000",
      "sla_hours": 24
    },
    "aging_sla_tiers": {
      "tier1_warning_hours": 24,
      "tier2_escalation_hours": 48,
      "tier3_executive_hours": 168
    }
  }
}
```

##### Profile 3: P2P Lending FBO Trust Escrow (Tripartite Escrow Matching)
```json
{
  "profile_version": "1.0.0",
  "rail_id": "P2P_ESCROW_CUSTODIAN",
  "topology_type": "TRIPARTITE_ESCROW",
  "matching_strategy": {
    "strategy_mode": "WATERFALL_TRI_PARTY",
    "primary_key": "loan_id",
    "fallback_chain": ["PRIMARY_KEY", "COMPOSITE_TIER_1"],
    "confidence_threshold": 1.0
  },
  "composite_keys": [
    {
      "tier": 1,
      "name": "escrow_borrower_collection_key",
      "fields": ["bank_reference", "account_id", "booking_date", "amount_cents"],
      "case_sensitive": false,
      "strip_whitespace": true
    }
  ],
  "tolerance_rules": {
    "amount_cents": 0,
    "basis_points": 0,
    "time_drift_seconds": 0,
    "auto_write_off": false,
    "write_off_ledger_account": "210400"
  },
  "cut_off_schedule": {
    "timezone": "Asia/Jakarta",
    "cycles": ["17:00"],
    "grace_period_minutes": 60,
    "statement_expected_offset_hours": 2,
    "weekend_settlement_lag_days": 1
  },
  "break_routing": {
    "unmatched_internal": {
      "escalation_queue": "escrow-compliance",
      "auto_action": "POLL_RAIL_STATUS",
      "suspense_account": "110400",
      "sla_hours": 4
    },
    "unmatched_external": {
      "escalation_queue": "escrow-custody-ops",
      "auto_action": "POST_TO_SUSPENSE",
      "suspense_account": "240900",
      "sla_hours": 4
    },
    "amount_mismatch": {
      "escalation_queue": "escrow-audit-four-eyes",
      "maker_checker_threshold_cents": 1,
      "suspense_account": "210400",
      "sla_hours": 2
    },
    "aging_sla_tiers": {
      "tier1_warning_hours": 4,
      "tier2_escalation_hours": 8,
      "tier3_executive_hours": 24
    }
  }
}
```

##### Profile 4: Dynamic QRIS & UPI Acquiring (Streaming Intraday Sliding-Window Matching)
```json
{
  "profile_version": "1.0.0",
  "rail_id": "QRIS_ARTAJASA",
  "topology_type": "STREAMING_INTRADAY",
  "matching_strategy": {
    "strategy_mode": "HEURISTIC_SCORE",
    "primary_key": "rail_transaction_ref",
    "fallback_chain": ["PRIMARY_KEY", "COMPOSITE_TIER_1", "SLIDING_WINDOW"],
    "confidence_threshold": 0.95
  },
  "composite_keys": [
    {
      "tier": 1,
      "name": "qris_intraday_virtual_account",
      "fields": ["account_id", "amount_cents", "proprietary_code"],
      "case_sensitive": false,
      "strip_whitespace": true
    }
  ],
  "tolerance_rules": {
    "amount_cents": 0,
    "basis_points": 0,
    "time_drift_seconds": 30,
    "auto_write_off": false,
    "write_off_ledger_account": "510000"
  },
  "cut_off_schedule": {
    "timezone": "Asia/Jakarta",
    "cycles": ["23:59"],
    "grace_period_minutes": 15,
    "statement_expected_offset_hours": 1,
    "weekend_settlement_lag_days": 0
  },
  "break_routing": {
    "unmatched_internal": {
      "escalation_queue": "realtime-streaming-ops",
      "auto_action": "HOLD_IN_REDIS_BUFFER",
      "suspense_account": "110900",
      "sla_hours": 1
    },
    "unmatched_external": {
      "escalation_queue": "unallocated-intraday",
      "auto_action": "POST_TO_SUSPENSE",
      "suspense_account": "240900",
      "sla_hours": 1
    },
    "amount_mismatch": {
      "escalation_queue": "switch-investigation",
      "maker_checker_threshold_cents": 10000,
      "suspense_account": "510000",
      "sla_hours": 2
    },
    "aging_sla_tiers": {
      "tier1_warning_hours": 1,
      "tier2_escalation_hours": 4,
      "tier3_executive_hours": 24
    }
  }
}
```

##### Profile 5: Web3 Stablecoin Fiat On/Off Ramps (Cryptographic On-Chain Matching)
```json
{
  "profile_version": "1.0.0",
  "rail_id": "CRYPTO_USDC_POLYGON",
  "topology_type": "CRYPTOGRAPHIC_ON_CHAIN",
  "matching_strategy": {
    "strategy_mode": "BLOCKCHAIN_FINALITY",
    "primary_key": "tx_hash",
    "fallback_chain": ["PRIMARY_KEY", "COMPOSITE_TIER_1"],
    "confidence_threshold": 1.0
  },
  "composite_keys": [
    {
      "tier": 1,
      "name": "on_chain_event_log",
      "fields": ["contract_address", "block_number", "amount_cents", "direction"],
      "case_sensitive": false,
      "strip_whitespace": true
    }
  ],
  "tolerance_rules": {
    "amount_cents": 10,
    "basis_points": 5,
    "time_drift_seconds": 600,
    "auto_write_off": true,
    "write_off_ledger_account": "510500"
  },
  "cut_off_schedule": {
    "timezone": "UTC",
    "cycles": ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"],
    "grace_period_minutes": 10,
    "statement_expected_offset_hours": 0,
    "weekend_settlement_lag_days": 0
  },
  "break_routing": {
    "unmatched_internal": {
      "escalation_queue": "crypto-custody-tier1",
      "auto_action": "RETRY_RPC_RECEIPT_PROBE",
      "suspense_account": "110700",
      "sla_hours": 2
    },
    "unmatched_external": {
      "escalation_queue": "crypto-compliance-aml",
      "auto_action": "SCREEN_ON_CHAIN_SOURCE",
      "suspense_account": "240900",
      "sla_hours": 4
    },
    "amount_mismatch": {
      "escalation_queue": "gas-slippage-ops",
      "maker_checker_threshold_cents": 25000,
      "suspense_account": "510500",
      "sla_hours": 4
    },
    "aging_sla_tiers": {
      "tier1_warning_hours": 2,
      "tier2_escalation_hours": 6,
      "tier3_executive_hours": 24
    }
  }
}
```

---

## 4. Unified Error Taxonomy & Normalization Layer

Different payment rails, banking networks, and card schemes emit radically divergent error structures (e.g. ISO 8583 response codes, NACHA R-codes, SWIFT reject codes). The adapter normalization layer intercepts all native responses and maps them into the standardized **Platform Error Taxonomy**:

| Rail Type | Native Code | Description | Unified Platform Error Code | HTTP Status | Actionable Remediation |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Card (ISO 8583)** | `51` | Insufficient funds | `INSUFFICIENT_FUNDS` | 402 | Prompt user to choose alternative card/wallet |
| **Card (ISO 8583)** | `54` | Expired card | `CARD_EXPIRED` | 400 | Reject request; prompt customer for updated card |
| **Card (ISO 8583)** | `05` | Do not honor | `BANK_DECLINED` | 402 | Prompt customer to contact issuing bank |
| **ACH (NACHA)** | `R01` | Insufficient funds | `INSUFFICIENT_FUNDS` | 402 | Schedule automated retry in 48 hours |
| **ACH (NACHA)** | `R02` / `R03`| Account closed / No account| `ACCOUNT_CLOSED_OR_INVALID` | 422 | Invalidate stored bank account; halt recurring pulls |
| **ISO 20022** | `AC04` | Closed account number | `ACCOUNT_CLOSED` | 422 | Cancel payout; auto-refund locked balance |
| **ISO 20022** | `AM04` | Insufficient funds in nostro | `NOSTRO_LIQUIDITY_EXHAUSTED` | 503 | Circuit-break corridor; alert Treasury team |
| **E-Wallet** | `OTP_EXPIRED`| Customer authorization timeout| `AUTH_SESSION_EXPIRED` | 408 | Regenerate payment intent token |
| **Domestic Switch**| `NAME_MISMATCH`| Beneficiary name does not match| `BENEFICIARY_PROBE_FAILED` | 422 | Flag for merchant confirmation |

---

## 5. Product Orchestration Engine (Workflow DAG)

The platform treats financial products as directed acyclic workflow graphs executed by an event-sourced workflow runtime. Adding a new product is purely an exercise in authoring a new declarative DAG configuration.

### 5.1 Architecture of a Workflow Step
Every step in a workflow DAG implements a dual-method contract:
- `Execute(ctx, state) -> Result`: Advances the positive path.
- `Compensate(ctx, state) -> Result`: Reverses previously executed state changes if a downstream step fails (Saga Pattern).

```
[ Step 1: Hold Ledger Funds ] ──► [ Step 2: AML Screening ] ──► [ Step 3: Rail Dispatch ]
             │                               │                              │
             ▼ (Compensate)                  ▼ (Compensate)                 ▼ (Compensate)
[ Release Ledger Reservation ]   [ Log Audit Flag ]             [ Submit Rail Cancel / Refund ]
```

---

## 6. Blueprints for Future Product Extensions

### 6.1 Buy Now Pay Later (BNPL 4-Pay Installments)
Adding a BNPL installment product requires **zero modifications** to the core card acquirer or ledger engines:
1. **Origination Step**: Merchant checkout invokes `PaymentIntentService` with product type `BNPL`.
2. **Underwriting Step**: Invokes Risk SPI with customer data; evaluates credit scoring model.
3. **Ledger Immediate Settle Step**: Immediately settles 100% of the basket value to the merchant balance:
   - `DEBIT: Asset: BNPL Receivables`
   - `CREDIT: Liability: Merchant Available Balance`
   - `CREDIT: Revenue: Platform BNPL Merchant Fee`
4. **Installment Plan Generator**: Generates 4 equal installments due at $T+0, T+14, T+28, T+42$ days.
5. **Scheduled Auto-Debit Step**: On due dates, the scheduled worker invokes existing `PaymentRailAdapter.sale()` using the customer's vaulted payment method.

### 6.2 Enterprise Mass Payroll Batch Disbursals
Adding high-volume payroll dispatches without ledger refactoring:
1. **File Ingestion Step**: Enterprise client uploads payroll file (CSV or ISO 20022 `pain.001`).
2. **Validation & Batch Aggregation Step**: Parser calculates total gross batch payout $\sum P$ and validates bank codes.
3. **Atomic Batch Ledger Hold**: Books an atomic reservation on enterprise operating balance for $\sum P$.
4. **Parallel Sub-Workflow Dispatch**: Spawns concurrent sub-tasks calling existing `PayoutRailAdapter.submitPayout()` across local instant switches (FedNow / BI-FAST).
5. **Rollup & Settle Step**: As callbacks arrive, ledger converts individual holds to permanent bank transit credits, dispatching a consolidated batch webhook upon 100% completion.

### 6.3 Crypto-Fiat Stablecoin On/Off Ramps
Adding Web3 financial gateways:
- **Fiat-to-Crypto (On-Ramp)**:
  - Step 1: Capture fiat via existing Payin (Cards / Virtual Accounts).
  - Step 2: Upon state reaching `CAPTURED`, emit event triggering the `CryptoRailAdapter` SPI.
  - Step 3: Smart contract mints or transfers USDC/USDT on-chain to user's wallet address.
- **Crypto-to-Fiat (Off-Ramp)**:
  - Step 1: Deposit detector watches custodial blockchain address for stablecoin transfer.
  - Step 2: Upon 12 block confirmations, invoke existing `PayoutRailAdapter.submitPayout()` to remit fiat directly to the user's domestic bank account.
