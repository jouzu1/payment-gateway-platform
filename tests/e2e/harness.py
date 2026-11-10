"""
E2E Testing Domain Simulator & Verification Harness.
Provides deterministic, requirement-driven domain models, accounting oracles,
state machines, and simulation engines for all 25 Payment Gateway Platform features.
"""

from __future__ import annotations
import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any


# ==============================================================================
# 1. CORE ENUMS & CONSTANTS
# ==============================================================================

class EntryDirection(str, Enum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class AccountType(str, Enum):
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    EXPENSE = "EXPENSE"


class TransactionStatus(str, Enum):
    INITIATED = "INITIATED"
    AUTHENTICATED = "AUTHENTICATED"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    CLEARED = "CLEARED"
    SETTLED = "SETTLED"
    REVERSED = "REVERSED"
    FAILED = "FAILED"
    HELD_FOR_REVIEW = "HELD_FOR_REVIEW"


class IdempotencyState(str, Enum):
    STARTED = "STARTED"
    IN_FLIGHT = "IN_FLIGHT"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"


class SagaState(str, Enum):
    INIT = "INIT"
    RESERVING = "RESERVING"
    DISPATCHING = "DISPATCHING"
    COMPLETED = "COMPLETED"
    COMPENSATING = "COMPENSATING"
    COMPENSATED = "COMPENSATED"
    FAILED = "FAILED"


# ==============================================================================
# 2. IMMUTABLE DOUBLE-ENTRY LEDGER SUBSYSTEM (F09, F10)
# ==============================================================================

@dataclass(frozen=True)
class EntryLeg:
    account_id: str
    amount: int  # Unsigned minor units (e.g. cents)
    direction: EntryDirection
    currency: str


@dataclass(frozen=True)
class JournalEntry:
    entry_id: str
    transaction_id: str
    timestamp: float
    description: str
    legs: Tuple[EntryLeg, ...]


@dataclass
class LedgerAccount:
    account_id: str
    name: str
    account_type: AccountType
    currency: str
    debit_balance: int = 0
    credit_balance: int = 0
    held_balance: int = 0

    @property
    def net_balance(self) -> int:
        """Net balance based on normal account balance rules."""
        if self.account_type in (AccountType.ASSET, AccountType.EXPENSE):
            return self.debit_balance - self.credit_balance
        else:
            return self.credit_balance - self.debit_balance

    @property
    def available_balance(self) -> int:
        return self.net_balance - self.held_balance


class LedgerEngine:
    """
    Enforces double-entry financial accounting:
    1. Assets = Liabilities + Equity
    2. Sum of Debits == Sum of Credits (Zero-sum journal)
    3. Unsigned 64-bit integer minor unit representation
    4. Immutable append-only journal entries
    """
    def __init__(self):
        self.accounts: Dict[str, LedgerAccount] = {}
        self.journal: List[JournalEntry] = []
        self.holds: Dict[str, Tuple[str, int, str]] = {}  # hold_id -> (account_id, amount, currency)

    def create_account(self, account_id: str, name: str, account_type: AccountType, currency: str) -> LedgerAccount:
        if account_id in self.accounts:
            raise ValueError(f"Account {account_id} already exists")
        acc = LedgerAccount(account_id=account_id, name=name, account_type=account_type, currency=currency)
        self.accounts[account_id] = acc
        return acc

    def post_journal_entry(self, transaction_id: str, legs: List[EntryLeg], description: str) -> JournalEntry:
        if not legs or len(legs) < 2:
            raise ValueError("A journal entry must contain at least 2 entry legs")

        # Currency validation & Zero-sum validation per currency
        currency_debits: Dict[str, int] = {}
        currency_credits: Dict[str, int] = {}

        for leg in legs:
            if leg.amount <= 0:
                raise ValueError(f"Entry amount must be strictly positive integer: got {leg.amount}")
            if leg.account_id not in self.accounts:
                raise ValueError(f"Account {leg.account_id} does not exist in ledger")
            acc = self.accounts[leg.account_id]
            if acc.currency != leg.currency:
                raise ValueError(f"Currency mismatch for account {acc.account_id}: expected {acc.currency}, got {leg.currency}")

            if leg.direction == EntryDirection.DEBIT:
                currency_debits[leg.currency] = currency_debits.get(leg.currency, 0) + leg.amount
            else:
                currency_credits[leg.currency] = currency_credits.get(leg.currency, 0) + leg.amount

        # Check that for every currency in this journal, debits == credits
        all_currencies = set(currency_debits.keys()) | set(currency_credits.keys())
        for c in all_currencies:
            d = currency_debits.get(c, 0)
            cr = currency_credits.get(c, 0)
            if d != cr:
                raise ValueError(f"Unbalanced journal entry for currency {c}: Debits={d}, Credits={cr}, Diff={d - cr}")

        # Post legs into accounts
        for leg in legs:
            acc = self.accounts[leg.account_id]
            if leg.direction == EntryDirection.DEBIT:
                acc.debit_balance += leg.amount
            else:
                acc.credit_balance += leg.amount

        entry = JournalEntry(
            entry_id=str(uuid.uuid4()),
            transaction_id=transaction_id,
            timestamp=time.time(),
            description=description,
            legs=tuple(legs)
        )
        self.journal.append(entry)
        return entry

    def hold_funds(self, hold_id: str, account_id: str, amount: int, currency: str) -> None:
        if account_id not in self.accounts:
            raise ValueError(f"Account {account_id} not found")
        acc = self.accounts[account_id]
        if acc.currency != currency:
            raise ValueError("Currency mismatch")
        if acc.available_balance < amount:
            raise ValueError(f"Insufficient available funds for hold: required {amount}, available {acc.available_balance}")
        acc.held_balance += amount
        self.holds[hold_id] = (account_id, amount, currency)

    def release_hold(self, hold_id: str) -> None:
        if hold_id not in self.holds:
            raise ValueError(f"Hold {hold_id} not found")
        account_id, amount, _ = self.holds.pop(hold_id)
        self.accounts[account_id].held_balance -= amount

    def commit_hold(self, hold_id: str, destination_account_id: str, transaction_id: str) -> JournalEntry:
        if hold_id not in self.holds:
            raise ValueError(f"Hold {hold_id} not found")
        src_account_id, amount, currency = self.holds.pop(hold_id)
        src_acc = self.accounts[src_account_id]
        src_acc.held_balance -= amount

        # Execute journal transfer
        legs = [
            EntryLeg(account_id=src_account_id, amount=amount, direction=EntryDirection.DEBIT, currency=currency),
            EntryLeg(account_id=destination_account_id, amount=amount, direction=EntryDirection.CREDIT, currency=currency)
        ]
        return self.post_journal_entry(transaction_id, legs, f"Commit hold {hold_id}")

    def verify_accounting_equation(self, currency: str) -> Tuple[bool, int, int]:
        """
        Verifies Assets = Liabilities + Equity (+ Revenue - Expense)
        Returns (is_balanced, total_assets, total_liabilities_equity)
        """
        assets = sum(a.net_balance for a in self.accounts.values() if a.currency == currency and a.account_type == AccountType.ASSET)
        liabilities = sum(a.net_balance for a in self.accounts.values() if a.currency == currency and a.account_type == AccountType.LIABILITY)
        equity = sum(a.net_balance for a in self.accounts.values() if a.currency == currency and a.account_type == AccountType.EQUITY)
        revenue = sum(a.net_balance for a in self.accounts.values() if a.currency == currency and a.account_type == AccountType.REVENUE)
        expenses = sum(a.net_balance for a in self.accounts.values() if a.currency == currency and a.account_type == AccountType.EXPENSE)

        rhs = liabilities + equity + (revenue - expenses)
        return (assets == rhs, assets, rhs)


# ==============================================================================
# 3. MATHEMATICAL WATERFALL SPLIT ENGINE (F07)
# ==============================================================================

@dataclass(frozen=True)
class InvestorShare:
    investor_id: str
    allocation_weight_bps: int  # Basis points (100 bps = 1%)


@dataclass
class WaterfallResult:
    collected_amount: int
    platform_fee: int
    withholding_tax: int
    overdue_penalty: int
    interest_paid: int
    principal_paid: int
    investor_payouts: Dict[str, int]
    rounding_remainder: int

    @property
    def total_distributed(self) -> int:
        return (self.platform_fee + self.withholding_tax + self.overdue_penalty +
                sum(self.investor_payouts.values()))


class WaterfallEngine:
    """
    Executes financial waterfall split algorithms for P2P lending:
    Strict precedence: Penalty -> Tax -> Fee -> Interest -> Principal
    Ensures zero penny leaks using Banker's Rounding and remainder cent allocation.
    """
    @staticmethod
    def calculate_split(
        collected_amount: int,
        principal_due: int,
        interest_due: int,
        penalty_due: int,
        platform_fee_bps: int,  # e.g. 50 = 0.5%
        tax_rate_bps: int,       # e.g. 1500 = 15% on interest
        investors: List[InvestorShare]
    ) -> WaterfallResult:
        if collected_amount <= 0:
            raise ValueError("Collected repayment amount must be positive")
        if not investors:
            raise ValueError("At least one investor required for waterfall distribution")

        total_bps = sum(inv.allocation_weight_bps for inv in investors)
        if total_bps != 10000:
            raise ValueError(f"Investor allocation basis points must sum to 10000 (100%), got {total_bps}")

        remaining = collected_amount

        # 1. Deduct overdue penalty first
        penalty_paid = min(remaining, penalty_due)
        remaining -= penalty_paid

        # 2. Accrued interest
        interest_paid = min(remaining, interest_due)
        remaining -= interest_paid

        # 3. Withholding tax on interest (15%)
        tax_amount = round((interest_paid * tax_rate_bps) / 10000)
        remaining_interest_for_investors = interest_paid - tax_amount

        # 4. Platform servicing fee from remaining
        platform_fee = round((collected_amount * platform_fee_bps) / 10000)

        # 5. Principal paid
        principal_paid = min(remaining, principal_due)
        remaining -= principal_paid

        # Total net distributable to investors = (interest - tax) + principal - platform_fee
        net_distributable = remaining_interest_for_investors + principal_paid - platform_fee

        # Allocate fractionally to investors
        payouts: Dict[str, int] = {}
        allocated_so_far = 0

        for inv in investors:
            share = round((net_distributable * inv.allocation_weight_bps) / 10000)
            payouts[inv.investor_id] = share
            allocated_so_far += share

        # Remainder cent allocation to first investor (deterministic tie-breaking)
        remainder = net_distributable - allocated_so_far
        first_investor_id = investors[0].investor_id
        payouts[first_investor_id] += remainder

        return WaterfallResult(
            collected_amount=collected_amount,
            platform_fee=platform_fee,
            withholding_tax=tax_amount,
            overdue_penalty=penalty_paid,
            interest_paid=interest_paid,
            principal_paid=principal_paid,
            investor_payouts=payouts,
            rounding_remainder=remainder
        )


# ==============================================================================
# 4. FX QUOTE & CROSS-BORDER ENGINE (F04)
# ==============================================================================

@dataclass
class GuaranteedFXQuote:
    quote_id: str
    source_currency: str
    target_currency: str
    rate: int        # Rate scaled by 10^scale (e.g. 15,500 IDR/USD with scale 4 = 155000000)
    rate_scale: int  # Decimal power
    ttl_seconds: int
    created_at: float

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl_seconds

    def convert(self, source_units: int) -> int:
        if self.is_expired:
            raise ValueError(f"FX Quote {self.quote_id} has expired")
        # Target minor units using integer banker's rounding
        return round((source_units * self.rate) / (10 ** self.rate_scale))


class FXEngine:
    def __init__(self):
        self.quotes: Dict[str, GuaranteedFXQuote] = {}

    def lock_quote(self, src: str, tgt: str, rate: int, scale: int = 4, ttl: int = 60) -> GuaranteedFXQuote:
        quote = GuaranteedFXQuote(
            quote_id=str(uuid.uuid4()),
            source_currency=src,
            target_currency=tgt,
            rate=rate,
            rate_scale=scale,
            ttl_seconds=ttl,
            created_at=time.time()
        )
        self.quotes[quote.quote_id] = quote
        return quote


# ==============================================================================
# 5. RISK, FRAUD & AML SANCTIONS ENGINE (F12)
# ==============================================================================

class SanctionsMatchStatus(str, Enum):
    CLEAN = "CLEAN"
    SANCTIONS_HIT = "SANCTIONS_HIT"
    PEP_HIT = "PEP_HIT"


class SanctionsEngine:
    """Real-time OFAC/UN/PEP sanctions screening."""
    def __init__(self):
        self.sdn_list = {
            "OSAMA BIN LADEN", "VLADIMIR PUTIN", "KIM JONG UN", "CARTEL DE SINALOA",
            "TEST SANCTIONED ENTITY", "NORTH KOREA EMBASSY"
        }
        self.pep_list = {
            "JOHN DOE SENATOR", "MINISTER OF FINANCE", "PRESIDENTIAL AIDE"
        }

    def screen_entity(self, name: str, country_code: str) -> Tuple[SanctionsMatchStatus, str]:
        normalized = name.strip().upper()
        if country_code.upper() in ("IR", "KP", "CU", "SY"):
            return (SanctionsMatchStatus.SANCTIONS_HIT, f"Country embargo: {country_code}")
        if normalized in self.sdn_list:
            return (SanctionsMatchStatus.SANCTIONS_HIT, f"Exact match on OFAC SDN: {normalized}")
        for sdn in self.sdn_list:
            if sdn in normalized or normalized in sdn:
                return (SanctionsMatchStatus.SANCTIONS_HIT, f"Fuzzy match on OFAC SDN: {sdn}")
        if normalized in self.pep_list:
            return (SanctionsMatchStatus.PEP_HIT, f"Match on PEP register: {normalized}")
        return (SanctionsMatchStatus.CLEAN, "Clear")


# ==============================================================================
# 6. DISTRIBUTED IDEMPOTENCY FRAMEWORK (F17)
# ==============================================================================

@dataclass
class IdempotencyRecord:
    key: str
    state: IdempotencyState
    request_hash: str
    response_body: Optional[Dict[str, Any]] = None
    http_status: Optional[int] = None
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 86400)


