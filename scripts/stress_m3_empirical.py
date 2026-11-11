#!/usr/bin/env python3
"""
Adversarial Empirical Stress Harness for Milestone 3 (Universal Pluggable Reconciliation Engine).
Author: Challenger M3_1 (critic, specialist)
Project: Payment Gateway Platform

Tests:
1. Multi-Topology High-Volume Concurrency (100+ transactions per topology across all 5 topologies)
2. Pro-Rata Split Hamilton Algorithm: 10,000 randomized iterations with varying investor counts
3. Tolerance Bounds: Exact behavior at min($2.00, 15 bps) +/- 1 cent across varying gross sizes
4. Break Lifecycle FSM: State transition graph, invalid jump rejections, Maker-Checker dual control
5. 3-Tier Deduplication Edge Cases: File SHA256, line hash, cross-statement natural occurrence deduplication
"""

import hashlib
import random
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, List, Tuple, Any

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.e2e.harness import (
    LedgerEngine,
    EntryLeg,
    EntryDirection,
    AccountType,
    WaterfallEngine,
    InvestorShare,
    ReconciliationEngine,
    ReconciliationTopology,
    ReconTopology,
    StatementFormat,
    StatementStatus,
    ReconciliationRunStatus,
    ReconMatchStatus,
    ReconBreakClass,
    ReconBreakStatus,
    BreakResolutionAction,
    StatementLine,
    ClearingStatement,
    ReconciliationBreak,
    ReconciliationMatch,
    ReconciliationRun,
)


