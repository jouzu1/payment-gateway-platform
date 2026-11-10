# Internal High-Throughput gRPC & Protobuf Service Specifications

## 1. Internal Communication Architecture

While external merchants interact with the platform over public REST APIs, all internal microservice-to-microservice communications execute over **gRPC (HTTP/2 with Protocol Buffers v3)**.

### Architectural Advantages:
- **Binary Serialization**: Protocol Buffers compress payloads to ~20% the byte size of equivalent JSON, drastically reducing serialization CPU overhead and memory bandwidth.
- **HTTP/2 Multiplexing**: Multiple concurrent RPC calls execute across a single persistent TCP connection, eliminating TCP handshake and slow-start latencies.
- **Strict Interface Typing**: Compile-time schema guarantees eliminate schema mismatch bugs between upstream product workflows and downstream core banking engines.

The complete executable Protobuf definition is maintained at:
`schemas/service.proto`

---

## 2. Service Definitions & Remote Procedure Call (RPC) Matrix

```
+--------------------------------------------------------------------------------------------------+
|                                    INTERNAL gRPC SERVICE MESH                                    |
|                                                                                                  |
|   +-----------------------+     +-----------------------+     +------------------------------+   |
|   |    PaymentService     |     |     PayoutService     |     |        LedgerService         |   |
|   |  - AuthorizePayment   |     |  - ValidateBeneficiary|     |  - CreateJournalEntry        |   |
|   |  - CapturePayment     |     |  - SubmitPayout       |     |  - HoldFunds / ReleaseHold   |   |
|   |  - CancelPayment      |     |  - QueryPayoutStatus  |     |  - CommitHold                |   |
|   |  - RefundPayment      |     |  - CancelPayout       |     |  - GetAccountBalance         |   |
|   +-----------------------+     +-----------------------+     +------------------------------+   |
|                                                                                                  |
|   +-----------------------+     +-----------------------+     +------------------------------+   |
|   |    RoutingService     |     |      RiskService      |     |      SettlementService       |   |
|   |  - EvaluateRoute      |     |  - ScreenParty        |     |  - ProcessCutOffBatch        |   |
|   |  - ReportRailHealth   |     |  - Evaluate3DSRisk    |     |  - IngestBankStatement       |   |
|   +-----------------------+     |  - ScreenTransaction  |     |  - Run3WayReconciliation     |   |
|                                 +-----------------------+     +------------------------------+   |
+--------------------------------------------------------------------------------------------------+
```

### 2.1 LedgerService RPC Details
The `LedgerService` manages all atomic postings and reservations:
- `CreateJournalEntry(CreateJournalEntryRequest) -> (CreateJournalEntryResponse)`: Commits an immutable, balanced multi-leg journal entry. Enforces $\sum \text{Debits} \equiv \sum \text{Credits}$ within the atomic transaction.
- `HoldFunds(HoldFundsRequest) -> (HoldFundsResponse)`: Atomically reserves funds on a liability or asset account, returning a `reservation_id` with an explicit TTL.
- `ReleaseHold(ReleaseHoldRequest) -> (ReleaseHoldResponse)`: Reverses an active reservation, restoring available balance.
- `CommitHold(CommitHoldRequest) -> (CommitHoldResponse)`: Converts a reservation into a permanent settled debit/credit leg pair.

### 2.2 RoutingService RPC Details
- `EvaluateRoute(EvaluateRouteRequest) -> (EvaluateRouteResponse)`: Evaluates currency, amount, rail health, and cost metrics, returning the ranked optimal path: `selected_rail_code` and `fallback_rails`.
- `ReportRailHealth(ReportRailHealthRequest) -> (ReportRailHealthResponse)`: Ingests real-time latency and failure telemetry to dynamically trip or reset circuit breakers.

### 2.3 RiskService RPC Details
- `ScreenParty(ScreenPartyRequest) -> (ScreenPartyResponse)`: Dispatches counterparty details to the fuzzy sanctions screening engine. Returns `match_status` (`CLEAN`, `POTENTIAL_MATCH`, `CONFIRMED_SANCTION`) and match score.
- `Evaluate3DSRisk(Evaluate3DSRiskRequest) -> (Evaluate3DSRiskResponse)`: Calculates 3DS risk score and advises frictionless authorization (`transStatus = 'Y'`) or challenge step-up (`transStatus = 'C'`).

---

## 3. High-Throughput Transport Optimizations

### 3.1 Deadlines and Context Propagation
Every gRPC request context propagates distributed trace IDs (`x-trace-id`) and strict timeouts:
```go
ctx, cancel := context.WithTimeout(parentCtx, 800*time.Millisecond)
defer cancel()
```
If an upstream client abandons a connection, downstream gRPC calls are cancelled immediately via context propagation, preventing wasted database and CPU cycles.

### 3.2 Connection Pooling & Keep-Alive Tuning
Internal gRPC client stubs maintain persistent connection pools with tuned HTTP/2 keep-alive parameters:
```properties
grpc.keepalive_time_ms = 10000
grpc.keepalive_timeout_ms = 5000
grpc.keepalive_permit_without_calls = true
grpc.http2.max_pings_without_data = 0
grpc.http2.min_time_between_pings_ms = 5000
```
This guarantees sub-millisecond connection reuse while detecting silent TCP dropouts instantly.