class IdempotencyEngine:
    def __init__(self):
        self.records: Dict[str, IdempotencyRecord] = {}

    def begin_transaction(self, key: str, payload: Dict[str, Any]) -> Tuple[bool, Optional[IdempotencyRecord]]:
        """
        Returns (is_new, record).
        Raises ValueError on conflict or mismatch.
        """
        payload_hash = hashlib.sha256(str(sorted(payload.items())).encode()).hexdigest()
        now = time.time()

        if key in self.records:
            rec = self.records[key]
            if now > rec.expires_at:
                # Expired key can be reused
                del self.records[key]
            else:
                if rec.request_hash != payload_hash:
                    raise ValueError(f"Idempotency key '{key}' payload mismatch (422 Unprocessable)")
                if rec.state == IdempotencyState.IN_FLIGHT:
                    raise ValueError(f"Idempotency key '{key}' currently in-flight (409 Conflict)")
                return (False, rec)

        rec = IdempotencyRecord(key=key, state=IdempotencyState.IN_FLIGHT, request_hash=payload_hash)
        self.records[key] = rec
        return (True, rec)

    def commit_transaction(self, key: str, response: Dict[str, Any], status_code: int = 200) -> None:
        if key not in self.records:
            raise ValueError(f"Record for key {key} not found")
        rec = self.records[key]
        rec.state = IdempotencyState.COMMITTED
        rec.response_body = response
        rec.http_status = status_code


