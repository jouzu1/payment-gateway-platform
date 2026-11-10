# Enterprise System Topology & Network Architecture

## 1. Executive Summary & Architectural Invariants

The next-generation Payment Gateway Platform is engineered to deliver Tier-1 financial infrastructure reliability, supporting mission-critical payment acquiring (Payin), domestic instant remittance (Local Payout), cross-border multi-currency remittance (FX Payout), and P2P lending regulatory escrow operations. 

The platform guarantees:
- **Zero Single Point of Failure (SPOF)**: Fully redundant physical, network, compute, database, and event-streaming components across a minimum of three Availability Zones (Multi-AZ 3-AZ Active-Active).
- **Sub-50ms Processing Latency**: Real-time ingress, tokenization, risk evaluation, and double-entry ledger allocation operating with baseline targets of $p50 < 20\text{ms}$, $p95 < 50\text{ms}$, and $p99 < 100\text{ms}$.
- **Strict Financial Isolation**: Clear zoning between public internet edge, containerized application clusters, isolated PCI-DSS Level 1 cardholder data vaults, internal banking ledger networks, and dedicated partner bank interconnects.

---

## 2. Multi-Tier System Topology

```
+--------------------------------------------------------------------------------------------------+
|                                  ZONE 0: PUBLIC CLIENT EDGE                                      |
|   Merchant Web / Mobile App       Cardholder Browser          Partner Webhook Consumer           |
+-------------------------------------------------+------------------------------------------------+
                                                  | TLS 1.3 / mTLS / Anycast DNS
                                                  v
+--------------------------------------------------------------------------------------------------+
|                       ZONE 1: PERIMETER SECURITY & EDGE INGRESS (DMZ)                            |
|  - AWS Route 53 Application Recovery Controller (ARC) + AWS Shield Advanced (DDoS Protection)   |
|  - AWS WAF v2 (Layer 7 Inspection, SQLi / XSS filtering, Rate Limiting, Geo-IP Fencing)         |
|  - AWS Network Load Balancers (L4 Dual-Stack, Cross-Zone Enabled across 3 AZs: AZ-A, AZ-B, AZ-C)|
|  - Envoy / Kong API Gateway Mesh (mTLS termination, JWT validation, Distributed Idempotency)    |
+-------------------------------------------------+------------------------------------------------+
                                                  | Internal VPC Peering / Private Subnets
                                                  v
+--------------------------------------------------------------------------------------------------+
|                        ZONE 2: APPLICATION & PRODUCT ORCHESTRATION                               |
|  Kubernetes (EKS) Multi-AZ Compute Cluster (Graviton ARM64 Nodes managed via Karpenter)          |
|  +---------------------------+  +---------------------------+  +-------------------------------+ |
|  |  Local Remittance Engine  |  | Cross-Border FX Engine    |  | Payin Acquiring Engine        | |
|  +---------------------------+  +---------------------------+  +-------------------------------+ |
|  +---------------------------+  +---------------------------+  +-------------------------------+ |
|  |  P2P Lending Escrow WF    |  | Smart Routing Engine      |  | Real-Time AML/Sanctions Svc   | |
|  +---------------------------+  +---------------------------+  +-------------------------------+ |
+-------------------------------------------------+------------------------------------------------+
                                                  | gRPC / Internal Service Mesh (Linkerd)
                                                  v
+--------------------------------------------------------------------------------------------------+
|                        ZONE 3: SECURE TRANSACTION CORE & DATA PERSISTENCE                        |
|  +---------------------------------------------------------------------------------------------+ |
|  | Immutable Double-Entry Ledger Subsystem (LMAX Disruptor In-Memory Core + Batched NVMe WAL)  | |
|  +---------------------------------------------------------------------------------------------+ |
|  - Primary Relational Cluster: AWS Aurora PostgreSQL Multi-AZ (Sync 6-way storage across 3 AZs)|
|  - Distributed Cache Tier: Redis Cluster Multi-AZ (3 Masters, 3 Replicas in distinct AZs)       |
|  - Durable Event Log: Apache Kafka Cluster (3 AZs, RF=3, min.insync.replicas=2, acks=all)        |
+-------------------------------------------------+------------------------------------------------+
                                                  | Dedicated DX / IPSec VPN / VPC Endpoints
                                                  v
+--------------------------------------------------------------------------------------------------+
|                     ZONE 4: EXTERNAL FINANCIAL CLEARING & PARTNER SWITCHES                       |
|  - Card Payment Networks: Visa Direct, Mastercard Send, CyberSource                             |
|  - Domestic Clearing Switches: FedNow (US), BI-FAST (ID), UPI / IMPS (IN), Faster Payments (UK)  |
|  - Cross-Border Correspondent Networks: SWIFT Alliance Lite2, ISO 20022 Partner Banks           |
|  - Regulated Custodian Banks: Tripartite FBO Escrow Trust Accounts for P2P Lending              |
+--------------------------------------------------------------------------------------------------+
```

---

## 3. Network Topology & Boundary Isolation

### 3.1 VPC Subnet Hierarchy & Failure Domain Segmentation
The physical infrastructure is partitioned into discrete Virtual Private Clouds (VPCs) across three Availability Zones (`AZ-A`, `AZ-B`, `AZ-C`):