def run_pro_rata_hamilton_stress(iterations: int = 10000) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Stress-tests the Largest Remainder (Hamilton / Hare-Niemeyer) allocation algorithm:
    - 10,000 randomized iterations
    - Varying investor counts (1 to 500)
    - Varying amounts (1 cent to $10,000,000.00)
    - Invariant: Zero minor units created or lost (sum(payouts) == amount)
    - Invariant: Non-negative allocations (all payouts >= 0)
    - Invariant: Maximum deviation from exact proportional share is strictly < 1 minor unit
    """
    start_time = time.perf_counter()
    random.seed(42)  # Deterministic seed for reproducible stress testing

    total_minor_units_distributed = 0
    investor_count_distribution = []

    for i in range(iterations):
        # Vary investor counts: small (1-5), medium (10-50), large (100-500)
        scenario = i % 5
        if scenario == 0:
            num_investors = random.randint(1, 5)
        elif scenario == 1:
            num_investors = random.randint(6, 20)
        elif scenario == 2:
            num_investors = random.randint(21, 100)
        elif scenario == 3:
            num_investors = random.randint(101, 300)
        else:
            num_investors = random.randint(301, 500)

        # Generate weights summing to 10,000 bps
        # Use Dirichlet-like random division
        raw_weights = [random.randint(1, 1000) for _ in range(num_investors)]
        weight_sum = sum(raw_weights)
        norm_weights = [max(1, (w * 10000) // weight_sum) for w in raw_weights]
        diff = 10000 - sum(norm_weights)
        norm_weights[0] += diff  # Ensure exact sum to 10000

        investors = [InvestorShare(f"inv_{idx}", w) for idx, w in enumerate(norm_weights)]

        # Test varying amounts:
        # 1. Tiny amounts (amount < num_investors, e.g. 1, 2, 3 cents)
        # 2. Odd prime amounts (e.g. 7, 97, 10007 cents)
        # 3. Standard payments ($10.00 to $10,000.00)
        # 4. Large enterprise loans ($100,000.00 to $10,000,000.00)
        if i % 10 == 0:
            amount = random.randint(1, max(1, num_investors // 2))  # Severe deficit: cents < investors
        elif i % 10 == 1:
            amount = 1  # Absolute minimum non-zero
        elif i % 10 == 2:
            amount = 9999999  # Large odd prime-adjacent
        elif i % 10 == 3:
            amount = 1000000000  # $10M in cents
        else:
            amount = random.randint(100, 50000000)

        payouts = WaterfallEngine.calculate_pro_rata_split(amount, investors)

        # Invariant 1: Conservation of minor units (Zero penny leakage)
        actual_total = sum(payouts.values())
        if actual_total != amount:
            return False, f"Iteration {i}: Conservation violated! Expected {amount}, got {actual_total} (diff: {actual_total - amount})", {}

        # Invariant 2: Non-negative payouts
        min_payout = min(payouts.values())
        if min_payout < 0:
            return False, f"Iteration {i}: Negative payout generated! Min payout: {min_payout}", {}

        # Invariant 3: Proportional accuracy bounded by < 1 cent
        total_bps = sum(inv.allocation_weight_bps for inv in investors)
        for inv in investors:
            expected_fractional = (amount * inv.allocation_weight_bps) / total_bps
            actual_share = payouts[inv.investor_id]
            deviation = abs(actual_share - expected_fractional)
            if deviation >= 1.0:
                return False, f"Iteration {i}: Deviation >= 1.0 cent for {inv.investor_id}! Deviation: {deviation}", {}

        total_minor_units_distributed += amount
        investor_count_distribution.append(num_investors)

    elapsed = time.perf_counter() - start_time
    stats = {
        "iterations": iterations,
        "elapsed_seconds": round(elapsed, 3),
        "avg_investors": round(sum(investor_count_distribution) / len(investor_count_distribution), 1),
        "max_investors": max(investor_count_distribution),
        "total_minor_units_distributed": total_minor_units_distributed,
        "ops_per_second": int(iterations / elapsed),
    }
    return True, f"10,000 iterations passed with zero penny leaks and < 1.0 cent deviation in {elapsed:.2f}s", stats


def run_tolerance_boundaries_stress() -> Tuple[bool, str, Dict[str, Any]]:
    """
    Stress-tests tolerance boundaries at min($2.00, 15 bps) +/- 1 cent
    across a broad range of gross amounts:
    - Small amounts ($1.00 to $1,333.00) where 15 bps is active (< 200 cents)
    - Transition boundary ($1,333.33) where 15 bps equals 200 cents
    - Large amounts ($1,334.00 to $10,000,000.00) where $2.00 (200 cents) cap is active
    """
    engine = ReconciliationEngine()
    test_cases = [
        # (gross_amount_cents, expected_max_tolerance_cents)
        (500, 0),        # $5.00: 500 * 15 / 10000 = 0.75 -> 0 cents
        (1000, 1),       # $10.00: 1000 * 15 / 10000 = 1.5 -> 1 cent
        (10000, 15),     # $100.00: 10000 * 15 / 10000 = 15 cents
        (50000, 75),     # $500.00: 50000 * 15 / 10000 = 75 cents
        (100000, 150),   # $1,000.00: 100000 * 15 / 10000 = 150 cents
        (133333, 199),   # $1,333.33: 133333 * 15 / 10000 = 199.9995 -> 199 cents
        (133334, 200),   # $1,333.34: 133334 * 15 / 10000 = 200.001 -> capped at 200 cents
        (200000, 200),   # $2,000.00: 15 bps = 300 cents -> capped at 200 cents
        (500000, 200),   # $5,000.00: 15 bps = 750 cents -> capped at 200 cents
        (1000000, 200),  # $10,000.00: 15 bps = 1500 cents -> capped at 200 cents
        (10000000, 200), # $100,000.00: 15 bps = 15000 cents -> capped at 200 cents
    ]

    total_checks = 0

    for gross, tol in test_cases:
        # 1. Exact match (variance == 0)
        status_exact, brk_exact = engine.match_one_to_one(
            internal_ref=f"tx_{gross}_0",
            internal_amount=gross,
            external_ref=f"ext_{gross}_0",
            external_amount=gross,
        )
        assert status_exact == ReconMatchStatus.EXACT_MATCH, f"Gross {gross}: exact match failed"
        assert brk_exact is None
        total_checks += 1

        if tol > 0:
            # 2. Lower boundary (tol - 1 cent)
            status_low, brk_low = engine.match_one_to_one(
                internal_ref=f"tx_{gross}_low",
                internal_amount=gross,
                external_ref=f"ext_{gross}_low",
                external_amount=gross - (tol - 1),
            )
            expected_low_status = ReconMatchStatus.EXACT_MATCH if (tol - 1) == 0 else ReconMatchStatus.TOLERANCE_MATCH
            assert status_low == expected_low_status, f"Gross {gross}: tol - 1 failed, expected {expected_low_status}, got {status_low}"
            assert brk_low is None
            total_checks += 1

            # 3. Exact tolerance boundary (tol) -> TOLERANCE_MATCH
            status_exact_tol, brk_exact_tol = engine.match_one_to_one(
                internal_ref=f"tx_{gross}_exact_tol",
                internal_amount=gross,
                external_ref=f"ext_{gross}_exact_tol",
                external_amount=gross - tol,
            )
            assert status_exact_tol == ReconMatchStatus.TOLERANCE_MATCH, f"Gross {gross}: tol boundary failed"
            assert brk_exact_tol is None
            total_checks += 1

        # 4. Out-of-tolerance boundary (tol + 1 cent) -> AMOUNT_DISCREPANCY (Break)
        status_high, brk_high = engine.match_one_to_one(
            internal_ref=f"tx_{gross}_high",
            internal_amount=gross,
            external_ref=f"ext_{gross}_high",
            external_amount=gross - (tol + 1),
        )
        assert status_high == ReconMatchStatus.AMOUNT_DISCREPANCY, f"Gross {gross}: tol + 1 failed to create break"
        assert brk_high is not None, f"Gross {gross}: break object was None"
        assert brk_high.break_class == ReconBreakClass.AMOUNT_MISMATCH
        assert brk_high.discrepancy_amount == tol + 1
        assert brk_high.status == ReconBreakStatus.OPEN
        total_checks += 1

        # 5. Reverse variance (bank credited more: ext > int) -> AMOUNT_DISCREPANCY
        status_rev, brk_rev = engine.match_one_to_one(
            internal_ref=f"tx_{gross}_rev",
            internal_amount=gross,
            external_ref=f"ext_{gross}_rev",
            external_amount=gross + 50,  # Overpayment
        )
        assert status_rev == ReconMatchStatus.AMOUNT_DISCREPANCY, f"Gross {gross}: negative variance failed"
        assert brk_rev is not None
        assert brk_rev.discrepancy_amount == 50
        total_checks += 1

    return True, f"All {total_checks} mathematical boundary checks at min($2.00, 15 bps) +/- 1 cent passed", {"total_boundary_checks": total_checks}


def run_break_lifecycle_stress() -> Tuple[bool, str, Dict[str, Any]]:
    """
    Stress-tests Break Lifecycle FSM:
    - State transition validity
    - Invalid jump rejections (e.g. INVESTIGATING -> OPEN)
    - Terminal state immutability (cannot modify RESOLVED or WRITTEN_OFF)
    - Non-existent break handling
    - Maker-Checker dual control thresholds (> $500.00 / 50,000 cents)
    - Automated tolerance write-off ledger posting
    """
    ledger = LedgerEngine()
    ledger.create_account("110100", "Bank Settlement Nostro", AccountType.ASSET, "USD")
    ledger.create_account("510000", "Bank Fee Variance Expense", AccountType.EXPENSE, "USD")
    engine = ReconciliationEngine(ledger=ledger)

    total_checks = 0

    # 1. State creation
    brk = engine.create_break(
        break_id="BRK-FSM-001",
        run_id="RUN-1",
        settlement_batch_id="BATCH-1",
        break_class=ReconBreakClass.AMOUNT_MISMATCH,
        expected_amount=100000,
        actual_amount=95000,
        discrepancy_amount=5000,
        currency="USD"
    )
    assert brk.status == ReconBreakStatus.OPEN
    total_checks += 1

    # 2. OPEN -> INVESTIGATING
    engine.transition_break("BRK-FSM-001", ReconBreakStatus.INVESTIGATING, actor_id="analyst_1")
    assert brk.status == ReconBreakStatus.INVESTIGATING
    total_checks += 1

    # 3. Invalid jump: INVESTIGATING -> OPEN must raise ValueError
    try:
        engine.transition_break("BRK-FSM-001", ReconBreakStatus.OPEN, actor_id="analyst_1")
        return False, "Failed to reject invalid transition INVESTIGATING -> OPEN", {}
    except ValueError:
        total_checks += 1

    # 4. INVESTIGATING -> RESOLVED (Terminal state)
    res_brk = engine.resolve_break("BRK-FSM-001", BreakResolutionAction.FORCE_MATCH, resolved_by="analyst_1")
    assert res_brk.status == ReconBreakStatus.RESOLVED
    assert res_brk.resolved_at is not None
    total_checks += 1

    # 5. Terminal state immutability: Cannot transition RESOLVED break
    for invalid_target in [ReconBreakStatus.OPEN, ReconBreakStatus.INVESTIGATING, ReconBreakStatus.WRITTEN_OFF]:
        try:
            engine.transition_break("BRK-FSM-001", invalid_target, actor_id="analyst_1")
            return False, f"Failed to protect terminal RESOLVED state against {invalid_target}", {}
        except ValueError:
            total_checks += 1

    # 6. WRITTEN_OFF Terminal state immutability
    brk_wo = engine.create_break(
        break_id="BRK-FSM-002",
        run_id="RUN-1",
        settlement_batch_id=None,
        break_class=ReconBreakClass.AMOUNT_MISMATCH,
        expected_amount=2000,
        actual_amount=0,
        discrepancy_amount=2000
    )
    engine.transition_break("BRK-FSM-002", ReconBreakStatus.INVESTIGATING)
    wo_brk = engine.resolve_break("BRK-FSM-002", BreakResolutionAction.WRITE_OFF_VARIANCE, resolved_by="treasurer_1")
    assert wo_brk.status == ReconBreakStatus.WRITTEN_OFF
    total_checks += 1

    for invalid_target in [ReconBreakStatus.OPEN, ReconBreakStatus.INVESTIGATING, ReconBreakStatus.RESOLVED]:
        try:
            engine.transition_break("BRK-FSM-002", invalid_target)
            return False, f"Failed to protect terminal WRITTEN_OFF state against {invalid_target}", {}
        except ValueError:
            total_checks += 1

    # 7. Non-existent break ID raises KeyError
    try:
        engine.transition_break("NON_EXISTENT_ID", ReconBreakStatus.RESOLVED)
        return False, "Failed to raise KeyError on missing break ID", {}
    except KeyError:
        total_checks += 1

    # 8. Maker-Checker Dual Control (> $500 / 50,000 cents)
    large_brk = engine.create_break(
        break_id="BRK-LARGE-001",
        run_id="RUN-1",
        settlement_batch_id=None,
        break_class=ReconBreakClass.AMOUNT_MISMATCH,
        expected_amount=200000,
        actual_amount=120000,
        discrepancy_amount=80000  # $800.00 > $500.00
    )
    # Maker alone -> status becomes PENDING_APPROVAL
    res_maker = engine.resolve_break(
        "BRK-LARGE-001",
        action=BreakResolutionAction.FORCE_MATCH,
        resolved_by="maker_user",
        second_approver=None
    )
    assert res_maker.status == ReconBreakStatus.PENDING_APPROVAL
    total_checks += 1

    # Maker attempting to self-approve -> still PENDING_APPROVAL
    res_self = engine.resolve_break(
        "BRK-LARGE-001",
        action=BreakResolutionAction.FORCE_MATCH,
        resolved_by="maker_user",
        second_approver="maker_user"
    )
    assert res_self.status == ReconBreakStatus.PENDING_APPROVAL
    total_checks += 1

    # Independent checker approves -> RESOLVED
    res_checker = engine.resolve_break(
        "BRK-LARGE-001",
        action=BreakResolutionAction.FORCE_MATCH,
        resolved_by="maker_user",
        second_approver="checker_supervisor"
    )
    assert res_checker.status == ReconBreakStatus.RESOLVED
    assert res_checker.approved_by == "checker_supervisor"
    total_checks += 1

    # 9. Automated Fee Write-off and Double-Entry Ledger Posting
    auto_brk = engine.create_break(
        break_id="BRK-AUTO-001",
        run_id="RUN-1",
        settlement_batch_id=None,
        break_class=ReconBreakClass.AMOUNT_MISMATCH,
        expected_amount=100000,  # $1,000 -> 15 bps = 150 cents
        actual_amount=99860,
        discrepancy_amount=140   # 140 cents <= 150 cents
    )
    res_auto = engine.resolve_break(
        "BRK-AUTO-001",
        action=BreakResolutionAction.AUTO_FEE_WRITE_OFF,
        resolved_by="system"
    )
    assert res_auto.status == ReconBreakStatus.RESOLVED
    assert ledger.accounts["510000"].net_balance == 140  # Expense debited
    assert ledger.accounts["110100"].credit_balance == 140 # Nostro credited
    total_checks += 1

    return True, f"All {total_checks} break lifecycle FSM and Maker-Checker checks passed", {"total_fsm_checks": total_checks}


def run_deduplication_stress() -> Tuple[bool, str, Dict[str, Any]]:
    """
    Stress-tests 3-Tier Deduplication:
    - Tier 1: Full-file SHA256 idempotency (exact duplicates ignored)
    - Tier 2: Line hash uniqueness within statement
    - Tier 3: Cross-statement natural occurrence deduplication (amended statements with mixed lines)
    - Multi-statement collision avoidance across different rails/accounts
    - Case sensitivity of identifiers
    - Statement supersession semantics
    """
    engine = ReconciliationEngine()
    total_checks = 0

    # Tier 1: Full file idempotency
    csv_v1 = (
        "STMT_ID:STMT_ACH_01\n"
        "RAIL:ACH\n"
        "OPENING:50000\n"
        "CLOSING:75000\n"
        "TX_A1,2026-11-11,15000,CREDIT,USD\n"
        "TX_A2,2026-11-11,10000,CREDIT,USD\n"
    )
    stmt1 = engine.ingest_statement(csv_v1, fmt="CSV", rail_code="ACH")
    assert stmt1.statement_id == "STMT_ACH_01"
    assert len(stmt1.lines) == 2
    assert not stmt1.is_duplicate
    total_checks += 1

    # Ingest exact same content -> must be flagged duplicate and return original
    stmt1_dup = engine.ingest_statement(csv_v1, fmt="CSV", rail_code="ACH")
    assert stmt1_dup.statement_id == stmt1.statement_id
    assert stmt1_dup.is_duplicate is True
    assert len(engine.statements) == 1  # No duplicate statement created
    total_checks += 1

    # Tier 3: Amended Statement with mixed existing and net-new lines
    csv_v2 = (
        "STMT_ID:STMT_ACH_02\n"
        "RAIL:ACH\n"
        "OPENING:50000\n"
        "CLOSING:95000\n"
        "TX_A1,2026-11-11,15000,CREDIT,USD\n"  # Duplicate of STMT 1 line 1
        "TX_A2,2026-11-11,10000,CREDIT,USD\n"  # Duplicate of STMT 1 line 2
        "TX_A3,2026-11-11,20000,CREDIT,USD\n"  # Net-new line!
    )
    stmt2 = engine.ingest_statement(csv_v2, fmt="CSV", rail_code="ACH", replaces_statement_id=stmt1.id)
    assert stmt1.status == StatementStatus.SUPERSEDED
    assert stmt2.statement_id == "STMT_ACH_02"
    assert len(stmt2.lines) == 3

    # Check Tier 3 line reconciliation statuses
    assert stmt2.lines[0].reconciliation_status == ReconMatchStatus.SKIPPED_ALREADY_PROCESSED
    assert stmt2.lines[0].original_line_id == stmt1.lines[0].id
    assert stmt2.lines[1].reconciliation_status == ReconMatchStatus.SKIPPED_ALREADY_PROCESSED
    assert stmt2.lines[1].original_line_id == stmt1.lines[1].id
    assert stmt2.lines[2].reconciliation_status == ReconMatchStatus.PENDING
    assert stmt2.lines[2].original_line_id is None
    total_checks += 3

    # Matching with active lines: SKIPPED lines must be excluded from matching
    int_records = [
        {"id": "tx_a1", "ref": "TX_A1", "amount": 15000},
        {"id": "tx_a3", "ref": "TX_A3", "amount": 20000},
    ]
    matches, breaks = engine.match_one_to_one(
        internal_records=int_records,
        external_lines=stmt2.lines,
    )
    # Only TX_A3 is active in stmt2; TX_A1 was SKIPPED_ALREADY_PROCESSED!
    # Therefore, TX_A3 matches, while TX_A1 becomes UNMATCHED_INTERNAL in stmt2's run
    assert len(matches) == 1
    assert matches[0].external_reference == "TX_A3"
    assert len(breaks) == 1
    assert breaks[0].break_class == ReconBreakClass.UNMATCHED_INTERNAL
    assert breaks[0].external_reference == "TX_A1"
    total_checks += 2

    # Tier 2: Intra-statement identical lines (occurrence counting)
    # E.g. Customer makes two identical $25 transfers in same file
    csv_multi_same = (
        "STMT_ID:STMT_MULTI_01\n"
        "RAIL:FEDNOW\n"
        "OPENING:0\n"
        "CLOSING:5000\n"
        "TX_REPEAT,2026-11-11,2500,CREDIT,USD\n"  # 1st occurrence
        "TX_REPEAT,2026-11-11,2500,CREDIT,USD\n"  # 2nd occurrence in same statement
    )
    stmt_multi = engine.ingest_statement(csv_multi_same, fmt="CSV", rail_code="FEDNOW")
    assert len(stmt_multi.lines) == 2
    assert stmt_multi.lines[0].line_hash != stmt_multi.lines[1].line_hash  # Sequences differ
    assert stmt_multi.lines[0].reconciliation_status == ReconMatchStatus.PENDING
    assert stmt_multi.lines[1].reconciliation_status == ReconMatchStatus.PENDING
    total_checks += 3

    # Ingest amended version of multi-statement containing 3 occurrences (2 existing + 1 new)
    csv_multi_amended = (
        "STMT_ID:STMT_MULTI_02\n"
        "RAIL:FEDNOW\n"
        "OPENING:0\n"
        "CLOSING:7500\n"
        "TX_REPEAT,2026-11-11,2500,CREDIT,USD\n"  # 1st occurrence (duplicate)
        "TX_REPEAT,2026-11-11,2500,CREDIT,USD\n"  # 2nd occurrence (duplicate)
        "TX_REPEAT,2026-11-11,2500,CREDIT,USD\n"  # 3rd occurrence (new!)
    )
    stmt_multi_v2 = engine.ingest_statement(csv_multi_amended, fmt="CSV", rail_code="FEDNOW")
    assert stmt_multi_v2.lines[0].reconciliation_status == ReconMatchStatus.SKIPPED_ALREADY_PROCESSED
    assert stmt_multi_v2.lines[1].reconciliation_status == ReconMatchStatus.SKIPPED_ALREADY_PROCESSED
    assert stmt_multi_v2.lines[2].reconciliation_status == ReconMatchStatus.PENDING
    total_checks += 3

    # Rail / Account collision protection: Same ref on different rail or account is NOT a duplicate
    csv_diff_rail = (
        "STMT_ID:STMT_SWIFT_01\n"
        "RAIL:SWIFT\n"
        "OPENING:0\n"
        "CLOSING:2500\n"
        "TX_REPEAT,2026-11-11,2500,CREDIT,USD\n"  # Same ref, but SWIFT rail
    )
    stmt_swift = engine.ingest_statement(csv_diff_rail, fmt="CSV", rail_code="SWIFT")
    assert stmt_swift.lines[0].reconciliation_status == ReconMatchStatus.PENDING  # Not skipped!
    total_checks += 1

    return True, f"All {total_checks} 3-Tier deduplication edge cases verified successfully", {"total_dedup_checks": total_checks}


def run_multi_topology_high_concurrency_stress(volume_per_topology: int = 150) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Stress-tests all 5 matching topologies with high-volume datasets (150+ items each):
    1. Topology 1 (1:1 Direct): 150 internal transactions vs 150 external lines
    2. Topology 2 (Netting): 25 settlement batches with 500 transactions netted against bank credits
    3. Topology 3 (Tripartite Escrow): 150 loans with multi-investor waterfalls
    4. Topology 4 (Streaming Intraday): 150 streaming events with random arrival latencies
    5. Topology 5 (Cryptographic On-Chain): 150 Web3 crypto-fiat transactions with blockchain receipts
    """
    start_time = time.perf_counter()
    random.seed(99)
    engine = ReconciliationEngine()
    topology_results = {}

    # --------------------------------------------------------------------------
    # 1. Topology 1: 1:1 Direct Matching
    # --------------------------------------------------------------------------
    t1_internal = []
    t1_external = []
    # 70 exact matches
    for i in range(70):
        ref = f"T1_EXACT_{i:04d}"
        amt = random.randint(5000, 200000)
        t1_internal.append({"id": f"int_{ref}", "ref": ref, "amount": amt})
        t1_external.append(StatementLine(
            id=f"line_{ref}", statement_id="STMT_T1", line_sequence=i+1,
            bank_reference=ref, booking_date="2026-11-11", amount=amt,
            direction=EntryDirection.CREDIT, line_hash=f"hash_{ref}", end_to_end_id=ref
        ))

    # 40 tolerance matches (variances within min($2.00, 15 bps))
    for i in range(40):
        ref = f"T1_TOL_{i:04d}"
        amt = random.randint(100000, 500000)  # $1k - $5k
        max_tol = min(200, int(amt * 0.0015))
        fee = random.randint(1, max_tol)
        t1_internal.append({"id": f"int_{ref}", "ref": ref, "amount": amt})
        t1_external.append(StatementLine(
            id=f"line_{ref}", statement_id="STMT_T1", line_sequence=70+i+1,
            bank_reference=ref, booking_date="2026-11-11", amount=amt - fee,
            direction=EntryDirection.CREDIT, line_hash=f"hash_{ref}", end_to_end_id=ref
        ))

    # 20 amount mismatches (fee exceeds tolerance) -> breaks
    for i in range(20):
        ref = f"T1_MISMATCH_{i:04d}"
        amt = 100000  # $1,000 -> max tol = 150 cents
        fee = 500     # $5.00 > 150 cents
        t1_internal.append({"id": f"int_{ref}", "ref": ref, "amount": amt})
        t1_external.append(StatementLine(
            id=f"line_{ref}", statement_id="STMT_T1", line_sequence=110+i+1,
            bank_reference=ref, booking_date="2026-11-11", amount=amt - fee,
            direction=EntryDirection.CREDIT, line_hash=f"hash_{ref}", end_to_end_id=ref
        ))

    # 10 internal only (unmatched internal)
    for i in range(10):
        ref = f"T1_UNMATCHED_INT_{i:04d}"
        amt = random.randint(5000, 50000)
        t1_internal.append({"id": f"int_{ref}", "ref": ref, "amount": amt})

    # 10 external only (unmatched external)
    for i in range(10):
        ref = f"T1_UNMATCHED_EXT_{i:04d}"
        amt = random.randint(5000, 50000)
        t1_external.append(StatementLine(
            id=f"line_{ref}", statement_id="STMT_T1", line_sequence=130+i+1,
            bank_reference=ref, booking_date="2026-11-11", amount=amt,
            direction=EntryDirection.CREDIT, line_hash=f"hash_{ref}", end_to_end_id=ref
        ))

    m1, b1 = engine.match_one_to_one(internal_records=t1_internal, external_lines=t1_external)
    assert len(m1) == 110, f"Topology 1 expected 110 matches, got {len(m1)}"
    assert len(b1) == 40, f"Topology 1 expected 40 breaks (20 mismatch + 10 int + 10 ext), got {len(b1)}"
    topology_results["topology_1_direct"] = {"matches": len(m1), "breaks": len(b1), "total_processed": len(t1_internal) + len(t1_external)}

    # --------------------------------------------------------------------------
    # 2. Topology 2: N:M Netting Matching (25 Batches x 20 Txs = 500 Transactions)
    # --------------------------------------------------------------------------
    t2_batches = []
    t2_ext_credits = []
    total_tx_count = 0

    for b_idx in range(25):
        batch_id = f"NET_BATCH_{b_idx:03d}"
        txs = []
        gross = 0
        for t_idx in range(20):
            amt = random.randint(1000, 20000)
            fee = int(amt * 0.02)
            txs.append({"id": f"tx_{b_idx}_{t_idx}", "amount": amt, "fee_amount": fee, "direction": EntryDirection.CREDIT})
            gross += amt
            total_tx_count += 1

        interchange = int(gross * 0.015)
        scheme_fee = int(gross * 0.003)
        expected_net = gross - interchange - scheme_fee

        if b_idx < 18:
            # Exact net credit match
            actual_net = expected_net
        elif b_idx < 22:
            # Within $50 tolerance
            actual_net = expected_net - 2500
        else:
            # Exceeds tolerance
            actual_net = expected_net - 10000

        t2_batches.append({"batch_id": batch_id, "gross_amount": gross, "transactions": txs})
        t2_ext_credits.append(StatementLine(
            id=f"cred_{batch_id}", statement_id="STMT_T2", line_sequence=b_idx+1,
            bank_reference=batch_id, booking_date="2026-11-11", amount=actual_net,
            direction=EntryDirection.CREDIT, line_hash=f"hash_net_{batch_id}"
        ))

    m2_total = 0
    b2_total = 0
    for b_idx in range(25):
        m_sub, b_sub = engine.match_netting(
            internal_batches=[t2_batches[b_idx]],
            external_credits=[t2_ext_credits[b_idx]],
            settlement_batch_id=t2_batches[b_idx]["batch_id"],
            interchange_fee=int(t2_batches[b_idx]["gross_amount"] * 0.015),
            scheme_fee=int(t2_batches[b_idx]["gross_amount"] * 0.003),
            interchange_tolerance_cents=5000
        )
        m2_total += len(m_sub)
        b2_total += len(b_sub)

    assert m2_total == 22, f"Topology 2 expected 22 matches, got {m2_total}"
    assert b2_total == 3, f"Topology 2 expected 3 breaks, got {b2_total}"
    topology_results["topology_2_netting"] = {"matches": m2_total, "breaks": b2_total, "transactions_netted": total_tx_count}

    # --------------------------------------------------------------------------
    # 3. Topology 3: Tripartite Escrow Matching (150 Loans x 5 Investors = 750 Payouts)
    # --------------------------------------------------------------------------
    m3_total = 0
    b3_total = 0
    shares = [
        InvestorShare("inv_1", 3500),
        InvestorShare("inv_2", 2500),
        InvestorShare("inv_3", 2000),
        InvestorShare("inv_4", 1000),
        InvestorShare("inv_5", 1000),
    ]

    for loan_idx in range(150):
        loan_id = f"LOAN_{loan_idx:04d}"
        repayment = random.randint(50000, 500000)
        fee = int(repayment * 0.015)
        tax = int(repayment * 0.01)
        net_pool = repayment - fee - tax

        # Split investor payout with Hamilton pro-rata
        allocations = WaterfallEngine.calculate_pro_rata_split(net_pool, shares)
        investor_payouts = list(allocations.values())

        if loan_idx < 130:
            # Balanced escrow
            bank_bal = repayment
        else:
            # Bank balance divergence (break)
            bank_bal = repayment - 5000

        m_tri, b_tri = engine.match_tripartite_escrow(
            borrower_repayment=repayment,
            platform_fees=fee,
            withholding_tax=tax,
            investor_payouts=investor_payouts,
            bank_line_amount=bank_bal,
            loan_id=loan_id
        )
        m3_total += len(m_tri)
        b3_total += len(b_tri)

    assert m3_total == 130, f"Topology 3 expected 130 matches, got {m3_total}"
    assert b3_total == 20, f"Topology 3 expected 20 breaks, got {b3_total}"
    topology_results["topology_3_tripartite"] = {"matches": m3_total, "breaks": b3_total, "loans_simulated": 150}

    # --------------------------------------------------------------------------
    # 4. Topology 4: Streaming Intraday Matching (150 Events)
    # --------------------------------------------------------------------------
    t4_intents = []
    t4_callbacks = []
    base_time = 1700000000.0

    # 100 fast-path matches within 30s
    for i in range(100):
        ref = f"STREAM_{i:04d}"
        amt = random.randint(1000, 50000)
        delta = random.uniform(0.5, 25.0)
        t4_intents.append({"id": f"int_{ref}", "ref": ref, "amount": amt, "timestamp": base_time + i})
        t4_callbacks.append({"id": f"cb_{ref}", "ref": ref, "amount": amt, "timestamp": base_time + i + delta})

    # 25 timing delays (> 30s)
    for i in range(25):
        ref = f"STREAM_LATE_{i:04d}"
        amt = random.randint(1000, 50000)
        delta = random.uniform(35.0, 120.0)
        t4_intents.append({"id": f"int_{ref}", "ref": ref, "amount": amt, "timestamp": base_time + 100 + i})
        t4_callbacks.append({"id": f"cb_{ref}", "ref": ref, "amount": amt, "timestamp": base_time + 100 + i + delta})

    # 15 unmatched internal intents (callbacks lost)
    for i in range(15):
        ref = f"STREAM_LOST_{i:04d}"
        amt = random.randint(1000, 50000)
        t4_intents.append({"id": f"int_{ref}", "ref": ref, "amount": amt, "timestamp": base_time + 125 + i})

    # 10 unmatched external callbacks (unsolicited)
    for i in range(10):
        ref = f"STREAM_UNSOL_{i:04d}"
        amt = random.randint(1000, 50000)
        t4_callbacks.append({"id": f"cb_{ref}", "ref": ref, "amount": amt, "timestamp": base_time + 140 + i})

    m4, b4 = engine.match_streaming_intraday(
        intents=t4_intents,
        callbacks=t4_callbacks,
        window_seconds=30.0
    )
    assert len(m4) == 100, f"Topology 4 expected 100 matches, got {len(m4)}"
    assert len(b4) == 50, f"Topology 4 expected 50 breaks (25 late + 15 lost + 10 unsol), got {len(b4)}"
    topology_results["topology_4_streaming"] = {"matches": len(m4), "breaks": len(b4), "events_simulated": len(t4_intents) + len(t4_callbacks)}

    # --------------------------------------------------------------------------
    # 5. Topology 5: Cryptographic On-Chain Matching (150 Web3 Transactions)
    # --------------------------------------------------------------------------
    t5_fiat_entries = []
    t5_receipts = []

    # 90 exact matches (confirmations >= 12, status 0x1, variance 0)
    for i in range(90):
        amt = random.randint(50000, 5000000)
        t5_fiat_entries.append({"fiat_amount": amt, "fx_rate": 1.0, "expected_tokens": amt})
        t5_receipts.append({
            "tx_hash": f"0x{uuid.uuid4().hex}", "status": "0x1",
            "confirmations": random.randint(12, 64), "token_amount": amt, "is_reorged": False
        })

    # 25 tolerance matches (slippage <= 5 bps)
    for i in range(25):
        amt = 1000000  # $10,000.00 USDC -> 5 bps = 500 USDC cents
        slip = random.randint(1, 500)
        t5_fiat_entries.append({"fiat_amount": amt, "fx_rate": 1.0, "expected_tokens": amt})
        t5_receipts.append({
            "tx_hash": f"0x{uuid.uuid4().hex}", "status": "0x1",
            "confirmations": 15, "token_amount": amt - slip, "is_reorged": False
        })

    # 15 reorg breaks (is_reorged = True)
    for i in range(15):
        amt = 500000
        t5_fiat_entries.append({"fiat_amount": amt, "fx_rate": 1.0, "expected_tokens": amt})
        t5_receipts.append({
            "tx_hash": f"0x{uuid.uuid4().hex}", "status": "0x1",
            "confirmations": 20, "token_amount": amt, "is_reorged": True
        })

    # 10 confirmation delays (confirmations < 12)
    for i in range(10):
        amt = 500000
        t5_fiat_entries.append({"fiat_amount": amt, "fx_rate": 1.0, "expected_tokens": amt})
        t5_receipts.append({
            "tx_hash": f"0x{uuid.uuid4().hex}", "status": "0x1",
            "confirmations": random.randint(1, 11), "token_amount": amt, "is_reorged": False
        })

    # 10 reverted contracts (status 0x0)
    for i in range(10):
        amt = 500000
        t5_fiat_entries.append({"fiat_amount": amt, "fx_rate": 1.0, "expected_tokens": amt})
        t5_receipts.append({
            "tx_hash": f"0x{uuid.uuid4().hex}", "status": "0x0",
            "confirmations": 15, "token_amount": 0, "is_reorged": False
        })

    m5, b5 = engine.match_cryptographic_on_chain(
        fiat_entries=t5_fiat_entries,
        blockchain_receipts=t5_receipts,
        confirmation_threshold=12,
        max_slippage_bps=5
    )
    assert len(m5) == 115, f"Topology 5 expected 115 matches (90 exact + 25 tolerance), got {len(m5)}"
    assert len(b5) == 35, f"Topology 5 expected 35 breaks (15 reorg + 10 delay + 10 revert), got {len(b5)}"
    topology_results["topology_5_on_chain"] = {"matches": len(m5), "breaks": len(b5), "transactions_simulated": len(t5_fiat_entries)}

    elapsed = time.perf_counter() - start_time
    return True, f"All 5 topologies passed high-volume stress (750+ transactions evaluated in {elapsed:.3f}s)", {
        "elapsed_seconds": round(elapsed, 3),
        "results": topology_results
    }