# ==============================================================================
# 7. SAGA ORCHESTRATOR & OUTBOX (F18)
# ==============================================================================

@dataclass
class SagaStep:
    name: str
    executed: bool = False
    compensated: bool = False


class SagaOrchestrator:
    def __init__(self, saga_id: str):
        self.saga_id = saga_id
        self.state = SagaState.INIT
        self.steps: List[SagaStep] = []
        self.outbox_events: List[Dict[str, Any]] = []

    def add_step(self, name: str) -> None:
        self.steps.append(SagaStep(name=name))

    def execute_forward(self, fail_at_step: Optional[str] = None) -> bool:
        self.state = SagaState.DISPATCHING
        for step in self.steps:
            if fail_at_step and step.name == fail_at_step:
                self.compensate()
                return False
            step.executed = True
            self.outbox_events.append({
                "saga_id": self.saga_id,
                "event": f"{step.name}_COMPLETED",
                "timestamp": time.time()
            })
        self.state = SagaState.COMPLETED
        return True

    def compensate(self) -> None:
        self.state = SagaState.COMPENSATING
        for step in reversed(self.steps):
            if step.executed:
                step.compensated = True
                self.outbox_events.append({
                    "saga_id": self.saga_id,
                    "event": f"{step.name}_COMPENSATED",
                    "timestamp": time.time()
                })
        self.state = SagaState.COMPENSATED


