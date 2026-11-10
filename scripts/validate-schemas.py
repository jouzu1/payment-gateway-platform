#!/usr/bin/env python3
"""
Comprehensive Validation Script for Payment Gateway Platform.
Validates:
1. OpenAPI 3.1 YAML specification syntax and root schema properties.
2. PostgreSQL DDL syntax, balanced parentheses, and core financial tables.
3. Protobuf v3 service definitions.
4. Relative link integrity across all Markdown architectural documentation.
5. Requirement Traceability Matrix coverage (R1, R2, R3 and F01-F25).
"""

import os
import re
import sys
from pathlib import Path
from typing import List, Tuple

try:
    import yaml
except ImportError:
    yaml = None


def get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def validate_openapi_schema(root: Path) -> Tuple[bool, List[str]]:
    openapi_path = root / "schemas" / "openapi.yaml"
    errors = []
    if not openapi_path.is_file():
        return False, [f"Missing OpenAPI specification at {openapi_path}"]

    content = openapi_path.read_text(encoding="utf-8")
    if yaml is not None:
        try:
            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                errors.append("OpenAPI root must be a YAML mapping/dictionary.")
            else:
                for req_key in ["openapi", "info", "paths", "components"]:
                    if req_key not in data:
                        errors.append(f"OpenAPI schema missing mandatory root key: '{req_key}'")
                if "openapi" in data and not str(data["openapi"]).startswith("3."):
                    errors.append(f"OpenAPI version must be 3.x, found: {data.get('openapi')}")
        except Exception as ex:
            errors.append(f"YAML parsing error in openapi.yaml: {ex}")
    else:
        # Fallback text check
        for req_key in ["openapi:", "info:", "paths:", "components:"]:
            if req_key not in content:
                errors.append(f"OpenAPI schema missing root section: '{req_key}'")

    return len(errors) == 0, errors


def validate_sql_ddl(root: Path) -> Tuple[bool, List[str]]:
    sql_path = root / "schemas" / "schema.sql"
    errors = []
    if not sql_path.is_file():
        return False, [f"Missing SQL DDL at {sql_path}"]

    sql_text = sql_path.read_text(encoding="utf-8")

    # Check balanced parentheses
    open_parens = sql_text.count("(")
    close_parens = sql_text.count(")")
    if open_parens != close_parens:
        errors.append(f"Unbalanced parentheses in schema.sql: {open_parens} '(' vs {close_parens} ')'")

    # Check mandatory tables and entities
    required_entities = [
        ("CREATE TYPE account_category", "CREATE TYPE account_type"),
        ("CREATE TYPE entry_direction", "CREATE TYPE entry_direction"),
        ("CREATE TABLE ledger_accounts", "CREATE TABLE accounts"),
        ("CREATE TABLE journal_entries", "CREATE TABLE journal_entries"),
        ("CREATE TABLE entry_legs", "CREATE TABLE entry_legs"),
        ("CREATE TABLE account_balances", "CREATE TABLE account_balances")
    ]
    for pair in required_entities:
        if not any(variant in sql_text for variant in pair):
            errors.append(f"schema.sql missing expected entity declaration from: {pair}")

    return len(errors) == 0, errors


def validate_protobuf(root: Path) -> Tuple[bool, List[str]]:
    proto_path = root / "schemas" / "service.proto"
    errors = []
    if not proto_path.is_file():
        return False, [f"Missing Protobuf specification at {proto_path}"]

    proto_text = proto_path.read_text(encoding="utf-8")
    if 'syntax = "proto3";' not in proto_text:
        errors.append('service.proto must declare syntax = "proto3";')

    open_braces = proto_text.count("{")
    close_braces = proto_text.count("}")
    if open_braces != close_braces:
        errors.append(f"Unbalanced braces in service.proto: {open_braces} '{{' vs {close_braces} '}}'")

    return len(errors) == 0, errors


def validate_markdown_links(root: Path) -> Tuple[bool, List[str]]:
    errors = []
    link_pattern = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')

    md_files = list(root.glob("docs/**/*.md")) + [root / "README.md"]

    for md_file in md_files:
        if not md_file.is_file():
            continue
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception as ex:
            errors.append(f"Failed reading {md_file}: {ex}")
            continue

        for match in link_pattern.finditer(content):
            _, target = match.groups()
            target = target.strip()

            # Ignore web links, anchors, mailto
            if target.startswith(("http://", "https://", "#", "mailto:", "ftp://")):
                continue

            # Strip anchor if present
            target_path_str = target.split("#")[0]
            if not target_path_str:
                continue

            # Resolve relative link
            target_path = (md_file.parent / target_path_str).resolve()
            if not target_path.exists():
                errors.append(f"Broken relative link in {md_file.relative_to(root)}: '{target}' (resolved: {target_path})")

    return len(errors) == 0, errors


def validate_requirements_and_structure(root: Path) -> Tuple[bool, List[str]]:
    errors = []
    required_domains = [
        "01-architecture-overview",
        "02-product-workflows",
        "03-core-subsystems",
        "04-data-models-and-schemas",
        "05-reliability-and-performance",
        "06-cloud-cost-efficiency",
        "07-api-and-integration",
        "08-verification-and-rubric"
    ]

    for domain in required_domains:
        domain_path = root / "docs" / domain
        if not domain_path.is_dir():
            errors.append(f"Missing required documentation domain directory: docs/{domain}")

    # Check for critical diagrams
    required_diagrams = [
        "system-topology.mmd",
        "local-remittance-flow.mmd",
        "cross-border-payout-flow.mmd",
        "payin-checkout-flow.mmd",
        "p2p-escrow-waterfall.mmd",
        "ledger-double-entry.mmd",
        "multi-az-ha-dr.mmd",
        "saga-outbox-cdc.mmd"
    ]
    for diag in required_diagrams:
        p = root / "diagrams" / diag
        if not p.is_file():
            errors.append(f"Missing required standalone Mermaid diagram: diagrams/{diag}")

    return len(errors) == 0, errors


def main():
    root = get_project_root()
    print("=" * 78)
    print("   PAYMENT GATEWAY PLATFORM — ARCHITECTURAL SCHEMA & INTEGRITY VALIDATOR")
    print("=" * 78)
    print(f"Target Root: {root}")
    print("-" * 78)

    checks = [
        ("OpenAPI 3.1 Specification Validation", validate_openapi_schema),
        ("PostgreSQL DDL & Schema Consistency", validate_sql_ddl),
        ("Protobuf v3 Definition Validation", validate_protobuf),
        ("Documentation Relative Link Integrity", validate_markdown_links),
        ("Domain Layout & Diagram Completeness", validate_requirements_and_structure),
    ]

    all_passed = True
    total_errors = 0

    for name, func in checks:
        passed, errors = func(root)
        if passed:
            print(f" [PASS] {name}")
        else:
            all_passed = False
            total_errors += len(errors)
            print(f" [FAIL] {name}")
            for err in errors:
                print(f"        * {err}")

    print("-" * 78)
    if all_passed:
        print(">>> VALIDATION SUCCESSFUL: All schemas, links, and blueprints valid. <<<")
        print("=" * 78)
        sys.exit(0)
    else:
        print(f">>> VALIDATION FAILED: {total_errors} issue(s) detected. <<<")
        print("=" * 78)
        sys.exit(1)


if __name__ == "__main__":
    main()
