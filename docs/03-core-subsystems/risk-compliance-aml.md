# Risk, Compliance & Anti-Money Laundering (AML) Subsystem

## 1. Subsystem Overview & Regulatory Mandate

Operating an enterprise-scale payment gateway requires automated, low-latency risk mitigation to satisfy global Anti-Money Laundering (AML), Counter-Financing of Terrorism (CFT), and fraud prevention regulations (e.g., US FinCEN, FATF 40 Recommendations, EU AMLD6, Monetary Authority of Singapore / Bank Indonesia mandates).

The **Risk, Compliance & AML Subsystem** operates inline on the transaction critical path, executing three concurrent layers of defense:
1. **Real-Time Sanctions & PEP Screening**: Pre-flight verification of counterparties against global watchlists with sub-10ms response times.
2. **Dynamic Transaction Monitoring & Velocity Checks**: Detects card testing, rapid-fire micro-transactions, and money laundering structuring (smurfing).
3. **Cardholder 3DS Risk Evaluation**: Scores checkout sessions to intelligently trigger EMV 3DS2 frictionless authorization or challenge step-up.

---

## 2. Real-Time Sanctions Screening Architecture

```
[ Inbound Counterparty Details ] (Sender, Beneficiary, DOB, Country, Address)
              │
              ▼
[ In-Memory Bloom Filter ] ──────────────► Clean? (Negative match in < 0.2ms -> Bypass)
              │ (Potential Match)
              ▼
[ Tokenized Fuzzy String Matching ] ─────► Levenshtein + Jaro-Winkler Token Scoring
              │
              ▼
       [ Match Score ]
              │
    +---------+---------+
    │                   │
    ▼                   ▼
Score < 0.30       0.30 <= Score < 0.85          Score >= 0.85
 [ CLEAN ]        [ POTENTIAL MATCH ]        [ CONFIRMED SANCTION ]
Approved           L2 Compliance Review      Transaction Blocked Instantly
```

### 2.1 Watchlists Ingested & Synced
The screening engine continuously syncs and indexes global sanctions watchlists every 60 minutes:
- **US Treasury OFAC**: Specially Designated Nationals (SDN) and Consolidated Sanctions List.
- **United Nations Security Council (UNSC)**: Consolidated Sanctions List.
- **European Union**: Consolidated Financial Sanctions List (EEAS).
- **Politically Exposed Persons (PEP)**: Heads of state, senior government, military, judicial officials, and immediate family members / close associates (RCA).

### 2.2 Tokenized Fuzzy Matching Algorithms
Because names appear with transliteration variations (e.g. Arabic, Cyrillic, Chinese names translated to Latin script) and typographical errors, exact string matching is inadequate. The engine computes a composite similarity metric:

$$\text{CompositeScore} = w_1 \cdot \text{JaroWinkler}(S_1, S_2) + w_2 \cdot \text{TokenSortRatio}(S_1, S_2) + w_3 \cdot \text{CountryMatch}(C_1, C_2)$$

Where:
- $\text{JaroWinkler}(S_1, S_2)$: Measures character transposition similarity with prefix weighting.
- $\text{TokenSortRatio}(S_1, S_2)$: Tokenizes strings, sorts tokens alphabetically, and calculates Levenshtein edit distance (neutralizing flipped "Given Name / Family Name" orders).
- Weights: $w_1 = 0.50, w_2 = 0.35, w_3 = 0.15$.

---

## 3. Transaction Monitoring & Fraud Prevention Engine

The platform deploys sliding-window streaming analytics (implemented via Redis sorted sets and Apache Flink) to evaluate behavioral signals in real time:

| Fraud Vector | Detection Rule / Pattern | Automated Action |
| :--- | :--- | :--- |
| **Card Testing Attack** | $\ge 5$ authorization attempts with different CVVs or expiry dates on the same IP in 60 seconds. | Temporary IP ban; block card tokens; trigger reCAPTCHA Enterprise. |
| **Structuring / Smurfing** | Single customer initiates multiple transfers just below reporting threshold (e.g. \$9,900 vs \$10,000 limit) within 24 hours. | Flag transaction for SAR (Suspicious Activity Report) filing; place payout on compliance hold. |
| **Velocity Spike** | Merchant transaction volume exceeds 300% of historical rolling 30-day hourly average. | Route payments through mandatory 3DS challenge; notify Risk Operations. |
| **High-Risk Geolocation** | Card issued in Country A, IP address in Country B, shipping to Country C with Tor exit node. | Auto-decline or force biometric challenge step-up. |

---

## 4. EMV 3DS Risk Scoring Rules

For card acquiring, the risk engine calculates an internal risk score from 0 to 100 before dispatching the `AReq` to the Directory Server:

```
[ Checkout Event ] ──► [ Extract Device Fingerprint & BIN Data ]
                              │
                              ▼
                       [ Evaluate Rules ]
                       - Device IP reputation score
                       - Card BIN issuing country vs merchant country
                       - Historical chargeback rate for this PAN
                       - Time of day and transaction basket value
                              │
                              ▼
                     [ 3DS Risk Score (0 - 100) ]
                              │
         +--------------------+--------------------+
         │                                         │
    Score <= 25                               Score > 25
[ Low Risk: Request Frictionless ]       [ Elevated Risk: Force Challenge ]
transStatus = 'Y'                        transStatus = 'C'
Zero friction for cardholder             Cardholder enters OTP
```

---

## 5. Regulatory Compliance & Audit Trail Archiving

Every risk screening result, watch-list snapshot version, fuzzy match score, and human compliance review decision is written to an immutable audit log:
- **Retention Period**: 7 years, complying with FinCEN Bank Secrecy Act (BSA) and FATF standards.
- **Cryptographic Chaining**: Audit logs are hashed into daily Merkle trees anchored to encrypted cloud storage with WORM (Write Once, Read Many) policies enabled, preventing historical tampering.
