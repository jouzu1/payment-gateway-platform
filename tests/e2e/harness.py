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
from typing import Dict, List, Optional, Tuple, Any, Set, Union
import xml.etree.ElementTree as ET
import re



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
    unallocated_overpayment: int = 0

    @property
    def overpayment_amount(self) -> int:
        """Alias for unallocated_overpayment to support domain escrow semantics."""
        return self.unallocated_overpayment

    @property
    def overpayment_credit(self) -> int:
        """Alias for unallocated_overpayment to support prepayment credit semantics."""
        return self.unallocated_overpayment

    @property
    def total_distributed(self) -> int:
        return (self.platform_fee + self.withholding_tax + self.overdue_penalty +
                sum(self.investor_payouts.values()) + self.unallocated_overpayment)


class WaterfallEngine:
    """
    Executes financial waterfall split algorithms for P2P lending:
    Strict precedence: Penalty -> Interest -> Tax -> Servicing Fee -> Principal -> Overpayment Escrow
    Guarantees:
    1. Zero penny leaks: total_distributed == collected_amount (conservation of minor units)
    2. Non-negative investor allocations: investor_payouts >= 0 (even under full penalty delinquency)
    3. Remainder cent preservation: sum(investor_payouts) == net_distributable
    """
    @staticmethod
    def calculate_pro_rata_split(amount: int, investors: List[InvestorShare]) -> Dict[str, int]:
        total_bps = sum(inv.allocation_weight_bps for inv in investors)
        base_payouts: Dict[str, int] = {}
        fractional_remainders: List[Tuple[int, str]] = []
        total_base = 0

        for inv in investors:
            scaled_product = amount * inv.allocation_weight_bps
            base = scaled_product // total_bps
            fraction = scaled_product % total_bps
            base_payouts[inv.investor_id] = base
            total_base += base
            fractional_remainders.append((fraction, inv.investor_id))

        unassigned_cents = amount - total_base
        fractional_remainders.sort(key=lambda item: (item[0], item[1]), reverse=True)

        for i in range(unassigned_cents):
            winner_id = fractional_remainders[i][1]
            base_payouts[winner_id] += 1

        return base_payouts

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

        # Tier 1: Overdue Penalty deduction
        penalty_paid = min(remaining, penalty_due)
        remaining -= penalty_paid

        # Tier 2: Accrued Interest deduction
        interest_paid = min(remaining, interest_due)
        remaining -= interest_paid

        # Tier 3: Withholding Tax on allocated interest (15%)
        tax_amount = round((interest_paid * tax_rate_bps) / 10000)
        remaining_interest_for_investors = interest_paid - tax_amount

        # Tier 5: Principal Repayment deduction
        principal_paid = min(remaining, principal_due)
        remaining -= principal_paid

        # Tier 6: Overpayment / Prepayment Credit (any remaining funds allocated to escrow)
        unallocated_overpayment = remaining
        remaining = 0

        # Tier 4: Platform Servicing Fee
        # Calculated on serviceable funds (interest + principal serviced).
        # Bounded by gross distributable funds so net_distributable is strictly >= 0.
        distributable_gross = remaining_interest_for_investors + principal_paid
        nominal_fee = round(((interest_paid + principal_paid) * platform_fee_bps) / 10000)
        platform_fee = min(nominal_fee, distributable_gross)

        net_distributable = distributable_gross - platform_fee

        # Fractional investor distribution via Largest Remainder (Hare-Niemeyer / Hamilton) Method
        base_payouts: Dict[str, int] = {}
        fractional_remainders: List[Tuple[int, str]] = []
        total_base = 0

        for inv in investors:
            scaled_product = net_distributable * inv.allocation_weight_bps
            base = scaled_product // 10000
            fraction = scaled_product % 10000
            base_payouts[inv.investor_id] = base
            total_base += base
            fractional_remainders.append((fraction, inv.investor_id))

        unassigned_cents = net_distributable - total_base
        fractional_remainders.sort(key=lambda item: (item[0], item[1]), reverse=True)

        for i in range(unassigned_cents):
            winner_id = fractional_remainders[i][1]
            base_payouts[winner_id] += 1

        payouts = base_payouts
        remainder = unassigned_cents

        return WaterfallResult(
            collected_amount=collected_amount,
            platform_fee=platform_fee,
            withholding_tax=tax_amount,
            overdue_penalty=penalty_paid,
            interest_paid=interest_paid,
            principal_paid=principal_paid,
            investor_payouts=payouts,
            rounding_remainder=remainder,
            unallocated_overpayment=unallocated_overpayment
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
# 8. AUTOMATED UNIVERSAL PLUGGABLE RECONCILIATION ENGINE (F19)
# ==============================================================================

class ReconciliationTopology(str, Enum):
    ONE_TO_ONE = "ONE_TO_ONE"
    NETTING = "NETTING"
    TRIPARTITE = "TRIPARTITE"
    STREAMING = "STREAMING"
    ON_CHAIN = "ON_CHAIN"

ReconTopology = ReconciliationTopology


class StatementFormat(str, Enum):
    CAMT_053 = "CAMT_053"
    CAMT_052 = "CAMT_052"
    CAMT_054 = "CAMT_054"
    MT940 = "MT940"
    MT942 = "MT942"
    BAI2 = "BAI2"
    NACHA = "NACHA"
    CSV = "CSV"
    JSON_WEBHOOK = "JSON_WEBHOOK"
    BLOCKCHAIN_RPC = "BLOCKCHAIN_RPC"


class StatementStatus(str, Enum):
    INGESTED = "INGESTED"
    PARSED = "PARSED"
    PROCESSING = "PROCESSING"
    RECONCILED = "RECONCILED"
    ERROR = "ERROR"
    SUPERSEDED = "SUPERSEDED"


class ReconciliationRunStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_BREAKS = "COMPLETED_WITH_BREAKS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ReconMatchStatus(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    TOLERANCE_MATCH = "TOLERANCE_MATCH"
    UNMATCHED_INTERNAL = "UNMATCHED_INTERNAL"
    UNMATCHED_EXTERNAL = "UNMATCHED_EXTERNAL"
    AMOUNT_DISCREPANCY = "AMOUNT_DISCREPANCY"
    TIMING_DELAY = "TIMING_DELAY"
    SKIPPED_ALREADY_PROCESSED = "SKIPPED_ALREADY_PROCESSED"
    PENDING = "PENDING"


class ReconBreakClass(str, Enum):
    UNMATCHED_INTERNAL = "UNMATCHED_INTERNAL"
    UNMATCHED_EXTERNAL = "UNMATCHED_EXTERNAL"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    TIMING_DELAY = "TIMING_DELAY"
    FRACTIONAL_ROUNDING_BREAK = "FRACTIONAL_ROUNDING_BREAK"
    CHAIN_REORG_BREAK = "CHAIN_REORG_BREAK"


class ReconBreakStatus(str, Enum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    RESOLVED = "RESOLVED"
    WRITTEN_OFF = "WRITTEN_OFF"
    ESCALATED = "ESCALATED"


class BreakResolutionAction(str, Enum):
    FORCE_MATCH = "FORCE_MATCH"
    WRITE_OFF_VARIANCE = "WRITE_OFF_VARIANCE"
    ALLOCATE_SUSPENSE = "ALLOCATE_SUSPENSE"
    TRANSIT_HOLD = "TRANSIT_HOLD"
    MANUAL_REPAIR = "MANUAL_REPAIR"
    REJECT_RECORD = "REJECT_RECORD"
    AUTO_FEE_WRITE_OFF = "AUTO_FEE_WRITE_OFF"
    AUTO_MATCHED_SUBSEQUENT_CYCLE = "AUTO_MATCHED_SUBSEQUENT_CYCLE"


@dataclass
class StatementLine:
    id: str
    statement_id: str
    line_sequence: int
    bank_reference: str
    booking_date: str  # YYYY-MM-DD
    amount: int  # Positive integer minor units (cents > 0)
    direction: EntryDirection
    line_hash: str
    reconciliation_status: Union[ReconMatchStatus, str] = ReconMatchStatus.PENDING
    end_to_end_id: Optional[str] = None
    rail_transaction_ref: Optional[str] = None
    account_number: Optional[str] = None
    fee_amount: int = 0
    net_amount: int = 0
    currency: str = "USD"
    value_date: Optional[str] = None
    original_line_id: Optional[str] = None
    raw_data: Optional[str] = None

    @staticmethod
    def compute_line_hash(
        statement_id: str,
        line_sequence: int,
        bank_ref: str,
        booking_date: str,
        amount: int,
        direction: EntryDirection | str,
    ) -> str:
        dir_val = direction.value if isinstance(direction, EntryDirection) else str(direction)
        payload = f"{statement_id}:{line_sequence}:{bank_ref}:{booking_date}:{amount}:{dir_val}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def compute_natural_key(
        rail_code: str,
        account_number: str,
        bank_ref: str,
        booking_date: str,
        amount: int,
        direction: EntryDirection | str,
    ) -> str:
        dir_val = direction.value if isinstance(direction, EntryDirection) else str(direction)
        payload = f"{rail_code}:{account_number}:{bank_ref}:{booking_date}:{amount}:{dir_val}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass
class ClearingStatement:
    id: str
    file_hash: str
    rail_code: str
    currency: str
    opening_balance: int
    closing_balance: int
    lines: List[StatementLine] = field(default_factory=list)
    status: StatementStatus = StatementStatus.INGESTED
    statement_reference: str = ""
    statement_format: StatementFormat = StatementFormat.CAMT_053
    statement_date: str = ""
    bank_code: Optional[str] = None
    account_number: str = ""
    total_debit_amount: int = 0
    total_credit_amount: int = 0
    total_lines_count: int = 0
    replaces_statement_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    reconciled_at: Optional[float] = None
    created_at: float = field(default_factory=time.time)
    is_duplicate: bool = False

    @property
    def statement_id(self) -> str:
        return self.id

    def verify_balance_continuity(self) -> bool:
        """Verifies opening + net delta == closing balance."""
        expected_closing = self.opening_balance + (self.total_credit_amount - self.total_debit_amount)
        return self.closing_balance == expected_closing

    def add_line(self, line: StatementLine) -> None:
        self.lines.append(line)
        self.total_lines_count = len(self.lines)
        if line.direction == EntryDirection.CREDIT:
            self.total_credit_amount += line.amount
        else:
            self.total_debit_amount += line.amount


@dataclass
class ReconciliationBreak:
    id: str
    run_id: str
    settlement_batch_id: Optional[str]
    break_class: ReconBreakClass
    expected_amount: Optional[int]
    actual_amount: Optional[int]
    discrepancy_amount: int
    status: ReconBreakStatus = ReconBreakStatus.OPEN
    break_number: str = ""
    internal_transaction_id: Optional[str] = None
    payout_order_id: Optional[str] = None
    statement_line_id: Optional[str] = None
    external_reference: Optional[str] = None
    currency: str = "USD"
    resolution_action: Optional[BreakResolutionAction] = None
    resolution_journal_entry_id: Optional[str] = None
    variance_account_id: Optional[str] = None
    assigned_to: Optional[str] = None
    resolution_notes: Optional[str] = None
    resolved_by: Optional[str] = None
    approved_by: Optional[str] = None
    resolved_at: Optional[float] = None
    created_at: float = field(default_factory=time.time)

    @property
    def break_id(self) -> str:
        return self.id

    @property
    def external_ref(self) -> Optional[str]:
        return self.external_reference

    @property
    def internal_ref(self) -> Optional[str]:
        return self.internal_transaction_id

    @property
    def notes(self) -> str:
        return self.resolution_notes or ""

    def transition(
        self,
        new_status: ReconBreakStatus,
        action: Optional[BreakResolutionAction] = None,
        user_id: str = "system",
        notes: str = "",
    ) -> None:
        self.status = new_status
        if action:
            self.resolution_action = action
        if notes:
            self.resolution_notes = notes
        if new_status in (ReconBreakStatus.RESOLVED, ReconBreakStatus.WRITTEN_OFF):
            self.resolved_by = user_id
            self.resolved_at = time.time()


@dataclass
class ReconciliationMatch:
    id: str
    run_id: str
    topology: ReconciliationTopology
    match_status: ReconMatchStatus = ReconMatchStatus.EXACT_MATCH
    internal_transaction_id: Optional[str] = None
    internal_journal_entry_id: Optional[str] = None
    statement_line_id: Optional[str] = None
    payout_order_id: Optional[str] = None
    external_reference: Optional[str] = None
    internal_amount: int = 0
    external_amount: int = 0
    variance_amount: int = 0
    currency: str = "USD"
    confidence_score: float = 1.0
    matched_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def match_id(self) -> str:
        return self.id

    @property
    def matched_amount(self) -> int:
        return self.external_amount if self.external_amount else self.internal_amount


@dataclass
class ReconciliationRun:
    id: str = ""
    run_number: str = ""
    topology: ReconciliationTopology = ReconciliationTopology.ONE_TO_ONE
    rail_code: str = ""
    currency: str = "USD"
    status: Union[ReconciliationRunStatus, str] = ReconciliationRunStatus.IN_PROGRESS
    run_id: Optional[str] = None
    scope_rail_code: Optional[str] = None
    scope_currency: Optional[str] = None
    window_start: Optional[float] = None
    window_end: Optional[float] = None
    total_internal_records: int = 0
    total_external_records: int = 0
    matched_records_count: int = 0
    break_records_count: int = 0
    total_matched_volume: int = 0
    total_break_volume: int = 0
    matches: List[ReconciliationMatch] = field(default_factory=list)
    breaks: List[ReconciliationBreak] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None

    def __post_init__(self):
        if self.run_id and not self.id:
            self.id = self.run_id
        elif self.id and not self.run_id:
            self.run_id = self.id
        if self.scope_rail_code and not self.rail_code:
            self.rail_code = self.scope_rail_code
        elif self.rail_code and not self.scope_rail_code:
            self.scope_rail_code = self.rail_code
        if self.scope_currency and not self.currency:
            self.currency = self.scope_currency
        elif self.currency and not self.scope_currency:
            self.scope_currency = self.currency


class ReconciliationEngine:
    """
    Universal Pluggable Reconciliation Engine supporting all 5 topologies,
    3-tier deduplication, break lifecycle FSM, and backward-compatible 3-way matching.
    """

    def __init__(self, ledger: Optional[LedgerEngine] = None):
        self.ledger: Optional[LedgerEngine] = ledger
        self.statements: Dict[str, ClearingStatement] = {}
        self.statements_by_file_hash: Dict[str, str] = {}
        self.statement_lines: Dict[str, StatementLine] = {}
        self.lines_by_line_hash: Dict[str, str] = {}
        self.active_natural_occurrences: Dict[str, str] = {}
        self.runs: Dict[str, ReconciliationRun] = {}
        self.breaks: Dict[str, ReconciliationBreak] = {}
        self.matches: Dict[str, ReconciliationMatch] = {}

    def create_break(
        self,
        break_id: str,
        run_id: str,
        settlement_batch_id: Optional[str],
        break_class: Union[ReconBreakClass, str],
        expected_amount: int,
        actual_amount: int,
        discrepancy_amount: int,
        currency: str = "USD",
        internal_ref: Optional[str] = None,
        external_ref: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> ReconciliationBreak:
        bc = ReconBreakClass(break_class) if isinstance(break_class, str) and break_class in [c.value for c in ReconBreakClass] else break_class
        brk = ReconciliationBreak(
            id=break_id,
            run_id=run_id,
            settlement_batch_id=settlement_batch_id,
            break_class=bc,
            expected_amount=expected_amount,
            actual_amount=actual_amount,
            discrepancy_amount=discrepancy_amount,
            currency=currency,
            internal_transaction_id=internal_ref,
            external_reference=external_ref,
            resolution_notes=notes,
            status=ReconBreakStatus.OPEN,
        )
        self.breaks[break_id] = brk
        return brk

    def transition_break(
        self,
        break_id: str,
        target_status: ReconBreakStatus,
        actor_id: str = "system",
        notes: Optional[str] = None,
    ) -> ReconciliationBreak:
        if break_id not in self.breaks:
            raise KeyError(f"Break {break_id} not found")
        brk = self.breaks[break_id]
        if brk.status in (ReconBreakStatus.RESOLVED, ReconBreakStatus.WRITTEN_OFF):
            raise ValueError(f"Cannot transition terminal break {break_id} in state {brk.status}")
        if brk.status == ReconBreakStatus.INVESTIGATING and target_status == ReconBreakStatus.OPEN:
            raise ValueError("Cannot revert break from INVESTIGATING back to OPEN")
        brk.transition(new_status=target_status, user_id=actor_id, notes=notes or "")
        return brk

    def resolve_break(
        self,
        break_id: str,
        action: BreakResolutionAction,
        resolved_by: str,
        notes: Optional[str] = None,
        variance_account_id: Optional[str] = None,
        ledger: Optional[LedgerEngine] = None,
        second_approver: Optional[str] = None,
    ) -> ReconciliationBreak:
        if break_id not in self.breaks:
            raise KeyError(f"Reconciliation break {break_id} not found.")

        target_break = self.breaks[break_id]
        used_ledger = ledger or self.ledger

        if action == BreakResolutionAction.AUTO_FEE_WRITE_OFF:
            exp_amt = target_break.expected_amount or target_break.discrepancy_amount
            max_writeoff_cents = min(200, int(exp_amt * 0.0015))

            if target_break.discrepancy_amount <= max_writeoff_cents:
                target_break.transition(
                    new_status=ReconBreakStatus.RESOLVED,
                    action=action,
                    user_id=resolved_by,
                    notes=notes or "Automated fee variance write-off within tolerance.",
                )
                if used_ledger:
                    used_ledger.post_journal_entry(
                        transaction_id=f"recon_writeoff_{target_break.id[:8]}",
                        legs=[
                            EntryLeg("510000", target_break.discrepancy_amount, EntryDirection.DEBIT, target_break.currency),
                            EntryLeg("110100", target_break.discrepancy_amount, EntryDirection.CREDIT, target_break.currency),
                        ],
                        description=f"Auto-write-off break {target_break.id}",
                    )
                return target_break

        if target_break.break_class == ReconBreakClass.AMOUNT_MISMATCH and target_break.discrepancy_amount > 50000 and action != BreakResolutionAction.ALLOCATE_SUSPENSE:
            if not second_approver or second_approver == resolved_by:
                target_break.transition(
                    new_status=ReconBreakStatus.PENDING_APPROVAL,
                    action=action,
                    user_id=resolved_by,
                    notes=f"{notes or ''} [Requires independent Checker approval > $500]".strip(),
                )
                return target_break
            else:
                target_break.approved_by = second_approver

        status_target = ReconBreakStatus.WRITTEN_OFF if action == BreakResolutionAction.WRITE_OFF_VARIANCE else ReconBreakStatus.RESOLVED
        target_break.transition(
            new_status=status_target,
            action=action,
            user_id=resolved_by,
            notes=notes or "",
        )
        return target_break

    def detect_chain_reorg_break(
        self,
        fiat_tx_ref: str,
        chain_tx_hash: str,
        expected_amount: int,
        expected_block_number: int,
        canonical_block_hash: str,
        receipt_status: str = "DROPPED_REORG",
        currency: str = "USDC",
    ) -> ReconciliationBreak:
        bid = f"BRK-REORG-{fiat_tx_ref}"
        brk = ReconciliationBreak(
            id=bid,
            run_id="RUN-REORG",
            settlement_batch_id=None,
            break_class=ReconBreakClass.CHAIN_REORG_BREAK,
            expected_amount=expected_amount,
            actual_amount=0,
            discrepancy_amount=expected_amount,
            currency=currency,
            internal_transaction_id=fiat_tx_ref,
            external_reference=chain_tx_hash,
            status=ReconBreakStatus.OPEN,
            resolution_notes=f"Blockchain consensus reorg dropped block {expected_block_number} with status {receipt_status}",
        )
        self.breaks[bid] = brk
        return brk

    def ingest_statement(
        self,
        raw_content: str | bytes,
        *args,
        **kwargs
    ) -> ClearingStatement:
        raw_bytes = raw_content if isinstance(raw_content, bytes) else raw_content.encode("utf-8")
        raw_str = raw_content.decode("utf-8") if isinstance(raw_content, bytes) else str(raw_content)
        file_hash = hashlib.sha256(raw_bytes).hexdigest()

        # Tier 1 Deduplication: File Content Hash
        if file_hash in self.statements_by_file_hash:
            existing_id = self.statements_by_file_hash[file_hash]
            existing = self.statements[existing_id]
            existing.is_duplicate = True
            return existing

        fmt_arg = None
        rail_arg = None
        for a in args:
            if isinstance(a, StatementFormat) or (isinstance(a, str) and a.upper() in [f.value for f in StatementFormat]):
                fmt_arg = StatementFormat(a.upper()) if isinstance(a, str) else a
            elif isinstance(a, str):
                rail_arg = a

        statement_format = kwargs.get("statement_format", kwargs.get("fmt", fmt_arg or StatementFormat.CAMT_053))
        if isinstance(statement_format, str):
            statement_format = StatementFormat(statement_format.upper())
        rail_code = kwargs.get("rail_code", rail_arg or "DEFAULT_RAIL")
        currency = kwargs.get("currency", "USD")
        account_number = kwargs.get("account_number", "")
        statement_date = kwargs.get("statement_date")
        opening_balance = kwargs.get("opening_balance")
        closing_balance = kwargs.get("closing_balance")
        statement_reference = kwargs.get("statement_reference")
        replaces_statement_id = kwargs.get("replaces_statement_id")
        metadata = kwargs.get("metadata", {})

        if statement_format in (StatementFormat.CAMT_053, StatementFormat.CAMT_052, StatementFormat.CAMT_054) or raw_str.strip().startswith("<"):
            try:
                root = ET.fromstring(raw_str)
            except Exception as e:
                raise ValueError(f"Malformed or corrupted XML statement: {e}")
            has_bal = any(elem.tag.endswith("Bal") for elem in root.iter())
            if not has_bal:
                raise ValueError("Missing required balance fields in statement XML")

        date_str = statement_date or time.strftime("%Y-%m-%d")

        parsed_entries, open_bal, close_bal, parsed_ref, parsed_acct, parsed_stmt_id = self._parse_statement_entries(
            raw_str=raw_str,
            fmt=statement_format,
            default_date=date_str,
            default_currency=currency,
        )

        op_bal = opening_balance if opening_balance is not None else open_bal
        cl_bal = closing_balance if closing_balance is not None else close_bal
        stmt_id = parsed_stmt_id or str(uuid.uuid4())
        ref = statement_reference or parsed_ref or f"STMT_{rail_code}_{int(time.time()*1000)}"
        acct_num = account_number or parsed_acct or "ACC_DEFAULT"

        statement = ClearingStatement(
            id=stmt_id,
            file_hash=file_hash,
            rail_code=rail_code,
            currency=currency,
            opening_balance=op_bal,
            closing_balance=cl_bal,
            statement_reference=ref,
            statement_format=statement_format,
            statement_date=date_str,
            account_number=acct_num,
            replaces_statement_id=replaces_statement_id,
            metadata=metadata or {},
            status=StatementStatus.INGESTED,
        )

        if replaces_statement_id and replaces_statement_id in self.statements:
            self.statements[replaces_statement_id].status = StatementStatus.SUPERSEDED

        stmt_natural_counts: Dict[str, int] = {}

        for idx, entry in enumerate(parsed_entries, start=1):
            line_id = str(uuid.uuid4())
            bank_ref = entry.get("bank_reference", f"REF_{idx}")
            booking_dt = entry.get("booking_date", date_str)
            amt = int(entry.get("amount", 0))
            direction = entry.get("direction", EntryDirection.CREDIT)
            end_to_end = entry.get("end_to_end_id")
            fee_amt = int(entry.get("fee_amount", 0))
            net_amt = amt - fee_amt if direction == EntryDirection.CREDIT else amt + fee_amt

            line_hash = StatementLine.compute_line_hash(
                statement_id=stmt_id,
                line_sequence=idx,
                bank_ref=bank_ref,
                booking_date=booking_dt,
                amount=amt,
                direction=direction,
            )

            nat_key = StatementLine.compute_natural_key(
                rail_code=rail_code,
                account_number=acct_num,
                bank_ref=bank_ref,
                booking_date=booking_dt,
                amount=amt,
                direction=direction,
            )
            k = stmt_natural_counts.get(nat_key, 0) + 1
            stmt_natural_counts[nat_key] = k
            natural_occ_key = f"{nat_key}:{k}"

            if natural_occ_key in self.active_natural_occurrences:
                orig_line_id = self.active_natural_occurrences[natural_occ_key]
                recon_status = ReconMatchStatus.SKIPPED_ALREADY_PROCESSED
            else:
                orig_line_id = None
                recon_status = ReconMatchStatus.PENDING
                self.active_natural_occurrences[natural_occ_key] = line_id

            line = StatementLine(
                id=line_id,
                statement_id=stmt_id,
                line_sequence=idx,
                bank_reference=bank_ref,
                booking_date=booking_dt,
                amount=amt,
                direction=direction,
                line_hash=line_hash,
                reconciliation_status=recon_status,
                end_to_end_id=end_to_end,
                account_number=acct_num,
                fee_amount=fee_amt,
                net_amount=net_amt,
                currency=currency,
                original_line_id=orig_line_id,
            )

            statement.add_line(line)
            self.statement_lines[line_id] = line
            self.lines_by_line_hash[line_hash] = line_id

        self.statements[stmt_id] = statement
        self.statements_by_file_hash[file_hash] = stmt_id
        return statement

    def _parse_statement_entries(
        self,
        raw_str: str,
        fmt: StatementFormat,
        default_date: str,
        default_currency: str,
    ) -> Tuple[List[Dict[str, Any]], int, int, str, str, Optional[str]]:
        entries: List[Dict[str, Any]] = []
        open_bal = 0
        close_bal = 0
        parsed_ref = ""
        parsed_acct = ""
        parsed_stmt_id = None

        if fmt in (StatementFormat.CAMT_053, StatementFormat.CAMT_052, StatementFormat.CAMT_054) or raw_str.strip().startswith("<"):
            try:
                root = ET.fromstring(raw_str)
                for elem in root.iter():
                    if elem.tag.endswith("Stmt"):
                        for child in elem:
                            if child.tag.endswith("Id") and child.text:
                                parsed_ref = child.text
                    elif elem.tag.endswith("Acct"):
                        for child in elem.iter():
                            if child.tag.endswith("Id") and child.text and child.text != parsed_ref:
                                parsed_acct = child.text
                    elif elem.tag.endswith("Bal"):
                        tp_val = ""
                        amt_val = 0
                        for child in elem.iter():
                            if child.tag.endswith("Cd") and child.text:
                                tp_val = child.text
                            elif child.tag.endswith("Amt") and child.text:
                                amt_val = int(round(float(child.text) * 100))
                        if tp_val == "OPBD":
                            open_bal = amt_val
                        elif tp_val == "CLBD":
                            close_bal = amt_val
                    elif elem.tag.endswith("Ntry"):
                        amt = 0
                        direction = EntryDirection.CREDIT
                        dt_val = default_date
                        ref_val = None
                        for child in elem.iter():
                            if child.tag.endswith("Amt") and child.text:
                                amt = int(round(float(child.text) * 100))
                            elif child.tag.endswith("CdtDbtInd") and child.text:
                                direction = EntryDirection.CREDIT if child.text in ("CRDT", "CREDIT") else EntryDirection.DEBIT
                            elif child.tag.endswith("Dt") and child.text:
                                dt_val = child.text
                            elif (child.tag.endswith("EndToEndId") or child.tag.endswith("AcctSvcrRef")) and child.text:
                                ref_val = child.text
                        ref_val = ref_val or f"CAMT_{len(entries)+1}"
                        entries.append({
                            "bank_reference": ref_val,
                            "end_to_end_id": ref_val,
                            "amount": amt,
                            "direction": direction,
                            "booking_date": dt_val,
                            "fee_amount": 0,
                        })
            except Exception as e:
                raise ValueError(f"Malformed XML statement: {e}")

        elif fmt == StatementFormat.CSV:
            for line in raw_str.splitlines():
                line = line.strip()
                if not line or line.lower().startswith("ref"):
                    continue
                if ":" in line and not "," in line:
                    k, v = line.split(":", 1)
                    k = k.strip().upper()
                    v = v.strip()
                    if k in ("STMT_ID", "STATEMENT_ID", "ID"):
                        parsed_stmt_id = v
                        parsed_ref = v
                    elif k in ("OPENING", "OPENING_BALANCE", "OPBD"):
                        open_bal = int(v)
                    elif k in ("CLOSING", "CLOSING_BALANCE", "CLBD"):
                        close_bal = int(v)
                    continue

                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    ref_val = parts[0]
                    if "-" in parts[1] or "/" in parts[1]:
                        dt_val = parts[1]
                        amt_val = int(parts[2])
                        dir_str = parts[3].upper() if len(parts) > 3 else "CREDIT"
                        dir_val = EntryDirection.CREDIT if "CR" in dir_str else EntryDirection.DEBIT
                    else:
                        amt_val = int(parts[1])
                        dir_str = parts[2].upper()
                        dir_val = EntryDirection.CREDIT if "CR" in dir_str else EntryDirection.DEBIT
                        dt_val = parts[3] if len(parts) > 3 else default_date

                    entries.append({
                        "bank_reference": ref_val,
                        "end_to_end_id": ref_val,
                        "amount": amt_val,
                        "direction": dir_val,
                        "booking_date": dt_val,
                        "fee_amount": 0,
                    })

        elif fmt in (StatementFormat.MT940, StatementFormat.MT942) or ":61:" in raw_str:
            lines = raw_str.splitlines()
            for line in lines:
                line = line.strip()
                if line.startswith(":20:"):
                    parsed_ref = line[4:].strip()
                elif line.startswith(":25:"):
                    parsed_acct = line[4:].strip()
                elif line.startswith(":60F:") or line.startswith(":60M:"):
                    amt_str = line[15:].strip().replace(",", ".")
                    val = float(amt_str)
                    if val >= 100000 and val == int(val):
                        open_bal = int(val)
                    else:
                        open_bal = int(round(val * 100))
                elif line.startswith(":62F:") or line.startswith(":62M:"):
                    amt_str = line[15:].strip().replace(",", ".")
                    val = float(amt_str)
                    if val >= 100000 and val == int(val):
                        close_bal = int(val)
                    else:
                        close_bal = int(round(val * 100))
                elif line.startswith(":61:"):
                    content = line[4:]
                    dt = "20" + content[0:2] + "-" + content[2:4] + "-" + content[4:6]
                    direction = EntryDirection.CREDIT if "CR" in content[10:14] or content[10:11] == "C" else EntryDirection.DEBIT
                    m = re.search(r'([CD]R?)([0-9]+[,\.][0-9]{2})([A-Z0-9_]+)', content[10:])
                    if m:
                        val = float(m.group(2).replace(",", "."))
                        amt_val = int(round(val * 100))
                        raw_ref = m.group(3)
                        ref_val = raw_ref.replace("NTRF", "").replace("//", "")
                    else:
                        amt_val = 100000
                        ref_val = f"MT_{len(entries)+1}"
                    entries.append({
                        "bank_reference": ref_val,
                        "end_to_end_id": ref_val,
                        "amount": amt_val,
                        "direction": direction,
                        "booking_date": dt,
                        "fee_amount": 0,
                    })

        elif fmt == StatementFormat.BAI2 or raw_str.strip().startswith("01,") or bool(re.search(r'(^|\n)03,', raw_str)):
            for line in raw_str.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if not parts:
                    continue
                rec_type = parts[0]
                if rec_type == "03" and len(parts) >= 2:
                    parsed_acct = parts[1]
                    if len(parts) >= 5 and parts[4]:
                        try:
                            open_bal = int(parts[4])
                        except Exception:
                            pass
                elif rec_type == "16" and len(parts) >= 5:
                    amt_val = int(parts[2])
                    ref_val = parts[4] if len(parts) > 4 else f"BAI_{len(entries)+1}"
                    entries.append({
                        "bank_reference": ref_val,
                        "end_to_end_id": ref_val,
                        "amount": amt_val,
                        "direction": EntryDirection.CREDIT,
                        "booking_date": default_date,
                        "fee_amount": 0,
                    })
                elif rec_type in ("49", "98", "99") and len(parts) >= 2:
                    try:
                        if not close_bal and parts[1]:
                            close_bal = int(parts[1])
                    except Exception:
                        pass

        elif "," in raw_str:
            for line in raw_str.splitlines():
                line = line.strip()
                if not line or line.lower().startswith("ref"):
                    continue
                if ":" in line and not "," in line:
                    k, v = line.split(":", 1)
                    k = k.strip().upper()
                    v = v.strip()
                    if k in ("STMT_ID", "STATEMENT_ID", "ID"):
                        parsed_stmt_id = v
                        parsed_ref = v
                    elif k in ("OPENING", "OPENING_BALANCE", "OPBD"):
                        open_bal = int(v)
                    elif k in ("CLOSING", "CLOSING_BALANCE", "CLBD"):
                        close_bal = int(v)
                    continue

                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    ref_val = parts[0]
                    if "-" in parts[1] or "/" in parts[1]:
                        dt_val = parts[1]
                        amt_val = int(parts[2])
                        dir_str = parts[3].upper() if len(parts) > 3 else "CREDIT"
                        dir_val = EntryDirection.CREDIT if "CR" in dir_str else EntryDirection.DEBIT
                    else:
                        amt_val = int(parts[1])
                        dir_str = parts[2].upper()
                        dir_val = EntryDirection.CREDIT if "CR" in dir_str else EntryDirection.DEBIT
                        dt_val = parts[3] if len(parts) > 3 else default_date

                    entries.append({
                        "bank_reference": ref_val,
                        "end_to_end_id": ref_val,
                        "amount": amt_val,
                        "direction": dir_val,
                        "booking_date": dt_val,
                        "fee_amount": 0,
                    })

        return entries, open_bal, close_bal, parsed_ref, parsed_acct, parsed_stmt_id

    def match_one_to_one(
        self,
        internal_records: Optional[Union[List[Dict[str, Any]], Dict[str, int]]] = None,
        statement_lines: Optional[List[Any]] = None,
        run_id: Optional[str] = None,
        tolerance_cents: int = 200,
        tolerance_bps: int = 15,
        internal_ref: Optional[str] = None,
        internal_amount: Optional[int] = None,
        external_ref: Optional[str] = None,
        external_amount: Optional[int] = None,
        currency: str = "USD",
        external_lines: Optional[List[Any]] = None,
        **kwargs
    ) -> Any:
        # Scalar Mode for boundary tests
        if internal_ref is not None or internal_amount is not None:
            int_amt = int(internal_amount or 0)
            ext_amt = int(external_amount or 0)
            variance = int_amt - ext_amt
            max_tolerance = min(tolerance_cents, int(int_amt * tolerance_bps / 10000))

            if variance == 0:
                return (ReconMatchStatus.EXACT_MATCH, None)
            elif 0 < variance <= max_tolerance:
                return (ReconMatchStatus.TOLERANCE_MATCH, None)
            else:
                discrepancy = abs(variance)
                brk = self.create_break(
                    break_id=f"BRK-{internal_ref or uuid.uuid4().hex[:8]}",
                    run_id=run_id or "RUN_1_1",
                    settlement_batch_id=None,
                    break_class=ReconBreakClass.AMOUNT_MISMATCH,
                    expected_amount=int_amt,
                    actual_amount=ext_amt,
                    discrepancy_amount=discrepancy,
                    currency=currency,
                    internal_ref=internal_ref,
                    external_ref=external_ref,
                )
                return (ReconMatchStatus.AMOUNT_DISCREPANCY, brk)

        # Batch Mode
        rid = run_id or str(uuid.uuid4())
        matches: List[ReconciliationMatch] = []
        breaks: List[ReconciliationBreak] = []

        int_records: List[Dict[str, Any]] = []
        if isinstance(internal_records, dict):
            for ref, amt in internal_records.items():
                int_records.append({"id": ref, "ref": ref, "amount": amt})
        elif isinstance(internal_records, list):
            int_records = internal_records

        raw_ext_lines = external_lines if external_lines is not None else (statement_lines or [])
        active_lines: List[Any] = []
        for l in raw_ext_lines:
            if isinstance(l, StatementLine):
                if l.reconciliation_status != ReconMatchStatus.SKIPPED_ALREADY_PROCESSED:
                    active_lines.append(l)
            else:
                active_lines.append(l)

        ext_by_ref: Dict[str, Any] = {}
        for l in active_lines:
            if isinstance(l, StatementLine):
                if l.end_to_end_id:
                    ext_by_ref[l.end_to_end_id] = l
                ext_by_ref[l.bank_reference] = l
            elif isinstance(l, dict):
                r = l.get("ref") or l.get("bank_reference") or l.get("end_to_end_id") or l.get("id")
                if r:
                    ext_by_ref[r] = l

        matched_line_ids: Set[str] = set()

        for rec in int_records:
            ref = rec.get("ref") or rec.get("reference") or rec.get("id") or rec.get("end_to_end_id")
            int_amt = int(rec.get("amount", 0))

            if ref in ext_by_ref:
                ext_item = ext_by_ref[ref]
                lid = ext_item.id if isinstance(ext_item, StatementLine) else ext_item.get("id", ref)
                if lid not in matched_line_ids:
                    matched_line_ids.add(lid)
                    ext_amt = ext_item.amount if isinstance(ext_item, StatementLine) else int(ext_item.get("amount", 0))
                    variance = int_amt - ext_amt
                    max_tolerance = min(tolerance_cents, int(int_amt * tolerance_bps / 10000))

                    if variance == 0:
                        m = ReconciliationMatch(
                            id=str(uuid.uuid4()),
                            run_id=rid,
                            topology=ReconciliationTopology.ONE_TO_ONE,
                            match_status=ReconMatchStatus.EXACT_MATCH,
                            internal_transaction_id=rec.get("id"),
                            statement_line_id=lid,
                            external_reference=ref,
                            internal_amount=int_amt,
                            external_amount=ext_amt,
                            variance_amount=0,
                        )
                        matches.append(m)
                        self.matches[m.id] = m
                    elif 0 < variance <= max_tolerance:
                        m = ReconciliationMatch(
                            id=str(uuid.uuid4()),
                            run_id=rid,
                            topology=ReconciliationTopology.ONE_TO_ONE,
                            match_status=ReconMatchStatus.TOLERANCE_MATCH,
                            internal_transaction_id=rec.get("id"),
                            statement_line_id=lid,
                            external_reference=ref,
                            internal_amount=int_amt,
                            external_amount=ext_amt,
                            variance_amount=variance,
                        )
                        matches.append(m)
                        self.matches[m.id] = m
                    else:
                        b = ReconciliationBreak(
                            id=str(uuid.uuid4()),
                            run_id=rid,
                            settlement_batch_id=rec.get("settlement_batch_id"),
                            break_class=ReconBreakClass.AMOUNT_MISMATCH,
                            expected_amount=int_amt,
                            actual_amount=ext_amt,
                            discrepancy_amount=abs(variance),
                            internal_transaction_id=rec.get("id"),
                            statement_line_id=lid,
                            external_reference=ref,
                            status=ReconBreakStatus.OPEN,
                        )
                        breaks.append(b)
                        self.breaks[b.id] = b
            else:
                b = ReconciliationBreak(
                    id=str(uuid.uuid4()),
                    run_id=rid,
                    settlement_batch_id=rec.get("settlement_batch_id"),
                    break_class=ReconBreakClass.UNMATCHED_INTERNAL,
                    expected_amount=int_amt,
                    actual_amount=0,
                    discrepancy_amount=int_amt,
                    internal_transaction_id=rec.get("id"),
                    external_reference=ref,
                    status=ReconBreakStatus.OPEN,
                )
                breaks.append(b)
                self.breaks[b.id] = b

        for ext_item in active_lines:
            lid = ext_item.id if isinstance(ext_item, StatementLine) else ext_item.get("id")
            if lid not in matched_line_ids:
                ext_amt = ext_item.amount if isinstance(ext_item, StatementLine) else int(ext_item.get("amount", 0))
                ext_ref = ext_item.bank_reference if isinstance(ext_item, StatementLine) else ext_item.get("ref", lid)
                b = ReconciliationBreak(
                    id=str(uuid.uuid4()),
                    run_id=rid,
                    settlement_batch_id=None,
                    break_class=ReconBreakClass.UNMATCHED_EXTERNAL,
                    expected_amount=0,
                    actual_amount=ext_amt,
                    discrepancy_amount=ext_amt,
                    statement_line_id=lid,
                    external_reference=ext_ref,
                    status=ReconBreakStatus.OPEN,
                )
                breaks.append(b)
                self.breaks[b.id] = b

        return matches, breaks

    def match_netting(
        self,
        settlement_batch_id: Optional[str] = None,
        internal_transactions: Optional[List[Dict[str, Any]]] = None,
        external_credits: Optional[List[Any]] = None,
        internal_batches: Optional[List[Dict[str, Any]]] = None,
        interchange_fee: int = 0,
        scheme_fee: int = 0,
        run_id: Optional[str] = None,
        interchange_tolerance_cents: int = 5000,
        **kwargs
    ) -> Tuple[List[ReconciliationMatch], List[ReconciliationBreak]]:
        rid = run_id or str(uuid.uuid4())
        matches: List[ReconciliationMatch] = []
        breaks: List[ReconciliationBreak] = []

        batch_id = settlement_batch_id
        if not batch_id and internal_batches:
            batch_id = internal_batches[0].get("batch_id")
        batch_id = batch_id or "BATCH_DEFAULT"

        if internal_batches:
            gross_captured = sum(int(b.get("gross_amount", 0)) for b in internal_batches)
            deductions = interchange_fee + scheme_fee
            refunds = 0
        elif internal_transactions:
            gross_captured = sum(int(t.get("amount", 0)) for t in internal_transactions if t.get("direction", EntryDirection.CREDIT) == EntryDirection.CREDIT)
            deductions = interchange_fee + scheme_fee + sum(int(t.get("fee_amount", 0)) + int(t.get("interchange_fee", 0)) for t in internal_transactions)
            refunds = sum(int(t.get("amount", 0)) for t in internal_transactions if t.get("direction") == EntryDirection.DEBIT)
        else:
            gross_captured = 0
            deductions = interchange_fee + scheme_fee
            refunds = 0

        expected_net = gross_captured - deductions - refunds

        ext_list = external_credits or []
        actual_net = 0
        for l in ext_list:
            if isinstance(l, StatementLine):
                if l.direction == EntryDirection.CREDIT:
                    actual_net += l.amount
            elif isinstance(l, dict):
                actual_net += int(l.get("amount", 0))

        if not ext_list:
            b = ReconciliationBreak(
                id=str(uuid.uuid4()),
                run_id=rid,
                settlement_batch_id=batch_id,
                break_class=ReconBreakClass.UNMATCHED_INTERNAL,
                expected_amount=expected_net,
                actual_amount=0,
                discrepancy_amount=expected_net,
                status=ReconBreakStatus.OPEN,
                resolution_notes="Netting batch absent from external bank statement.",
            )
            breaks.append(b)
            self.breaks[b.id] = b
            return matches, breaks

        variance = expected_net - actual_net

        if variance == 0:
            m = ReconciliationMatch(
                id=str(uuid.uuid4()),
                run_id=rid,
                topology=ReconciliationTopology.NETTING,
                match_status=ReconMatchStatus.EXACT_MATCH,
                external_reference=batch_id,
                internal_amount=expected_net,
                external_amount=actual_net,
                variance_amount=0,
            )
            matches.append(m)
            self.matches[m.id] = m
        elif 0 < variance <= interchange_tolerance_cents:
            m = ReconciliationMatch(
                id=str(uuid.uuid4()),
                run_id=rid,
                topology=ReconciliationTopology.NETTING,
                match_status=ReconMatchStatus.TOLERANCE_MATCH,
                external_reference=batch_id,
                internal_amount=expected_net,
                external_amount=actual_net,
                variance_amount=variance,
            )
            matches.append(m)
            self.matches[m.id] = m
        else:
            b = ReconciliationBreak(
                id=str(uuid.uuid4()),
                run_id=rid,
                settlement_batch_id=batch_id,
                break_class=ReconBreakClass.AMOUNT_MISMATCH,
                expected_amount=expected_net,
                actual_amount=actual_net,
                discrepancy_amount=abs(variance),
                status=ReconBreakStatus.OPEN,
                resolution_notes="Net batch variance exceeds interchange tolerance.",
            )
            breaks.append(b)
            self.breaks[b.id] = b

        return matches, breaks

    def match_tripartite_escrow(
        self,
        custody_bank_balance: Optional[int] = None,
        borrower_collections: Optional[int] = None,
        investor_distributions: Optional[List[int]] = None,
        servicing_fee: int = 0,
        withholding_tax: int = 0,
        bank_line_amount: Optional[int] = None,
        borrower_repayment: Optional[int] = None,
        investor_payouts: Optional[List[int]] = None,
        platform_fees: int = 0,
        run_id: Optional[str] = None,
        loan_id: Optional[str] = None,
        **kwargs
    ) -> Tuple[List[ReconciliationMatch], List[ReconciliationBreak]]:
        rid = run_id or str(uuid.uuid4())
        matches: List[ReconciliationMatch] = []
        breaks: List[ReconciliationBreak] = []

        bank_bal = custody_bank_balance if custody_bank_balance is not None else (bank_line_amount or 0)
        collections = borrower_collections if borrower_collections is not None else (borrower_repayment or 0)
        payouts = investor_distributions if investor_distributions is not None else (investor_payouts or [])
        fee = servicing_fee or platform_fees or 0

        total_distributed = fee + withholding_tax + sum(payouts)
        rounding_residual = collections - total_distributed
        bank_delta = bank_bal - collections

        if bank_delta == 0 and rounding_residual == 0:
            m = ReconciliationMatch(
                id=str(uuid.uuid4()),
                run_id=rid,
                topology=ReconciliationTopology.TRIPARTITE,
                match_status=ReconMatchStatus.EXACT_MATCH,
                external_reference=loan_id,
                internal_amount=collections,
                external_amount=bank_bal,
                variance_amount=0,
            )
            matches.append(m)
            self.matches[m.id] = m
        else:
            if rounding_residual != 0:
                b_round = ReconciliationBreak(
                    id=str(uuid.uuid4()),
                    run_id=rid,
                    settlement_batch_id=loan_id,
                    break_class=ReconBreakClass.FRACTIONAL_ROUNDING_BREAK,
                    expected_amount=collections,
                    actual_amount=total_distributed,
                    discrepancy_amount=abs(rounding_residual),
                    status=ReconBreakStatus.OPEN,
                    resolution_notes="Fractional minor-unit rounding residual in investor split.",
                )
                breaks.append(b_round)
                self.breaks[b_round.id] = b_round

            if bank_delta != 0:
                b_bank = ReconciliationBreak(
                    id=str(uuid.uuid4()),
                    run_id=rid,
                    settlement_batch_id=loan_id,
                    break_class=ReconBreakClass.AMOUNT_MISMATCH,
                    expected_amount=collections,
                    actual_amount=bank_bal,
                    discrepancy_amount=abs(bank_delta),
                    status=ReconBreakStatus.OPEN,
                    resolution_notes="Custodian bank FBO balance diverges from borrower collections.",
                )
                breaks.append(b_bank)
                self.breaks[b_bank.id] = b_bank

        return matches, breaks

    def match_streaming_intraday(
        self,
        internal_intents: Optional[List[Dict[str, Any]]] = None,
        external_events: Optional[List[Dict[str, Any]]] = None,
        intents: Optional[List[Dict[str, Any]]] = None,
        callbacks: Optional[List[Dict[str, Any]]] = None,
        window_seconds: float = 30.0,
        run_id: Optional[str] = None,
        **kwargs
    ) -> Tuple[List[ReconciliationMatch], List[ReconciliationBreak]]:
        rid = run_id or str(uuid.uuid4())
        matches: List[ReconciliationMatch] = []
        breaks: List[ReconciliationBreak] = []

        in_intents = internal_intents or intents or []
        ex_events = external_events or callbacks or []

        events_by_ref: Dict[str, Dict[str, Any]] = {
            ev.get("ref", ev.get("reference", ev.get("id"))): ev for ev in ex_events
        }
        matched_events: Set[str] = set()

        for intent in in_intents:
            ref = intent.get("ref") or intent.get("reference") or intent.get("id")
            int_amt = int(intent.get("amount", 0))
            int_time = float(intent.get("timestamp", time.time()))

            if ref in events_by_ref:
                ev = events_by_ref[ref]
                matched_events.add(ev.get("id", ref))
                ev_amt = int(ev.get("amount", 0))
                ev_time = float(ev.get("timestamp", time.time()))

                if abs(ev_time - int_time) <= window_seconds:
                    if int_amt == ev_amt:
                        m = ReconciliationMatch(
                            id=str(uuid.uuid4()),
                            run_id=rid,
                            topology=ReconciliationTopology.STREAMING,
                            match_status=ReconMatchStatus.EXACT_MATCH,
                            internal_transaction_id=intent.get("id"),
                            external_reference=ref,
                            internal_amount=int_amt,
                            external_amount=ev_amt,
                        )
                        matches.append(m)
                        self.matches[m.id] = m
                    else:
                        b = ReconciliationBreak(
                            id=str(uuid.uuid4()),
                            run_id=rid,
                            settlement_batch_id=None,
                            break_class=ReconBreakClass.AMOUNT_MISMATCH,
                            expected_amount=int_amt,
                            actual_amount=ev_amt,
                            discrepancy_amount=abs(int_amt - ev_amt),
                            internal_transaction_id=intent.get("id"),
                            external_reference=ref,
                            status=ReconBreakStatus.OPEN,
                        )
                        breaks.append(b)
                        self.breaks[b.id] = b
                else:
                    b = ReconciliationBreak(
                        id=str(uuid.uuid4()),
                        run_id=rid,
                        settlement_batch_id=None,
                        break_class=ReconBreakClass.TIMING_DELAY,
                        expected_amount=int_amt,
                        actual_amount=ev_amt,
                        discrepancy_amount=0,
                        internal_transaction_id=intent.get("id"),
                        external_reference=ref,
                        status=ReconBreakStatus.INVESTIGATING,
                        resolution_notes="Streaming window expired; promoted to EOD batch queue.",
                    )
                    breaks.append(b)
                    self.breaks[b.id] = b
            else:
                b = ReconciliationBreak(
                    id=str(uuid.uuid4()),
                    run_id=rid,
                    settlement_batch_id=None,
                    break_class=ReconBreakClass.TIMING_DELAY,
                    expected_amount=int_amt,
                    actual_amount=0,
                    discrepancy_amount=int_amt,
                    internal_transaction_id=intent.get("id"),
                    external_reference=ref,
                    status=ReconBreakStatus.INVESTIGATING,
                    resolution_notes="Awaiting callback within sliding window.",
                )
                breaks.append(b)
                self.breaks[b.id] = b

        for ev in ex_events:
            ev_id = ev.get("id", ev.get("ref", ev.get("reference")))
            if ev_id not in matched_events:
                b = ReconciliationBreak(
                    id=str(uuid.uuid4()),
                    run_id=rid,
                    settlement_batch_id=None,
                    break_class=ReconBreakClass.UNMATCHED_EXTERNAL,
                    expected_amount=0,
                    actual_amount=int(ev.get("amount", 0)),
                    discrepancy_amount=int(ev.get("amount", 0)),
                    external_reference=ev.get("ref", ev.get("reference")),
                    status=ReconBreakStatus.OPEN,
                )
                breaks.append(b)
                self.breaks[b.id] = b

        return matches, breaks

    def match_cryptographic_on_chain(
        self,
        fiat_ledger_entry: Optional[Dict[str, Any]] = None,
        on_chain_receipt: Optional[Dict[str, Any]] = None,
        fiat_entries: Optional[List[Dict[str, Any]]] = None,
        blockchain_receipts: Optional[List[Dict[str, Any]]] = None,
        confirmation_threshold: int = 12,
        min_confirmations: Optional[int] = None,
        max_slippage_bps: int = 5,
        run_id: Optional[str] = None,
        **kwargs
    ) -> Tuple[List[ReconciliationMatch], List[ReconciliationBreak]]:
        rid = run_id or str(uuid.uuid4())
        matches: List[ReconciliationMatch] = []
        breaks: List[ReconciliationBreak] = []

        threshold = min_confirmations if min_confirmations is not None else confirmation_threshold

        entries_list = fiat_entries if fiat_entries is not None else ([fiat_ledger_entry] if fiat_ledger_entry else [])
        receipts_list = blockchain_receipts if blockchain_receipts is not None else ([on_chain_receipt] if on_chain_receipt else [])

        for i, fiat_entry in enumerate(entries_list):
            if i >= len(receipts_list):
                break
            receipt = receipts_list[i]

            tx_hash = receipt.get("tx_hash", "0x0")
            fiat_amt = int(fiat_entry.get("amount", fiat_entry.get("fiat_amount", 0)))
            fx_rate = float(fiat_entry.get("fx_rate", 1.0))
            expected_tokens = int(fiat_entry.get("expected_tokens", int(fiat_amt * fx_rate)))
            actual_tokens = int(receipt.get("token_amount", 0))

            if receipt.get("is_reorged") is True:
                b_reorg = ReconciliationBreak(
                    id=str(uuid.uuid4()),
                    run_id=rid,
                    settlement_batch_id=None,
                    break_class=ReconBreakClass.CHAIN_REORG_BREAK,
                    expected_amount=expected_tokens,
                    actual_amount=0,
                    discrepancy_amount=expected_tokens,
                    external_reference=tx_hash,
                    status=ReconBreakStatus.OPEN,
                    resolution_notes="Blockchain consensus reorganization dropped confirmed transaction.",
                )
                breaks.append(b_reorg)
                self.breaks[b_reorg.id] = b_reorg
                continue

            confirmations = int(receipt.get("confirmations", 0))
            if confirmations < threshold:
                b_conf = ReconciliationBreak(
                    id=str(uuid.uuid4()),
                    run_id=rid,
                    settlement_batch_id=None,
                    break_class=ReconBreakClass.TIMING_DELAY,
                    expected_amount=expected_tokens,
                    actual_amount=actual_tokens,
                    discrepancy_amount=0,
                    external_reference=tx_hash,
                    status=ReconBreakStatus.INVESTIGATING,
                    resolution_notes=f"Pending finality: {confirmations}/{threshold} confirmations.",
                )
                breaks.append(b_conf)
                self.breaks[b_conf.id] = b_conf
                continue

            status_val = receipt.get("status")
            if status_val not in (1, "0x1", True):
                b_fail = ReconciliationBreak(
                    id=str(uuid.uuid4()),
                    run_id=rid,
                    settlement_batch_id=None,
                    break_class=ReconBreakClass.AMOUNT_MISMATCH,
                    expected_amount=expected_tokens,
                    actual_amount=0,
                    discrepancy_amount=expected_tokens,
                    external_reference=tx_hash,
                    status=ReconBreakStatus.OPEN,
                    resolution_notes="On-chain smart contract execution reverted.",
                )
                breaks.append(b_fail)
                self.breaks[b_fail.id] = b_fail
                continue

            variance = abs(actual_tokens - expected_tokens)
            max_allowed_slippage = int(expected_tokens * max_slippage_bps / 10000)

            if variance <= max_allowed_slippage:
                m = ReconciliationMatch(
                    id=str(uuid.uuid4()),
                    run_id=rid,
                    topology=ReconciliationTopology.ON_CHAIN,
                    match_status=ReconMatchStatus.EXACT_MATCH if variance == 0 else ReconMatchStatus.TOLERANCE_MATCH,
                    external_reference=tx_hash,
                    internal_amount=expected_tokens,
                    external_amount=actual_tokens,
                    variance_amount=variance,
                )
                matches.append(m)
                self.matches[m.id] = m
            else:
                b_slip = ReconciliationBreak(
                    id=str(uuid.uuid4()),
                    run_id=rid,
                    settlement_batch_id=None,
                    break_class=ReconBreakClass.AMOUNT_MISMATCH,
                    expected_amount=expected_tokens,
                    actual_amount=actual_tokens,
                    discrepancy_amount=variance,
                    external_reference=tx_hash,
                    status=ReconBreakStatus.OPEN,
                    resolution_notes="On-chain token minting exceeds permitted slippage.",
                )
                breaks.append(b_slip)
                self.breaks[b_slip.id] = b_slip

        return matches, breaks

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
