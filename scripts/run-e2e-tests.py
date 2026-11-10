#!/usr/bin/env python3
"""
Automated E2E Test Runner Harness for Payment Gateway Platform.
Executes test suites across Tiers 1 to 4 with filtering, reporting, and CI integration.
Supports pure standard library Python 3.11 with zero external pip dependencies.

Usage:
    python scripts/run-e2e-tests.py --all
    python scripts/run-e2e-tests.py --tier 1
    python scripts/run-e2e-tests.py --tier 2
    python scripts/run-e2e-tests.py --tier 3
    python scripts/run-e2e-tests.py --tier 4
    python scripts/run-e2e-tests.py --feature F09
    python scripts/run-e2e-tests.py --all --verbose
    python scripts/run-e2e-tests.py --all --json
"""

import argparse
import json
import os
import sys
import time
import unittest
from pathlib import Path


def get_project_root() -> Path:
    # Resolve scripts/.. to find project root
    return Path(__file__).resolve().parent.parent


def build_test_suite(tier: int = None, feature: str = None) -> unittest.TestSuite:
    project_root = get_project_root()
    test_dir = project_root / "tests" / "e2e"

    # Add project root to sys.path so 'tests.e2e...' can be imported
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    if tier == 1:
        patterns = ["test_tier1_features.py"]
    elif tier == 2:
        patterns = ["test_tier2_boundaries.py"]
    elif tier == 3:
        patterns = ["test_tier3_combinations.py"]
    elif tier == 4:
        patterns = ["test_tier4_real_world.py"]
    else:
        patterns = [
            "test_tier1_features.py",
            "test_tier2_boundaries.py",
            "test_tier3_combinations.py",
            "test_tier4_real_world.py"
        ]

    for pat in patterns:
        sub_suite = loader.discover(start_dir=str(test_dir), pattern=pat, top_level_dir=str(project_root))
        suite.addTests(sub_suite)

    if feature:
        # Filter tests by feature prefix (e.g. 'f01' or 'F01')
        feat_lower = feature.lower()
        filtered_suite = unittest.TestSuite()
        for test_case in _iterate_tests(suite):
            test_name = test_case.id().lower()
            if feat_lower in test_name:
                filtered_suite.addTest(test_case)
        return filtered_suite

    return suite


def _iterate_tests(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iterate_tests(item)
        else:
            yield item


def main():
    parser = argparse.ArgumentParser(description="Payment Gateway Platform E2E Test Runner")
    parser.add_argument("--all", action="store_true", help="Run all 4 Tiers of test suites")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3, 4], help="Run a specific Tier (1-4)")
    parser.add_argument("--feature", type=str, help="Filter tests by Feature ID (e.g. F01, F09)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose test execution output")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")

    args = parser.parse_args()

    if not args.all and not args.tier and not args.feature:
        # Default to running all
        args.all = True

    tier = args.tier if args.tier else None
    suite = build_test_suite(tier=tier, feature=args.feature)
    test_count = suite.countTestCases()

    if test_count == 0:
        if args.json:
            print(json.dumps({"status": "ERROR", "message": "No tests matched criteria", "total": 0}))
        else:
            print(f"[ERROR] No tests found matching criteria (tier={tier}, feature={args.feature})")
        sys.exit(2)

    t0 = time.perf_counter()

    if args.json:
        # Run test silently and collect results
        runner = unittest.TextTestRunner(stream=open(os.devnull, "w"), verbosity=0)
        result = runner.run(suite)
        duration = time.perf_counter() - t0

        output = {
            "status": "PASS" if result.wasSuccessful() else "FAIL",
            "total_tests": result.testsRun,
            "passed": result.testsRun - len(result.failures) - len(result.errors),
            "failed": len(result.failures),
            "errors": len(result.errors),
            "duration_seconds": round(duration, 4),
            "tier_filter": tier,
            "feature_filter": args.feature,
            "failures": [{"test": str(f[0]), "traceback": str(f[1])} for f in result.failures],
            "errors_detail": [{"test": str(e[0]), "traceback": str(e[1])} for e in result.errors]
        }
        print(json.dumps(output, indent=2))
        sys.exit(0 if result.wasSuccessful() else 1)

    else:
        print("=" * 78)
        print("   PAYMENT GATEWAY PLATFORM — END-TO-END VERIFICATION HARNESS")
        print("=" * 78)
        print(f"Target Workspace: {get_project_root()}")
        print(f"Test Filter:      {'Tier ' + str(tier) if tier else ('Feature ' + args.feature if args.feature else 'All Tiers (1-4)')}")
        print(f"Discovered Tests: {test_count} tests")
        print("-" * 78)

        verbosity = 2 if args.verbose else 1
        runner = unittest.TextTestRunner(verbosity=verbosity)
        result = runner.run(suite)
        duration = time.perf_counter() - t0

        print("-" * 78)
        print(f"EXECUTION SUMMARY:")
        print(f"  Total Executed: {result.testsRun}")
        print(f"  Passed:         {result.testsRun - len(result.failures) - len(result.errors)}")
        print(f"  Failed:         {len(result.failures)}")
        print(f"  Errors:         {len(result.errors)}")
        print(f"  Elapsed Time:   {duration:.3f}s")
        print("=" * 78)

        if result.wasSuccessful():
            print(">>> STATUS: ALL TESTS PASSED [100% SUCCESS] <<<")
            sys.exit(0)
        else:
            print(">>> STATUS: TEST SUITE FAILED <<<")
            sys.exit(1)


if __name__ == "__main__":
    main()
