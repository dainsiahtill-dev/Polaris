#!/usr/bin/env python3
"""
ADR Coverage Checker for Structural Bugs

确保所有 structural 分类的 bug 都有对应的 ADR。
同时检查 ADR 质量：是否包含必要章节。

用法:
    python docs/governance/ci/scripts/check_adrs.py
    python docs/governance/ci/scripts/check_adrs.py --check-coverage
    python docs/governance/ci/scripts/check_adrs.py --list-missing
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent.parent.parent
VC_DIR = REPO_ROOT / "docs" / "governance" / "templates" / "verification-cards"
ADR_DIR = REPO_ROOT / "docs" / "governance" / "decisions"

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

# Required sections in an ADR
REQUIRED_ADR_SECTIONS = [
    "状态",
    "上下文",
    "问题陈述",
    "决策",
    "后果",
    "验证方法",
]

# Required front-matter fields
REQUIRED_ADR_FIELDS = ["status", "context", "decision", "consequences"]


def load_yaml(path: Path) -> dict[str, Any] | None:
    """Load and parse a YAML file with proper resource management."""
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def extract_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Extract YAML front-matter from markdown ADR."""
    match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    if match:
        try:
            fm = yaml.safe_load(match.group(1))
            body = text[match.end() :]
            return (fm or {}, body)
        except Exception as e:
            logger.debug("Failed to parse front matter: %s", e)
    return ({}, text)


def check_adr_quality(adr_path: Path) -> dict[str, Any]:
    """Check quality of a single ADR."""
    text = adr_path.read_text(encoding="utf-8")
    fm, body = extract_front_matter(text)

    errors: list[str] = []
    warnings: list[str] = []

    # Check front-matter status
    status = fm.get("status", "")
    valid_statuses = ["proposed", "accepted", "deprecated", "superseded", "已实施", "实施中", "已废弃"]
    if status.lower() not in [s.lower() for s in valid_statuses]:
        warnings.append(f"status='{status}' — expected one of {valid_statuses}")

    # Check required sections in body
    body_lower = body.lower()
    for section in REQUIRED_ADR_SECTIONS:
        if section.lower() not in body_lower:
            errors.append(f"missing section: ## {section}")

    # Check body length (quality signal)
    if len(body) < 500:
        warnings.append(f"ADR body very short ({len(body)} chars) — may lack detail")

    # Check for 'consequences' section quality
    cons_match = re.search(r"## (?:后果|Consequences)[:\s]*\n(.*?)(?=\n## |\Z)", body, re.DOTALL | re.IGNORECASE)
    if cons_match:
        cons_text = cons_match.group(1).strip()
        if "收益" not in cons_text and "收益" not in body and "收益" not in body:
            if len(cons_text) < 50:
                warnings.append("'## 后果' section too short — should detail both gains and technical debt")

    # Extract ADR ID from filename
    adr_id = adr_path.stem.upper().replace("-", "-")

    return {
        "adr_id": adr_id,
        "path": str(adr_path),
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "body_len": len(body),
    }


def load_all_vcs(vc_dir: Path) -> list[dict[str, Any]]:
    if not vc_dir.exists():
        return []
    cards = []
    for f in sorted(vc_dir.glob("vc-*.yaml")):
        data = load_yaml(f)
        if data:
            data["_source_file"] = f.name
            cards.append(data)
    return cards


