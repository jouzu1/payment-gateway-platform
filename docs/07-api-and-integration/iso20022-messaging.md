# ISO 20022 Banking Messaging Architecture & Field Mappings

## 1. Domain Overview & Regulatory Mandate

ISO 20022 is the universal financial industry messaging standard governing modern domestic instant payment rails (e.g. FedNow, BI-FAST, UK Faster Payments, SEPA) and international cross-border banking networks (SWIFT CBPR+).

The gateway uses ISO 20022 XML schemas as the native lingua franca for all banking switches and correspondent bank integrations.

---

## 2. Core ISO 20022 Message Catalog

```
+--------------------------------------------------------------------------------------------------+
|                                    ISO 20022 MESSAGE CATALOG                                     |
|                                                                                                  |
|   1. pain.001 (Payments Initiation)                                                              |
|      - CustomerCreditTransferInitiation: Batch or single instruction from merchant/gateway.      |
|                                                                                                  |
|   2. pacs.008 (Payments Clearing & Settlement)                                                   |
|      - FinancialInstitutionalCustomerCreditTransfer: Interbank real-time credit instruction.     |
|                                                                                                  |
|   3. pacs.002 (Payment Status Report)                                                            |
|      - PaymentStatusReport: Confirmation or rejection of credit transfer.                       |
|                                                                                                  |
|   4. camt.053 (Cash Management - Statement)                                                     |
|      - BankToCustomerStatement: End-of-day electronic statement for 3-way reconciliation.        |
+--------------------------------------------------------------------------------------------------+
```

---

## 3. Detailed Message Specifications & Field Mapping Tables

### 3.1 `pain.001.001.09` (Payment Initiation)

Used to transmit batch payout instructions to commercial partner banks.

#### XML Payload Snippet:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.09">
  <CstmrCdtTrfInitn>
    <GrpHdr>
      <MsgId>MSG-20261110-8912</MsgId>
      <CreDtTm>2026-11-10T10:30:00Z</CreDtTm>
      <NbOfTxs>1</NbOfTxs>
      <InitgPty>
        <Nm>Enterprise Payment Gateway Ltd</Nm>
      </InitgPty>
    </GrpHdr>
    <PmtInf>
      <PmtInfId>PMTINF-BATCH-001</PmtInfId>
      <PmtMtd>TRF</PmtMtd>
      <ReqdExctnDt>
        <Dt>2026-11-10</Dt>
      </ReqdExctnDt>
      <Dbtr>
        <Nm>Merchant Operational Corp</Nm>
      </Dbtr>
      <DbtrAcct>
        <Id>
          <Othr><Id>8821938102</Id></Othr>
        </Id>
      </DbtrAcct>
      <DbtrAgt>
        <FinInstnId>
          <BICFI>CHASUS33XXX</BICFI>
        </FinInstnId>
      </DbtrAgt>
      <CdtTrfTxInf>
        <PmtId>
          <EndToEndId>E2E-20261110-001</EndToEndId>
          <UETR>c1b89a01-4b12-4f88-912a-89a12bc49012</UETR>
        </PmtId>
        <Amt>
          <InstdAmt Ccy="USD">5000.00</InstdAmt>
        </Amt>
        <CdtrAgt>
          <FinInstnId>
            <BICFI>BOFAUS3NXXX</BICFI>
          </FinInstnId>
        </CdtrAgt>
        <Cdtr>
          <Nm>Jane Doe</Nm>
        </Cdtr>
        <CdtrAcct>
          <Id>
            <Othr><Id>9821039128</Id></Othr>
          </Id>
        </CdtrAcct>
        <RmtInf>
          <Ustrd>Invoice #8921 Settlement</Ustrd>
        </RmtInf>
      </CdtTrfTxInf>
    </PmtInf>
  </CstmrCdtTrfInitn>
</Document>
```

#### Field Mapping Table:
| ISO 20022 XML XPath | Platform Entity Field | Description |
| :--- | :--- | :--- |
| `GrpHdr/MsgId` | `settlement_batches.id` | Unique batch file tracking message identifier. |
| `PmtInf/Dbtr/Nm` | `merchants.legal_name` | Legal name of originating merchant debtor. |
| `CdtTrfTxInf/PmtId/EndToEndId` | `payout_orders.end_to_end_id` | Unalterable tracking ID passed through central switch. |
| `CdtTrfTxInf/PmtId/UETR` | `payout_orders.id` (UUIDv4) | SWIFT Unique End-to-end Transaction Reference. |
| `CdtTrfTxInf/Amt/InstdAmt` | `payout_orders.source_amount` | Payment amount in instructed currency. |
| `CdtTrfTxInf/Cdtr/Nm` | `payout_orders.beneficiary_account_name`| Registered counterparty legal name. |
| `CdtTrfTxInf/CdtrAcct/Id/Othr/Id`| `payout_orders.beneficiary_account_number`| Destination bank account number. |

---

### 3.2 `pacs.008.001.08` (Real-Time Credit Transfer)

Dispatched directly across instant switches (FedNow, BI-FAST, UK Faster Payments):

```xml
<pacs.008.001.08>
  <GrpHdr>
    <MsgId>FEDNOW-20261110-0981</MsgId>
    <CreDtTm>2026-11-10T10:30:15Z</CreDtTm>
    <NbOfTxs>1</NbOfTxs>
    <SttlmInf>
      <SttlmMtd>CLRG</SttlmMtd>
    </SttlmInf>
  </GrpHdr>
  <CdtTrfTxInf>
    <PmtId>
      <EndToEndId>E2E-20261110-001</EndToEndId>
      <TxId>TX-FEDNOW-01</TxId>
    </PmtId>
    <IntrBkSttlmAmt Ccy="USD">5000.00</IntrBkSttlmAmt>
    <ChrgBr>SLEV</ChrgBr>
    <Dbtr><Nm>Merchant Operational Corp</Nm></Dbtr>
    <Cdtr><Nm>Jane Doe</Nm></Cdtr>
  </CdtTrfTxInf>
</pacs.008.001.08>
```

---

### 3.3 `pacs.002.001.10` (Payment Status Report & Clearing Codes)

The central switch confirms clearing using standardized status codes:

| `TxSts` Code | Name | Semantic Meaning | Platform Action |
| :--- | :--- | :--- | :--- |
| `ACCP` | Accepted Customer Profile | Transfer cleared and irrevocably settled. | State set to `SETTLED`; ledger hold committed. |
| `ACSP` | Accepted Settlement In Process | Credit transfer validated; settlement pending. | State remains `DISPATCHED_TO_RAIL`. |
| `RJCT` | Rejected | Clearing switch declined the instruction. | State set to `FAILED`; ledger hold released. |
| `PDNG` | Pending | Transfer queued in central bank clearing house. | Retain pending state; await webhook/polling. |
