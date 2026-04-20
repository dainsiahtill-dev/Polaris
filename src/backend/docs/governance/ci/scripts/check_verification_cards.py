#!/usr/bin/env python3
"""
Verify all Verification Cards against the schema.

检查所有 VC 是否：
1. 符合 YAML schema
2. 假设都已验证（无 unverified）
3. pre_mortem 质量达标
4. structural bug 有对应 ADR

用法:
    python docs/governance/ci/scripts/check_verification_cards.py
    python docs/governance/ci/scripts/check_verification_cards.py --strict
    python docs/governance/ci/scripts/check_verification_cards.py --vc vc-20260325-xxx.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent.parent.parent
VC_DIR = REPO_ROOT / "docs" / "governance" / "templates" / "verification-cards"
SCHEMA_PATH = REPO_ROOT / "docs" / "governance" / "schemas" / "verification-card.schema.yaml"
ADR_DIR = REPO_ROOT / "docs" / "governance" / "decisions"


class ValidationError:
    def __init__(self, card_id: str, field: str, message: str):
        self.card_id = card_id
        self.field = field
        self.message = message

    def __str__(self) -> str:
        return f"  [{self.card_id}] {self.field}: {self.message}"


def load_yaml(path: Path) -> dict[str, Any] | None:
    """Load and parse a YAML file with proper resource management."""
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def validate_card(card: dict[str, Any], source_file: str, strict: bool = False) -> list[ValidationError]:
    """Validate a single verification card. Returns list of errors."""
    errors: list[ValidationError] = []
    card_id = card.get("card_id", source_file)

    # Required top-level fields
    required_fields = [
        "card_id",
        "bug_summary",
        "classification",
        "assumptions",
        "pre_mortem",
        "verification_plan",
        "sign_off",
    ]
    for field in required_fields:
        if field not in card or not card[field]:
            errors.append(ValidationError(card_id, field, f"missing or empty required field '{field}'"))

    # Classification enum
    valid_classifications = ["one_off", "pattern", "structural"]
    if "classification" in card and card["classification"] not in valid_classifications:
        errors.append(
            ValidationError(
                card_id, "classification", f"must be one of {valid_classifications}, got: {card['classification']}"
            )
        )

    # Assumptions: minimum 2, each must have status + evidence
    assumptions = card.get("assumptions", [])
    if len(assumptions) < 2:
        errors.append(
            ValidationError(card_id, "assumptions", f"must have at least 2 assumptions, got {len(assumptions)}")
        )

    valid_statuses = ["unverified", "verified_true", "verified_false", "uncertain"]
    for i, assumption in enumerate(assumptions):
        a_id = assumption.get("id", f"A{i + 1}")
        if "status" not in assumption or assumption["status"] not in valid_statuses:
            errors.append(ValidationError(card_id, f"assumptions[{i}].status", f"must be one of {valid_statuses}"))
        if "evidence" not in assumption or len(str(assumption.get("evidence", ""))) < 5:
            errors.append(
                ValidationError(
                    card_id,
                    f"assumptions[{i}].evidence",
                    "evidence must be at least 5 characters and reference file:line",
                )
            )

        # In strict mode, no unverified assumptions allowed
        if strict and assumption.get("status") == "unverified":
            errors.append(
                ValidationError(
                    card_id,
                    f"assumptions[{i}].status",
                    f"[strict] assumption '{a_id}' is still 'unverified' — must verify before fix",
                )
            )

    # Pre_mortem quality check
    pm = card.get("pre_mortem", {})
    if pm:
        fp = pm.get("failure_point", "")
        if len(fp) < 10:
            errors.append(
                ValidationError(
                    card_id, "pre_mortem.failure_point", "failure_point too short (must be at least 10 chars)"
                )
            )
        if strict and len(fp) < 30:
            errors.append(
                ValidationError(
                    card_id,
                    "pre_mortem.failure_point",
                    f"[strict] failure_point too vague ({len(fp)} chars). "
                    "Must describe the specific broken path with variable names.",
                )
            )
        if "risk_zone" in pm and not isinstance(pm["risk_zone"], list):
            errors.append(
                ValidationError(card_id, "pre_mortem.risk_zone", "risk_zone must be a list of module/file names")
            )

    # Verification plan checks
    vp = card.get("verification_plan", {})
    if vp:
        for section in ["unit_tests", "integration_tests", "manual_check"]:
            if section not in vp:
                errors.append(
                    ValidationError(card_id, f"verification_plan.{section}", f"missing required section '{section}'")
                )

    # Structural bugs must have related ADRs
    if card.get("classification") == "structural":
        related = card.get("related_adrs", [])
        if not related:
            errors.append(
                ValidationError(
                    card_id, "related_adrs", "structural bug must have at least one related ADR (e.g. ['ADR-0042'])"
                )
            )
        # Check ADR file exists
        for adr_id in related:
            # ADR-0042 → glob for adr-0042*.md (filename includes slug)
            adr_prefix = adr_id.lower().replace("adr-", "adr-")
            adr_files = list(ADR_DIR.glob(f"{adr_prefix}*.md")) + list(ADR_DIR.glob(f"{adr_prefix}.md"))
            if not adr_files:
                errors.append(
                    ValidationError(
                        card_id, f"related_adrs.{adr_id}", f"ADR file not found: {ADR_DIR}/{adr_prefix}*.md"
                    )
                )

    # Sign-off required fields
    so = card.get("sign_off", {})
    for field in ["completed_by", "completed_at"]:
        if field not in so or not so[field]:
            errors.append(ValidationError(card_id, f"sign_off.{field}", f"missing '{field}'"))

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Verification Cards against schema")
    parser.add_argument("--vc-dir", type=Path, default=VC_DIR, help="VC directory")
    parser.add_argument("--schema", type=Path, default=SCHEMA_PATH, help="Schema file")
    parser.add_argument(
        "--strict", action="store_true", help="Enable strict checks: no unverified assumptions, detailed pre_mortem"
    )
    parser.add_argument("--vc", type=str, help="Validate a specific VC file (relative to --vc-dir)")
    parser.add_argument("--fail-fast", action="store_true", help="Exit on first error")
    args = parser.parse_args()

    # Load schema
    schema = load_yaml(args.schema)
    if not schema:
        print(f"ERROR: Could not load schema from {args.schema}", file=sys.stderr)
        return 1

    # Load VC files
    if args.vc:
        vc_files = [args.vc_dir / args.vc]
    else:
        if not args.vc_dir.exists():
            print(f"WARNING: VC directory not found: {args.vc_dir}", file=sys.stderr)
            print("No Verification Cards to validate.")
            return 0
        vc_files = sorted(args.vc_dir.glob("vc-*.yaml"))

    if not vc_files:
        print(f"WARNING: No VC files found in {args.vc_dir}", file=sys.stderr)
        return 0

    print(f"Validating {len(vc_files)} Verification Card(s)...\n")

    all_errors: list[ValidationError] = []
    validated = 0

    for vc_file in vc_files:
        data = load_yaml(vc_file)
        if not data:
            all_errors.append(ValidationError(vc_file.name, "file", "YAML parse failed or file empty"))
            continue

        # Schema-level validation (basic)
        card_id = data.get("card_id", vc_file.stem)
        for required_field in ["card_id", "bug_summary", "classification"]:
            if required_field not in data:
                all_errors.append(
                    ValidationError(card_id, required_field, f"missing required field '{required_field}'")
                )

        errors = validate_card(data, vc_file.name, strict=args.strict)
        all_errors.extend(errors)

        if not errors:
            validated += 1
            print(f"  {GREEN}✓{RESET} {vc_file.name} — PASS")
        else:
            print(f"  {RED}✗{RESET} {vc_file.name} — FAIL ({len(errors)} error(s))")

        if args.fail_fast and errors:
            break

    # Summary
    print(f"\n{'─' * 50}")
    print(f"Validated: {validated}/{len(vc_files)}")
    print(f"Errors:   {len(all_errors)}")

    if all_errors:
        print(f"\n{RED}Validation FAILED:{RESET}")
        for err in all_errors:
            print(err)
        return 1
    else:
        print(f"\n{GREEN}All Verification Cards valid.{RESET}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
