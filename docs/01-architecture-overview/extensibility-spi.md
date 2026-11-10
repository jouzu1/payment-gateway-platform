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
+--------------------------------------------------------------------------------------------------+
|                      PLUGGABLE SERVICE PROVIDER INTERFACE (SPI) ADAPTERS                         |
|                                                                                                  |
|   +-----------------------+  +-----------------------+  +-----------------------+                |
|   | PaymentRailAdapter    |  | PayoutRailAdapter     |  | FXRateProvider        |                |
|   | (Cards, Wallets, QR)  |  | (FedNow, FAST, SWIFT) |  | (LP Feeds, Spot FX)   |                |
|   +-----------------------+  +-----------------------+  +-----------------------+                |
+--------------------------------------------------------------------------------------------------+
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
```

### 2.2 Go SPI Specification

```go
package spi

import (
	"context"
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
```

---

## 3. Dynamic Capability Discovery & Rail Registry

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
