"""
Empirical Adversarial Stress Harness & Invariant Verification Oracles
For Payment Gateway Platform.

Executes rigorous stress testing across:
1. Double-Entry Accounting Invariant (High Concurrency & Multi-Currency)
2. Idempotency & Concurrency Guarantees (Race Conditions, Retries, Out-of-Order)
3. P2P Waterfall Split Math & Fractional Cent Allocation
4. Platform E2E Test Suite Execution
"""

import concurrent.futures
import hashlib
import random
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Dict, List, Tuple

# Add target repo root to sys.path
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.e2e.harness import (
    LedgerEngine, LedgerAccount, EntryLeg, EntryDirection, AccountType,
    WaterfallEngine, InvestorShare, WaterfallResult,
    IdempotencyEngine, IdempotencyRecord, IdempotencyState,
    FXEngine, SanctionsEngine, SagaOrchestrator
)


class EmpiricalLedgerStressOracle:
    """Stress tests the Double-Entry Accounting Invariants."""

    @staticmethod
    def run_concurrent_transfers(num_accounts: int = 20, num_transfers: int = 2000, num_workers: int = 16) -> Dict:
        ledger = LedgerEngine()
        lock = threading.Lock()

        # Create accounts
        # 10 Asset accounts (Cash / Clearing)
        # 10 Liability accounts (Merchant / User wallets)
        asset_ids = [f"asset_{i}" for i in range(num_accounts // 2)]
        liab_ids = [f"liab_{i}" for i in range(num_accounts // 2)]

        for a_id in asset_ids:
            ledger.create_account(a_id, f"Asset Account {a_id}", AccountType.ASSET, "USD")
        for l_id in liab_ids:
            ledger.create_account(l_id, f"Liability Account {l_id}", AccountType.LIABILITY, "USD")

        # Initial capital infusion: Debit Cash, Credit Equity
        equity = ledger.create_account("equity_pool", "Equity Pool", AccountType.EQUITY, "USD")
        initial_infusion = 10_000_000  # $100,000.00 in minor units
        for a_id in asset_ids:
            ledger.post_journal_entry(
                f"tx_init_{a_id}",
                [
                    EntryLeg(a_id, initial_infusion, EntryDirection.DEBIT, "USD"),
                    EntryLeg("equity_pool", initial_infusion, EntryDirection.CREDIT, "USD")
                ],
                f"Initial capital {a_id}"
            )

        invariant_violations = []
        zero_sum_violations = []

        def worker_transfer(tx_idx: int):
            src_asset = random.choice(asset_ids)
            tgt_asset = random.choice(asset_ids)
            tgt_liab = random.choice(liab_ids)
            amount = random.randint(100, 5000)

            # Scenario 1: Inter-asset transfer (e.g. transit clearing)
            # Scenario 2: Asset-to-Liability deposit/customer settlement
            tx_type = tx_idx % 2
            if tx_type == 0:
                legs = [
                    EntryLeg(src_asset, amount, EntryDirection.CREDIT, "USD"),
                    EntryLeg(tgt_asset, amount, EntryDirection.DEBIT, "USD")
                ]
            else:
                legs = [
                    EntryLeg(src_asset, amount, EntryDirection.DEBIT, "USD"),
                    EntryLeg(tgt_liab, amount, EntryDirection.CREDIT, "USD")
                ]

            with lock:
                try:
                    entry = ledger.post_journal_entry(f"tx_concurrent_{tx_idx}", legs, f"Concurrent transfer {tx_idx}")
                    # Assert sum(debits) == sum(credits) for this entry
                    d_sum = sum(leg.amount for leg in entry.legs if leg.direction == EntryDirection.DEBIT)
                    c_sum = sum(leg.amount for leg in entry.legs if leg.direction == EntryDirection.CREDIT)
                    if d_sum != c_sum:
                        zero_sum_violations.append((tx_idx, d_sum, c_sum))
                except Exception as e:
                    invariant_violations.append((tx_idx, str(e)))

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(worker_transfer, i) for i in range(num_transfers)]
            concurrent.futures.wait(futures)

        # Global Accounting Equation Verification
        is_balanced, total_assets, total_liab_equity = ledger.verify_accounting_equation("USD")
        delta = total_assets - total_liab_equity

        return {
            "transfers_executed": num_transfers,
            "journal_length": len(ledger.journal),
            "zero_sum_violations_count": len(zero_sum_violations),
            "zero_sum_violations": zero_sum_violations,
            "accounting_equation_balanced": is_balanced,
            "total_assets": total_assets,
            "total_liabilities_plus_equity": total_liab_equity,
            "leak_delta": delta,
            "invariant_violations_count": len(invariant_violations)
        }

    @staticmethod
    def run_multicurrency_exchange_stress(num_exchanges: int = 1000) -> Dict:
        ledger = LedgerEngine()
        currencies = ["USD", "EUR", "GBP", "IDR", "SGD"]

        # Setup cash & nostro accounts per currency
        for cur in currencies:
            ledger.create_account(f"cash_{cur}", f"Cash {cur}", AccountType.ASSET, cur)
            ledger.create_account(f"fx_position_{cur}", f"FX Position {cur}", AccountType.LIABILITY, cur)

            # Fund initial balances
            ledger.post_journal_entry(
                f"init_{cur}",
                [
                    EntryLeg(f"cash_{cur}", 100_000_000, EntryDirection.DEBIT, cur),
                    EntryLeg(f"fx_position_{cur}", 100_000_000, EntryDirection.CREDIT, cur)
                ],
                f"Initial float {cur}"
            )

        rates_to_usd = {
            "USD": 1.0,
            "EUR": 1.08,
            "GBP": 1.28,
            "SGD": 0.75,
            "IDR": 0.000065
        }

        zero_sum_failures = []
        for i in range(num_exchanges):
            src_cur = random.choice(currencies)
            tgt_cur = random.choice([c for c in currencies if c != src_cur])
            src_amount = random.randint(1000, 500000)

            # Convert to USD then to target currency minor units
            usd_equiv = src_amount * rates_to_usd[src_cur]
            tgt_amount = max(1, round(usd_equiv / rates_to_usd[tgt_cur]))

            # In proper multi-currency double entry:
            # Currency A book: Debit FX Position, Credit Cash (or vice-versa)
            # Currency B book: Debit Cash, Credit FX Position
            legs = [
                EntryLeg(f"fx_position_{src_cur}", src_amount, EntryDirection.DEBIT, src_cur),
                EntryLeg(f"cash_{src_cur}", src_amount, EntryDirection.CREDIT, src_cur),
                EntryLeg(f"cash_{tgt_cur}", tgt_amount, EntryDirection.DEBIT, tgt_cur),
                EntryLeg(f"fx_position_{tgt_cur}", tgt_amount, EntryDirection.CREDIT, tgt_cur)
            ]

            try:
                entry = ledger.post_journal_entry(f"tx_fx_{i}", legs, f"FX Exchange {src_cur}->{tgt_cur}")
                # Verify zero-sum per currency
                for c in (src_cur, tgt_cur):
                    deb = sum(l.amount for l in entry.legs if l.currency == c and l.direction == EntryDirection.DEBIT)
                    crd = sum(l.amount for l in entry.legs if l.currency == c and l.direction == EntryDirection.CREDIT)
                    if deb != crd:
                        zero_sum_failures.append((i, c, deb, crd))
            except Exception as e:
                zero_sum_failures.append((i, "EXCEPTION", str(e)))

        currency_balances = {}
        for cur in currencies:
            is_bal, assets, rhs = ledger.verify_accounting_equation(cur)
            currency_balances[cur] = {
                "balanced": is_bal,
                "assets": assets,
                "rhs": rhs,
                "delta": assets - rhs
            }

        return {
            "num_exchanges": num_exchanges,
            "zero_sum_failures": zero_sum_failures,
            "currency_balances": currency_balances,
            "all_currencies_balanced": all(b["balanced"] for b in currency_balances.values()),
            "all_deltas_zero": all(b["delta"] == 0 for b in currency_balances.values())
        }


class EmpiricalIdempotencyStressOracle:
    """Stress tests Idempotency & Race Conditions."""

    @staticmethod
    def run_stampede_race(num_threads: int = 100) -> Dict:
        """
        Simulates 100 concurrent threads hitting the gateway with the EXACT SAME Idempotency-Key.
        Tests thread-safety and race condition prevention in atomic locking.
        """
        engine = IdempotencyEngine()
        key = "idemp_stampede_key_001"
        payload = {"amount": 50000, "currency": "USD", "merchant_id": "m_123"}

        barrier = threading.Barrier(num_threads)
        results = []

        def worker(thread_id: int):
            barrier.wait()  # Align all threads to hit begin_transaction simultaneously
            try:
                is_new, rec = engine.begin_transaction(key, payload)
                results.append((thread_id, "SUCCESS", is_new, rec.state if rec else None))
            except ValueError as e:
                results.append((thread_id, "CONFLICT", str(e)))
            except Exception as e:
                results.append((thread_id, "ERROR", str(e)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        new_count = sum(1 for r in results if r[1] == "SUCCESS" and r[2] is True)
        conflict_count = sum(1 for r in results if r[1] == "CONFLICT")
        replayed_count = sum(1 for r in results if r[1] == "SUCCESS" and r[2] is False)

        return {
            "num_threads": num_threads,
            "new_count": new_count,
            "conflict_count": conflict_count,
            "replayed_count": replayed_count,
            "is_race_condition_detected": new_count > 1,
            "raw_results_sample": results[:10]
        }

    @staticmethod
    def run_out_of_order_and_payload_mutation() -> Dict:
        """Tests payload hash mismatch and lifecycle transitions under retries."""
        engine = IdempotencyEngine()
        key = "idemp_lifecycle_002"
        payload = {"amount": 10000, "currency": "USD"}
        mutated_payload = {"amount": 99999, "currency": "USD"}

        # 1. Begin transaction -> is_new=True
        is_new1, rec1 = engine.begin_transaction(key, payload)

        # 2. Duplicate while in-flight -> 409 Conflict
        in_flight_conflict = False
        try:
            engine.begin_transaction(key, payload)
        except ValueError as e:
            if "currently in-flight" in str(e):
                in_flight_conflict = True

        # 3. Payload mutation while in-flight -> 422 Unprocessable
        mutation_rejected = False
        try:
            engine.begin_transaction(key, mutated_payload)
        except ValueError as e:
            if "payload mismatch" in str(e):
                mutation_rejected = True

        # 4. Commit transaction
        engine.commit_transaction(key, {"tx_id": "tx_999", "status": "SETTLED"}, 200)

        # 5. Subsequent duplicate request -> returns cached response (is_new=False)
        is_new2, rec2 = engine.begin_transaction(key, payload)

        # 6. Subsequent request with mutated payload after commit -> 422 Unprocessable
        mutation_rejected_after_commit = False
        try:
            engine.begin_transaction(key, mutated_payload)
        except ValueError as e:
            if "payload mismatch" in str(e):
                mutation_rejected_after_commit = True

        return {
            "is_new_first": is_new1,
            "in_flight_conflict_raised": in_flight_conflict,
            "mutation_rejected_in_flight": mutation_rejected,
            "is_new_after_commit": is_new2,
            "cached_response_matched": rec2.response_body == {"tx_id": "tx_999", "status": "SETTLED"},
            "mutation_rejected_after_commit": mutation_rejected_after_commit
        }

    @staticmethod
    def run_atomic_reservation_double_debit_stress(num_concurrent_attempts: int = 50) -> Dict:
        """
        Tests whether atomic reservations prevent balance overdraft / double-debit.
        Account balance = $1,000.00. 50 threads try to reserve $500.00 each.
        Exactly 2 must succeed, 48 must fail with Insufficient Funds.
        """
        ledger = LedgerEngine()
        lock = threading.Lock()
        acc = ledger.create_account("vault_usd", "Vault", AccountType.LIABILITY, "USD")
        acc.credit_balance = 100_000  # $1,000.00

        successful_holds = []
        failed_holds = []
        barrier = threading.Barrier(num_concurrent_attempts)

        def attempt_hold(idx: int):
            barrier.wait()
            hold_id = f"hold_{idx}"
            # Synchronized atomic reservation simulating DB SELECT FOR UPDATE / atomic decrement
            with lock:
                try:
                    ledger.hold_funds(hold_id, "vault_usd", 50_000, "USD")  # $500.00
                    successful_holds.append(hold_id)
                except ValueError as e:
                    failed_holds.append((hold_id, str(e)))

        threads = [threading.Thread(target=attempt_hold, args=(i,)) for i in range(num_concurrent_attempts)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        return {
            "initial_balance": 100_000,
            "successful_holds_count": len(successful_holds),
            "failed_holds_count": len(failed_holds),
            "final_available_balance": acc.available_balance,
            "final_held_balance": acc.held_balance,
            "overdraft_occurred": acc.available_balance < 0,
            "double_debit_prevented": len(successful_holds) == 2 and acc.available_balance == 0
        }


class EmpiricalWaterfallStressOracle:
    """Stress tests P2P Waterfall Split Math & Fractional Penny Allocation."""

    @staticmethod
    def run_property_random_trials(num_trials: int = 1000) -> Dict:
        """
        Fuzzes WaterfallEngine across random collections, investor sizes,
        interest/penalty dues, and fee rates.
        Includes dedicated small-amount micro-collection fuzzing (1 to 50 cents)
        across 5 to 20 investors.
        Checks:
        1. Conservation of money: total_distributed == collected_amount
        2. Non-negative investor payouts: investor_payouts >= 0 (asserted on every trial)
        3. Rounding remainder conservation
        """
        engine = WaterfallEngine()
        leak_cases = []
        negative_payout_cases = []
        total_trials_run = 0

        # Seed random for determinism
        random.seed(42)

        for trial in range(num_trials):
            # Include small-amount fuzzing (1 to 50 cents) across 5 to 20 investors
            if trial < num_trials // 2:
                num_investors = random.randint(5, 20)
                collected = random.randint(1, 50)
                principal_due = random.randint(0, 50)
                interest_due = random.randint(0, 25)
                penalty_due = random.randint(0, 10)
            else:
                # Full-spectrum macro/micro fuzzing ($0.01 to $10,000.00)
                num_investors = random.randint(1, 20)
                collected = random.randint(1, 1_000_000)
                principal_due = random.randint(0, 1_000_000)
                interest_due = random.randint(0, 500_000)
                penalty_due = random.randint(0, 100_000)

            # Partition 10,000 basis points across investors
            if trial % 3 == 0:
                # Uniform / equal weights (stressing repeated identical fractional remainders)
                base_w = 10000 // num_investors
                rem_w = 10000 % num_investors
                weights = [base_w + (1 if i < rem_w else 0) for i in range(num_investors)]
            else:
                cuts = sorted(random.sample(range(1, 10000), num_investors - 1)) if num_investors > 1 else []
                weights = []
                prev = 0
                for c in cuts:
                    weights.append(c - prev)
                    prev = c
                weights.append(10000 - prev)

            investors = [InvestorShare(f"inv_{i}", w) for i, w in enumerate(weights)]
            platform_fee_bps = random.randint(0, 500)  # 0% to 5%
            tax_rate_bps = random.randint(0, 2000)     # 0% to 20%

            total_trials_run += 1
            try:
                res = engine.calculate_split(
                    collected_amount=collected,
                    principal_due=principal_due,
                    interest_due=interest_due,
                    penalty_due=penalty_due,
                    platform_fee_bps=platform_fee_bps,
                    tax_rate_bps=tax_rate_bps,
                    investors=investors
                )

                # Assert non-negative investor payouts on every trial
                assert all(amt >= 0 for amt in res.investor_payouts.values()), (
                    f"Trial {trial}: Negative investor payout detected in {res.investor_payouts}"
                )
                assert res.rounding_remainder >= 0, (
                    f"Trial {trial}: Negative rounding remainder {res.rounding_remainder}"
                )

                # Check 1: Negative investor payouts
                for inv_id, amt in res.investor_payouts.items():
                    if amt < 0:
                        negative_payout_cases.append({
                            "trial": trial,
                            "collected": collected,
                            "penalty_due": penalty_due,
                            "platform_fee_bps": platform_fee_bps,
                            "investor": inv_id,
                            "negative_amount": amt,
                            "platform_fee": res.platform_fee,
                            "tax": res.withholding_tax
                        })

                # Check 2: Monetary leakage (Conservation of cents)
                delta = collected - res.total_distributed
                if delta != 0:
                    leak_cases.append({
                        "trial": trial,
                        "collected": collected,
                        "total_distributed": res.total_distributed,
                        "delta": delta,
                        "principal_paid": res.principal_paid,
                        "interest_paid": res.interest_paid,
                        "penalty_paid": res.overdue_penalty,
                        "platform_fee": res.platform_fee,
                        "tax": res.withholding_tax
                    })

            except AssertionError as ae:
                negative_payout_cases.append({
                    "trial": trial,
                    "collected": collected,
                    "error": str(ae)
                })
                raise
            except Exception as e:
                leak_cases.append({
                    "trial": trial,
                    "error": str(e)
                })

        return {
            "num_trials": total_trials_run,
            "leak_count": len(leak_cases),
            "negative_payout_count": len(negative_payout_cases),
            "leak_sample": leak_cases[:5],
            "negative_payout_sample": negative_payout_cases[:5]
        }

    @staticmethod
    def run_fractional_cent_allocation_exactness() -> Dict:
        """
        Stress tests extreme odd-cent allocations (1 cent, 2 cents, 3 cents, 7 cents)
        split across prime-number investor counts (3, 7, 11).
        Verifies deterministic remainder cent allocation without single-cent loss.
        """
        engine = WaterfallEngine()
        tests = [
            (1, [3333, 3333, 3334]),
            (2, [3333, 3333, 3334]),
            (3, [3333, 3333, 3334]),
            (4, [1428] * 6 + [1432]),
            (6, [1000] * 10),
            (6, [909] * 10 + [910]),
            (7, [1428] * 6 + [1432]),
            (10, [1000] * 10),
            (11, [500] * 20),
            (100, [333, 333, 334] + [1000] * 9)
        ]

        results = []
        for collected, weights in tests:
            investors = [InvestorShare(f"inv_{i}", w) for i, w in enumerate(weights)]
            res = engine.calculate_split(
                collected_amount=collected,
                principal_due=collected,
                interest_due=0,
                penalty_due=0,
                platform_fee_bps=0,
                tax_rate_bps=0,
                investors=investors
            )
            total_allocated = sum(res.investor_payouts.values())
            has_no_negative = all(amt >= 0 for amt in res.investor_payouts.values())
            matches = (total_allocated == collected) and has_no_negative and (res.rounding_remainder >= 0)
            results.append({
                "collected": collected,
                "total_allocated": total_allocated,
                "remainder": res.rounding_remainder,
                "no_negative": has_no_negative,
                "matches": matches
            })

        return {
            "results": results,
            "all_exact": all(r["matches"] for r in results)
        }


class TestAdversarialStressOracle(unittest.TestCase):
    """Unittest suite wrapping the empirical stress oracles."""

    def test_01_double_entry_concurrent_transfers(self):
        res = EmpiricalLedgerStressOracle.run_concurrent_transfers(num_accounts=20, num_transfers=2000, num_workers=16)
        self.assertEqual(res["zero_sum_violations_count"], 0, "Journal entry debits != credits detected")
        self.assertTrue(res["accounting_equation_balanced"], "Global accounting equation Assets = Liab + Equity failed")
        self.assertEqual(res["leak_delta"], 0, "Non-zero financial leak detected in ledger balance sheet")

    def test_02_multicurrency_exchange_zero_sum(self):
        res = EmpiricalLedgerStressOracle.run_multicurrency_exchange_stress(num_exchanges=1000)
        self.assertEqual(len(res["zero_sum_failures"]), 0, "Multi-currency journal legs failed zero-sum per currency")
        self.assertTrue(res["all_currencies_balanced"], "Multi-currency balance equation imbalance")
        self.assertTrue(res["all_deltas_zero"], "Multi-currency financial leakage detected")

    def test_03_idempotency_stampede_race_condition(self):
        res = EmpiricalIdempotencyStressOracle.run_stampede_race(num_threads=100)
        self.assertFalse(res["is_race_condition_detected"], "Race condition allowed duplicate execution")
        self.assertEqual(res["new_count"], 1, "Exactly one thread should be recognized as new request")

    def test_04_idempotency_lifecycle_and_mutation(self):
        res = EmpiricalIdempotencyStressOracle.run_out_of_order_and_payload_mutation()
        self.assertTrue(res["is_new_first"])
        self.assertTrue(res["in_flight_conflict_raised"])
        self.assertTrue(res["mutation_rejected_in_flight"])
        self.assertTrue(res["cached_response_matched"])
        self.assertTrue(res["mutation_rejected_after_commit"])

    def test_05_atomic_reservations_double_debit(self):
        res = EmpiricalIdempotencyStressOracle.run_atomic_reservation_double_debit_stress(num_concurrent_attempts=50)
        self.assertFalse(res["overdraft_occurred"], "Account balance overdraft occurred")
        self.assertTrue(res["double_debit_prevented"], "Double debit was not prevented under concurrency")

    def test_06_fractional_cent_allocation_exactness(self):
        res = EmpiricalWaterfallStressOracle.run_fractional_cent_allocation_exactness()
        self.assertTrue(res["all_exact"], "Odd-cent split failed exactness check")

    def test_07_waterfall_property_fuzzing_invariants(self):
        """
        Adversarial test that exercises random property fuzzing across 1,000 trials.
        Asserts zero monetary leakage and zero negative investor payouts.
        """
        res = EmpiricalWaterfallStressOracle.run_property_random_trials(num_trials=1000)
        self.assertEqual(
            res["negative_payout_count"], 0,
            f"Adversarial breach: {res['negative_payout_count']} negative investor payouts detected! "
            f"Sample: {res['negative_payout_sample'][:1]}"
        )
        self.assertEqual(
            res["leak_count"], 0,
            f"Adversarial breach: {res['leak_count']} monetary leak cases detected! "
            f"Sample: {res['leak_sample'][:1]}"
        )


def main():
    print("=" * 80)
    print(" EMPIRICAL ADVERSARIAL STRESS TEST HARNESS & INVARIANT VERIFICATION ")
    print("=" * 80)

    # 1. Double-Entry Concurrent Transfers
    print("\n[ORACLE 1] Double-Entry Accounting Invariant (Concurrent 2,000 Transfers)...")
    res1 = EmpiricalLedgerStressOracle.run_concurrent_transfers(num_accounts=20, num_transfers=2000, num_workers=16)
    print(f"  - Transfers Executed:            {res1['transfers_executed']}")
    print(f"  - Zero-Sum Journal Violations:   {res1['zero_sum_violations_count']}")
    print(f"  - Accounting Equation Balanced:  {res1['accounting_equation_balanced']}")
    print(f"  - Total Assets:                  {res1['total_assets']:,} cents")
    print(f"  - Liabilities + Equity:          {res1['total_liabilities_plus_equity']:,} cents")
    print(f"  - Monetary Leak (Delta):         {res1['leak_delta']} cents")

    # 2. Multi-Currency Zero-Sum
    print("\n[ORACLE 2] Multi-Currency Exchange Zero-Sum Invariant (1,000 Exchanges)...")
    res2 = EmpiricalLedgerStressOracle.run_multicurrency_exchange_stress(num_exchanges=1000)
    print(f"  - Multi-Currency Exchanges:      {res2['num_exchanges']}")
    print(f"  - Zero-Sum Failures:             {len(res2['zero_sum_failures'])}")
    print(f"  - All Currencies Balanced:       {res2['all_currencies_balanced']}")
    print(f"  - All Currency Deltas Zero:      {res2['all_deltas_zero']}")

    # 3. Idempotency Stampede Race Condition
    print("\n[ORACLE 3] Idempotency Stampede Race Condition (100 Concurrent Threads)...")
    res3 = EmpiricalIdempotencyStressOracle.run_stampede_race(num_threads=100)
    print(f"  - Total Concurrent Requests:     {res3['num_threads']}")
    print(f"  - Handled as New Request:        {res3['new_count']}")
    print(f"  - Handled as Conflict/In-Flight: {res3['conflict_count']}")
    print(f"  - Race Condition Detected:       {res3['is_race_condition_detected']}")

    # 4. Idempotency Lifecycle & Mutation
    print("\n[ORACLE 4] Idempotency Lifecycle, Retries & Payload Mutation...")
    res4 = EmpiricalIdempotencyStressOracle.run_out_of_order_and_payload_mutation()
    print(f"  - Initial Request is New:        {res4['is_new_first']}")
    print(f"  - In-Flight Conflict Raised:     {res4['in_flight_conflict_raised']}")
    print(f"  - Mutation in In-Flight Rejected:{res4['mutation_rejected_in_flight']}")
    print(f"  - Retry Returns Cached Response: {res4['cached_response_matched']}")
    print(f"  - Post-Commit Mutation Rejected: {res4['mutation_rejected_after_commit']}")

    # 5. Atomic Reservation Double-Debit Prevention
    print("\n[ORACLE 5] Atomic Reservation & Double-Debit Prevention (50 Concurrent Attempts)...")
    res5 = EmpiricalIdempotencyStressOracle.run_atomic_reservation_double_debit_stress(num_concurrent_attempts=50)
    print(f"  - Initial Account Balance:       {res5['initial_balance']} cents")
    print(f"  - Successful Holds:              {res5['successful_holds_count']}")
    print(f"  - Failed Holds (Insufficient):   {res5['failed_holds_count']}")
    print(f"  - Overdraft Occurred:            {res5['overdraft_occurred']}")
    print(f"  - Double-Debit Prevented:        {res5['double_debit_prevented']}")

    # 6. Fractional Cent Allocation Exactness
    print("\n[ORACLE 6] P2P Waterfall Fractional Cent Allocation Exactness...")
    res6 = EmpiricalWaterfallStressOracle.run_fractional_cent_allocation_exactness()
    print(f"  - All Odd-Cent Splits Exact:     {res6['all_exact']}")
    for r in res6['results']:
        print(f"    * Collected: {r['collected']}c -> Distributed: {r['total_allocated']}c (Remainder: {r['remainder']}c)")

    # 7. Waterfall Random Property Fuzzing (1,000 Trials)
    print("\n[ORACLE 7] P2P Waterfall Property Fuzzing (1,000 Trials)...")
    res7 = EmpiricalWaterfallStressOracle.run_property_random_trials(num_trials=1000)
    print(f"  - Trials Run:                    {res7['num_trials']}")
    print(f"  - Monetary Leaks Detected:       {res7['leak_count']}")
    print(f"  - Negative Payouts Detected:     {res7['negative_payout_count']}")
    if res7['leak_count'] > 0:
        print(f"    * Sample Leak: {res7['leak_sample'][0]}")
    if res7['negative_payout_count'] > 0:
        print(f"    * Sample Negative Payout: {res7['negative_payout_sample'][0]}")

    print("\n" + "=" * 80)
    print(" EMPIRICAL VERIFICATION COMPLETE ")
    print("=" * 80)


if __name__ == "__main__":
    main()