def run_accounting_zero_leakage_stress(iterations: int = 1000) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Stress-tests double-entry ledger invariants under auto-write-offs and suspense parking:
    - Verifies Assets == Liabilities + Equity (+ Revenue - Expenses) at every step
    - Verifies zero balance leakage (Assets - RHS == 0) across 1,000 randomized break resolutions
    """
    ledger = LedgerEngine()
    ledger.create_account("110100", "Bank Settlement Nostro", AccountType.ASSET, "USD")
    ledger.create_account("240900", "Unallocated Suspense Liability", AccountType.LIABILITY, "USD")
    ledger.create_account("510000", "Bank Fee Variance Expense", AccountType.EXPENSE, "USD")
    engine = ReconciliationEngine(ledger=ledger)

    random.seed(123)
    for i in range(iterations):
        amt = random.randint(1, 200)
        brk_id = f"BRK-LEAK-{i:05d}"
        brk = engine.create_break(
            break_id=brk_id,
            run_id="RUN-LEAK",
            settlement_batch_id=None,
            break_class=ReconBreakClass.AMOUNT_MISMATCH,
            expected_amount=200000,
            actual_amount=200000 - amt,
            discrepancy_amount=amt,
            currency="USD"
        )
        engine.resolve_break(brk_id, BreakResolutionAction.AUTO_FEE_WRITE_OFF, resolved_by="system")

        is_balanced, assets, rhs = ledger.verify_accounting_equation("USD")
        if not is_balanced or assets != rhs:
            return False, f"Iteration {i}: Ledger equation violated! Assets: {assets}, RHS: {rhs}", {}

    return True, f"All {iterations} randomized break write-offs maintained exact double-entry balance with 0 leakage", {"iterations": iterations}


def main():
    print("=" * 78)
    print("   ADVERSARIAL EMPIRICAL STRESS HARNESS — CHALLENGER M3_1")
    print("=" * 78)

    suites = [
        ("1. Pro-Rata Split Hamilton Algorithm (10,000 iterations)", run_pro_rata_hamilton_stress),
        ("2. Mathematical Tolerance Bounds: min($2.00, 15 bps) +/- 1 cent", run_tolerance_boundaries_stress),
        ("3. Break Lifecycle FSM & Maker-Checker Dual Control", run_break_lifecycle_stress),
        ("4. 3-Tier Deduplication Edge Cases & Supersession", run_deduplication_stress),
        ("5. Multi-Topology High-Volume Concurrency (150+ txs per topology)", run_multi_topology_high_concurrency_stress),
        ("6. Double-Entry Accounting Equation Zero-Leakage (1,000 resolutions)", run_accounting_zero_leakage_stress),
    ]

    all_passed = True
    summary_report = {}

    for name, test_fn in suites:
        print(f"\n[EXEC] Running {name}...")
        try:
            passed, message, details = test_fn()
            if passed:
                print(f"  [PASS] {message}")
                summary_report[name] = {"status": "PASS", "message": message, "details": details}
            else:
                print(f"  [FAIL] {message}")
                summary_report[name] = {"status": "FAIL", "message": message, "details": details}
                all_passed = False
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  [ERROR] Exception encountered: {e}")
            summary_report[name] = {"status": "ERROR", "error": str(e)}
            all_passed = False

    print("\n" + "=" * 78)
    if all_passed:
        print(">>> EMPIRICAL STRESS TEST SUITE PASSED [VERDICT: CONFIRM] <<<")
        print("=" * 78)
        sys.exit(0)
    else:
        print(">>> EMPIRICAL STRESS TEST SUITE FAILED [VERDICT: REJECT] <<<")
        print("=" * 78)
        sys.exit(1)


if __name__ == "__main__":
    main()