1. **Public Subnets (DMZ)**:
   - Contains only AWS Network Load Balancers (NLBs) with Elastic IPs.
   - Strictly no application containers or databases reside in public subnets.
   - Dedicated NAT Gateways are provisioned **per AZ** (`NAT-GW-A`, `NAT-GW-B`, `NAT-GW-C`). An outage in AZ-A does not impair outbound internet routing for workloads in AZ-B or AZ-C.

2. **Private Application Subnets**:
   - Hosts the Kubernetes worker nodes executing stateless microservices.
   - Ingress is permitted solely from Zone 1 Envoy gateways via TLS 1.3 mutual authentication (mTLS).
   - Egress to third-party APIs (e.g. Card Acquirers, Cloudflare) routes strictly through the AZ-local NAT Gateway or AWS PrivateLink interface endpoints.

3. **Secure Data Subnets (Isolated)**:
   - Non-routable subnets containing Aurora PostgreSQL database instances, Redis nodes, and Kafka brokers.
   - Outbound internet access is disabled at the route table level.
   - Access is restricted via strict Security Group rules allowing traffic only on specific ports (`5432` for PostgreSQL, `6379` for Redis, `9092` for Kafka) originated exclusively from Zone 2 application security groups.

4. **PCI-DSS Level 1 Cardholder Data Environment (CDE VPC)**:
   - Physically isolated VPC connected to the main application VPC via AWS Transit Gateway with stateful Network Firewall inspection.
   - Hosts the Tokenization Vault and HSM-backed key management services (AWS CloudHSM).
   - Card Primary Account Numbers (PAN) and Card Verification Values (CVV) are ingested directly into the CDE from the client browser through iframe/SDK integration and exchanged for non-reversible tokens (`PaymentToken`). Unencrypted PANs never enter Zone 2 or Zone 3 networks.

5. **Direct Connect & Partner Interconnect VPC**:
   - Houses redundant 10 Gbps AWS Direct Connect (DX) cross-connects terminating at carrier-neutral facilities (Equinix, Global Switch), supplemented by automated BGP failover to redundant IPSec VPN tunnels.
   - Directly interfaces with central bank instant switches (e.g., FedNow router, BI-FAST Gateway) using ISO 20022 protocols over dedicated leased lines.

---

## 4. Ingress, Security, and Session Termination

### 4.1 Transport Layer Security & Protocol Enforcement
- **Public Ingress**: Enforces TLS 1.3 exclusively with cipher suites supporting Perfect Forward Secrecy (PFS), such as `TLS_AES_256_GCM_SHA384` and `TLS_CHACHA20_POLY1305_SHA256`. TLS 1.0, 1.1, and 1.2 are rejected at the edge.
- **Mutual TLS (mTLS)**: Mandatory for all B2B enterprise clients calling high-value remittance endpoints (`/v1/payouts`, `/v1/ledger/journals`) and all internal service-to-service RPC communications managed by the Linkerd service mesh.
- **HMAC Request Signature Verification**: Every mutating API request requires an `X-Signature` header computed as:
  $$\text{Signature} = \text{HMAC-SHA256}(K_{secret}, \text{Timestamp} + "." + \text{Payload})$$
  Timestamps older than 300 seconds are immediately rejected to neutralize replay attacks.

### 4.2 Distributed Idempotency Termination
To guarantee that network retries or client double-submits never execute duplicate ledger postings or duplicate bank transfers:
1. The Envoy API Gateway extracts the `Idempotency-Key` header (UUIDv4).
2. It executes an atomic `SETNX` reservation in the multi-AZ Redis cluster with a 60-second in-flight TTL.
3. If the key exists with state `COMMITTED`, the cached response payload is served directly with zero downstream execution.
4. If the key exists with state `IN_FLIGHT`, the gateway responds with `409 Conflict`.
5. Upon successful downstream processing, the database transaction updates the idempotency record to `COMMITTED` alongside the committed ledger entries.

---

## 5. Tier-by-Tier Component Failure Isolation

| Tier | Component | Redundancy Mechanism | Failure Mode & Automated Recovery |
| :--- | :--- | :--- | :--- |
| **Ingress** | AWS NLB + Envoy | Multi-AZ Cross-Zone Routing | Sub-second TCP route failover if an individual Envoy pod or AZ fails. |
| **Compute** | EKS on Graviton3/4 | `topologySpreadConstraints` across 3 AZs | Kubernetes control plane evicts pods on failed nodes; Karpenter provisions replacement capacity in healthy AZs in < 30s. |
| **Cache** | Redis Cluster | 3 Masters + 3 Replicas (1 master/replica pair per AZ) | Redis Sentinel / Cluster protocol promotes replica to master in < 2s upon node failure. |
| **Streaming** | Apache Kafka | 3 Brokers, $RF=3$, `min.insync.replicas=2` | Automated leader rebalancing; writes continue without data loss or downtime if one broker halts. |
| **Relational DB** | AWS Aurora PostgreSQL | Synchronous replication across 6 storage nodes in 3 AZs | Storage volume tolerates loss of 2 storage copies without write disruption and 3 copies without read disruption. Automatic DB instance failover in < 15s. |
| **Ledger Engine** | LMAX Disruptor Single-Writer | Hot-Standby In-Memory Mirroring | Raft-replicated memory states; hot-standby assumes journal sequencing instantly if primary process halts. |
| **Network Rails** | Domestic / SWIFT Rails | Multi-Rail Active-Standby with Circuit Breakers | Automatic failover to secondary acquirer / correspondent routing table when error rate breaches 5%. |