def main() -> int:
    parser = argparse.ArgumentParser(description="Check ADR coverage for structural bugs")
    parser.add_argument("--adr-dir", type=Path, default=ADR_DIR, help="ADR directory")
    parser.add_argument("--vc-dir", type=Path, default=VC_DIR, help="VC directory")
    parser.add_argument("--check-coverage", action="store_true", help="Cross-check VCs against ADRs")
    parser.add_argument("--list-missing", action="store_true", help="List missing ADRs")
    parser.add_argument("--strict-quality", action="store_true", help="Fail on ADR quality warnings too")
    args = parser.parse_args()

    # Load ADRs
    adr_files = sorted(args.adr_dir.glob("adr-*.md")) if args.adr_dir.exists() else []
    print(f"Found {len(adr_files)} ADR file(s)\n")

    adr_results: dict[str, dict[str, Any]] = {}
    quality_errors = 0
    quality_warnings = 0

    for adr_path in adr_files:
        result = check_adr_quality(adr_path)
        adr_id = result["adr_id"]
        adr_results[adr_id] = result

        status_icon = f"{CYAN}{result['status']}{RESET}" if result["status"] else f"{YELLOW}no status{RESET}"
        print(f"  {GREEN}✓{RESET} {adr_id}: {status_icon}")
        for err in result["errors"]:
            print(f"    {RED}ERROR:{RESET} {err}")
            quality_errors += 1
        for warn in result["warnings"]:
            print(f"    {YELLOW}WARN:{RESET} {warn}")
            quality_warnings += 1

    if not adr_files:
        print(f"  {YELLOW}WARNING: No ADR files found in {args.adr_dir}{RESET}")

    # Coverage check
    if args.check_coverage:
        print(f"\n{'─' * 50}")
        print(f"{BOLD}STRUCTURAL BUG ADR COVERAGE CHECK:{RESET}\n")

        cards = load_all_vcs(args.vc_dir)
        structural_cards = [c for c in cards if c.get("classification") == "structural"]

        if not structural_cards:
            print(f"  {DIM}No structural bugs found in VC files.{RESET}")
        else:
            covered = 0
            for card in structural_cards:
                card_id = card.get("card_id", "?")
                related = card.get("related_adrs", [])
                status_icon = GREEN + "✓" if related else RED + "✗"
                status_icon += RESET

                print(f"  {status_icon} {card_id} (structural)")
                if related:
                    for adr_id in related:
                        adr_prefix = adr_id.lower().replace("adr-", "adr-")
                        # ADR-0042 → glob for adr-0042*.md (filename includes slug)
                        adr_files = list(args.adr_dir.glob(f"{adr_prefix}*.md")) + list(
                            args.adr_dir.glob(f"{adr_prefix}.md")
                        )
                        exists = len(adr_files) > 0
                        exists_icon = f"{GREEN}found{RESET}" if exists else f"{RED}MISSING{RESET}"
                        found_name = adr_files[0].name if adr_files else f"{adr_prefix}*.md"
                        print(f"      {exists_icon} → {found_name}")
                        if exists:
                            covered += 1
                        else:
                            quality_errors += 1
                            print(f"        {RED}ERROR: ADR file not found{RESET}")
                else:
                    print(f"      {RED}ERROR: structural bug has no related_adrs{RESET}")
                    quality_errors += 1

            coverage = round(covered / len(structural_cards) * 100) if structural_cards else 100
            print(f"\n  Coverage: {covered}/{len(structural_cards)} = {coverage}%")

            if args.list_missing:
                print(f"\n  {BOLD}Missing ADRs:{RESET}")
                for card in structural_cards:
                    if not card.get("related_adrs"):
                        print(f"    {RED}✗{RESET} {card.get('card_id', '?')} — no ADR referenced")
                        # Suggest filename
                        bug_summary = card.get("bug_summary", "")[:50]
                        slug = bug_summary.lower().replace(" ", "-").replace(",", "")[:40]
                        print(f"      Suggest: docs/governance/decisions/adr-<N>-{slug}.md")

    # Quality summary
    print(f"\n{'─' * 50}")
    print(f"ADR Quality: {quality_errors} errors, {quality_warnings} warnings")

    if args.strict_quality and quality_errors > 0:
        print(f"{RED}Strict mode: FAIL due to {quality_errors} quality error(s){RESET}")
        return 1
    elif quality_errors > 0:
        print(f"{YELLOW}Note: {quality_errors} quality errors found (run with --strict-quality to fail){RESET}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