# ==============================================================================
# 8. AUTOMATED 3-WAY RECONCILIATION ENGINE (F19)
# ==============================================================================

class ReconMatchStatus(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    UNMATCHED_INTERNAL = "UNMATCHED_INTERNAL"
    UNMATCHED_EXTERNAL = "UNMATCHED_EXTERNAL"
    AMOUNT_DISCREPANCY = "AMOUNT_DISCREPANCY"


class ReconciliationEngine:
    """Matches Internal Ledger vs Gateway Transaction vs External Bank Statement."""
    @staticmethod
    def match_records(
        internal_records: Dict[str, int],  # ref -> amount
        gateway_records: Dict[str, int],   # ref -> amount
        bank_records: Dict[str, int]       # ref -> amount
    ) -> Dict[str, ReconMatchStatus]:
        all_refs = set(internal_records.keys()) | set(gateway_records.keys()) | set(bank_records.keys())
        results: Dict[str, ReconMatchStatus] = {}

        for ref in all_refs:
            in_int = ref in internal_records
            in_gtw = ref in gateway_records
            in_bnk = ref in bank_records

            if in_int and in_gtw and in_bnk:
                a_int = internal_records[ref]
                a_gtw = gateway_records[ref]
                a_bnk = bank_records[ref]
                if a_int == a_gtw == a_bnk:
                    results[ref] = ReconMatchStatus.EXACT_MATCH
                else:
                    results[ref] = ReconMatchStatus.AMOUNT_DISCREPANCY
            elif in_int and not in_bnk:
                results[ref] = ReconMatchStatus.UNMATCHED_INTERNAL
            elif in_bnk and not in_int:
                results[ref] = ReconMatchStatus.UNMATCHED_EXTERNAL
            else:
                results[ref] = ReconMatchStatus.AMOUNT_DISCREPANCY

        return results


# ==============================================================================
# 9. INGRESS & SECURITY SIMULATOR (F01)
# ==============================================================================

class IngressGateway:
    def __init__(self, secret_key: str, rate_limit_rps: int = 100):
        self.secret_key = secret_key
        self.rate_limit_rps = rate_limit_rps
        self.request_history: List[float] = []

    def verify_hmac(self, payload: str, signature: str, timestamp: int) -> bool:
        # Replay attack protection: timestamp must be within 300 seconds
        if abs(time.time() - timestamp) > 300:
            return False
        message = f"{timestamp}.{payload}".encode()
        expected = hmac.new(self.secret_key.encode(), message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def check_rate_limit(self) -> bool:
        now = time.time()
        self.request_history = [t for t in self.request_history if now - t < 1.0]
        if len(self.request_history) >= self.rate_limit_rps:
            return False
        self.request_history.append(now)
        return True
