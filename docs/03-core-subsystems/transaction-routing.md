# Smart Transaction Routing & High-Availability Rail Selection

## 1. Subsystem Overview & Core Objectives

In an enterprise payment platform processing thousands of transactions per second across dozens of banking partners, static routing creates severe operational liabilities:
- A single partner bank outage can halt an entire payment corridor.
- Interchange fees, network assessments, and switch processing costs vary dynamically by card issuer, geography, and transaction volume tiers.
- Clearing windows and cut-off deadlines dictate whether an instruction should travel over an instant RTGS rail or deferred batch clearing.

The **Smart Routing Engine** dynamically evaluates every inbound payin and outbound remittance to satisfy three concurrent optimization objectives:
1. **Maximized Authorization & Clearing Rate (Availability)**: Steers traffic away from degraded rails.
2. **Minimized Unit Processing Cost (Least-Cost Routing)**: Routes transactions over the lowest-cost capable rail.
3. **Optimized Latency & SLA Fulfillment**: Chooses fastest clearing paths based on real-time circuit health.

---

## 2. Multi-Dimensional Routing Evaluation Algorithm

When an order reaches the routing stage, the engine executes a 4-phase decision pipeline:

```
[ Inbound Transaction ]
           │
           ▼
[ Phase 1: Static Capability Filter ] ────► Eliminates rails lacking required features (e.g. 3DS2, Currency)
           │
           ▼
[ Phase 2: Real-Time Health & Circuit Breaker ] ─► Prunes rails with error rate > 5% or latency > 2,000ms
           │
           ▼
[ Phase 3: Cost-Optimization Scoring Engine ] ──► Calculates effective total processing cost
           │
           ▼
[ Phase 4: Primary Selection & Dynamic Fallback ] ─► Emits Ranked Order: [PrimaryRail, FallbackRail1, FallbackRail2]
```

---

## 3. The 4 Routing Decision Phases

### Phase 1: Static Capability & Compliance Filtering
The engine matches transaction metadata against registered rail descriptors in the **Adapter Capability Registry**:
- **Currency & Corridor Match**: Eliminates rails unable to settle in the requested ISO 4217 currency.
- **Transaction Ceilings**: Prunes rails where $\text{Amount} > \text{MaxPerTransactionLimit}$.
- **Method & Feature Support**: For cards, verifies whether the acquirer supports EMV 3DS 2.2; for payouts, verifies whether the destination bank switch supports instant name inquiry (probe).

### Phase 2: Active Health Probing & Dynamic Circuit Breaking
The engine maintains a real-time health scorecard in Redis for every rail adapter, updated via active synthetic heartbeat probes and passive transaction telemetry:
- **Metrics Tracked** (per 60-second sliding window):
  - Success Rate ($SR$): Ratio of approved/settled transactions to total attempts.
  - Latency ($p95$): 95th percentile response latency in milliseconds.
  - Concurrency Load: Number of currently in-flight requests.
- **Circuit Breaker States**:
  - `CLOSED (Healthy)`: $SR \ge 95\%$, $p95 < 2,000\text{ms}$. Traffic flows normally.
  - `OPEN (Tripped)`: $SR < 95\%$ or consecutive timeouts $\ge 5$. The rail is immediately excluded from routing; 100% of traffic is redirected to secondary fallbacks.
  - `HALF-OPEN (Canary Testing)`: After 30 seconds in `OPEN`, the gateway routes 2% canary traffic to test if the partner switch has recovered. If canaries succeed, the circuit resets to `CLOSED`.

### Phase 3: Cost-Optimization & Interchange Scoring
For eligible and healthy rails, the engine evaluates the total transaction fee:

$$\text{EffectiveCost} = \text{FixedSwitchFee} + (\text{GrossAmount} \times \text{VariableBps}) + \text{InterchangeAssessment}$$

#### Example: Domestic Card Payin Selection ($100.00 USD Transaction)
- **Acquirer A (Global Processor)**: Fixed fee \$0.10 + 1.80% = \$1.90 total fee.
- **Acquirer B (Direct Local Bank)**: Fixed fee \$0.25 + 1.20% = \$1.45 total fee.
- *Decision*: Acquirer B is selected, delivering an immediate **23.6% cost saving** per transaction.

### Phase 4: Primary Selection & Fallback Cascading
The engine emits a prioritized list of routes:
```json
{
  "selected_primary_rail": "ACQUIRER_LOCAL_DIRECT",
  "fallback_cascade": [
    { "rail_code": "ACQUIRER_GLOBAL_BACKUP", "priority": 1, "trigger": "TIMEOUT_OR_5XX" },
    { "rail_code": "ACQUIRER_FAILOVER_NET", "priority": 2, "trigger": "SYSTEM_DOWN" }
  ]
}
```
If the primary rail returns a retryable error (e.g. HTTP 504 Gateway Timeout, ISO 8583 response `91 - System Error`), the workflow worker automatically retries against `FallbackRail1` without asking the client to re-enter payment credentials.

---

## 4. Routing Rule Configuration & Merchant Overrides

Merchants can configure routing priority policies through the API:
1. **Least-Cost Routing (LCR)**: Minimizes financial processing expenses (default).
2. **High-Availability (Performance) Priority**: Directs traffic strictly to the rail with the highest historical success rate, disregarding minor cost differences (ideal for flash-sale merchants).
3. **Volume Quota Balancing (Weighted Traffic Split)**: Distributes traffic across multiple bank acquirers to satisfy minimum contractual volume commitments (e.g., 60% Bank Alpha, 40% Bank Beta).
