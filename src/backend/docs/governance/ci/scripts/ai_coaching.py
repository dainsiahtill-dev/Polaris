#!/usr/bin/env python3
"""
AI Coaching: Interactive Bug-Fix Process Diagnostic Tool

读取当前会话或最近的 VC/ADR，分析 AI 在修 bug 时最常犯的错误模式，
并给出具体的改进建议。目的是让 AI 变"聪明"，而不是给更多知识。

用法:
    python docs/governance/ci/scripts/ai_coaching.py
    python docs/governance/ci/scripts/ai_coaching.py --session path/to/conversation.jsonl
    python docs/governance/ci/scripts/ai_coaching.py --pattern-report
    python docs/governance/ci/scripts/ai_coaching.py --interactive
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import textwrap
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent.parent.parent
VC_DIR = REPO_ROOT / "docs" / "governance" / "templates" / "verification-cards"
ADR_DIR = REPO_ROOT / "docs" / "governance" / "decisions"

# ── Colors ─────────────────────────────────────────────────────────────────

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

# ── Known Bug Patterns ──────────────────────────────────────────────────────

# Patterns that indicate the bug was a "structural" bug but might be misclassified
STRUCTURAL_INDICATORS = [
    "stream",
    "content",
    "sanitize",
    "clean",
    "wrapper",
    "parse",
    "transcript",
    "history",
    "session",
    "import",
    "cycle",
    "loop",
]

# Patterns that indicate a "pattern" bug (same mistake repeated)
PATTERN_INDICATORS = [
    "same",
    "repeat",
    "again",
    "multiple",
    "several",
    "again",
]

# Keywords that signal missing pre-mortem depth
SHALLOW_PREMORTEM_PATTERNS = [
    r"if.*wrong",
    r"might.*fail",
    r"could.*break",
    r"maybe.*error",
]


# ── Session Analysis ─────────────────────────────────────────────────────────


def load_vcs(vc_dir: Path) -> list[dict[str, Any]]:
    """Load all Verification Cards from directory with proper resource management."""
    if not vc_dir.exists():
        return []
    cards = []
    for f in sorted(vc_dir.glob("vc-*.yaml")):
        try:
            with open(f, encoding="utf-8") as fp:
                data = yaml.safe_load(fp)
            if data:
                data["_source_file"] = f.name
                cards.append(data)
        except Exception as e:
            logger.debug("Failed to load VC %s: %s", f.name, e)
    return cards


def load_conversation_events(session_path: Path) -> list[dict[str, Any]]:
    """Load events from a conversation JSONL file."""
    events = []
    if not session_path.exists():
        return events
    try:
        for line in session_path.open(encoding="utf-8"):
            line = line.strip()
            if line:
                events.append(json.loads(line))
    except Exception as e:
        logger.debug("Failed to parse event line in %s: %s", session_path.name, e)
    return events


def analyze_vc_for_deep_issues(card: dict[str, Any]) -> list[dict[str, str]]:
    """Identify specific weaknesses in a VC."""
    issues: list[dict[str, str]] = []
    card_id = card.get("card_id", "?")

    # Check each assumption
    for i, assumption in enumerate(card.get("assumptions", [])):
        status = assumption.get("status", "")
        statement = assumption.get("statement", "")
        evidence = assumption.get("evidence", "")

        if status == "unverified":
            issues.append(
                {
                    "severity": "HIGH",
                    "area": "Assumption A" + assumption.get("id", str(i + 1)),
                    "problem": f"Still unverified: '{statement[:60]}'",
                    "fix": "Must read code and fill in 'verified_true' or 'verified_false' + evidence",
                }
            )

        if status == "verified_true" and "maybe" in statement.lower():
            issues.append(
                {
                    "severity": "MEDIUM",
                    "area": "Assumption A" + assumption.get("id", str(i + 1)),
                    "problem": f"Uncertain language ('maybe') but marked verified_true: '{statement[:60]}'",
                    "fix": "If uncertain, use status='uncertain' instead of 'verified_true'",
                }
            )

        if status == "verified_false":
            # Check if the "correct" assumption was wrong
            if len(evidence) < 20:
                issues.append(
                    {
                        "severity": "MEDIUM",
                        "area": f"Assumption A{assumption.get('id', str(i + 1))} (verified_false)",
                        "problem": f"Evidence too brief for verified_false: '{evidence[:40]}'",
                        "fix": "verified_false needs specific file:line evidence of what was wrong",
                    }
                )

    # Check pre_mortem
    pm = card.get("pre_mortem", {})
    fp = pm.get("failure_point", "")
    if fp:
        is_shallow = any(re.search(p, fp.lower()) for p in SHALLOW_PREMORTEM_PATTERNS)
        if is_shallow or len(fp) < 30:
            issues.append(
                {
                    "severity": "HIGH",
                    "area": "Pre-mortem",
                    "problem": f"failure_point too vague/shallow: '{fp[:60]}'",
                    "fix": "Must name specific variables, e.g. 'if clean_content is already sanitized when passed to _parse_content_and_thinking_tool_calls, and native_tool_calls is empty, tool calls won't be extracted'",
                }
            )
    else:
        issues.append(
            {
                "severity": "CRITICAL",
                "area": "Pre-mortem",
                "problem": "No failure_point at all",
                "fix": "Must describe: (1) the specific broken path (2) variable names (3) condition that triggers the bug",
            }
        )

    # Check verification_plan
    vp = card.get("verification_plan", {})
    unit_tests = vp.get("unit_tests", [])
    if not unit_tests:
        issues.append(
            {
                "severity": "MEDIUM",
                "area": "Verification Plan",
                "problem": "No unit_tests specified",
                "fix": "Must specify at least one test file + expected behavior",
            }
        )

    # Check classification
    cls = card.get("classification", "")
    bug_summary = card.get("bug_summary", "").lower()
    if cls == "one_off" and any(ind in bug_summary for ind in STRUCTURAL_INDICATORS):
        issues.append(
            {
                "severity": "HIGH",
                "area": "Classification",
                "problem": f"Classified as 'one_off' but bug involves '{', '.join([i for i in STRUCTURAL_INDICATORS if i in bug_summary])}' — likely structural",
                "fix": "Change classification to 'structural' and write an ADR",
            }
        )

    return issues


# ── Pattern Analysis ────────────────────────────────────────────────────────


def build_pattern_report(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a pattern report across all VCs."""
    all_assumptions: dict[str, int] = {}
    all_keywords: dict[str, int] = {}
    classification_counts: dict[str, int] = {}
    assumption_status_totals: dict[str, int] = {}

    for card in cards:
        cls = card.get("classification", "unknown")
        classification_counts[cls] = classification_counts.get(cls, 0) + 1

        for assumption in card.get("assumptions", []):
            status = assumption.get("status", "unverified")
            assumption_status_totals[status] = assumption_status_totals.get(status, 0) + 1

            statement = assumption.get("statement", "").lower()
            for kw in [
                "stream",
                "content",
                "parse",
                "native",
                "import",
                "sanitize",
                "clean",
                "wrapper",
                "loop",
                "null",
                "empty",
            ]:
                if kw in statement:
                    all_keywords[kw] = all_keywords.get(kw, 0) + 1

    return {
        "total_cards": len(cards),
        "classification_distribution": classification_counts,
        "assumption_status_totals": assumption_status_totals,
        "top_assumption_keywords": sorted(all_keywords.items(), key=lambda x: -x[1])[:8],
    }


# ── Interactive Mode ────────────────────────────────────────────────────────


def interactive_mode(cards: list[dict[str, Any]]) -> None:
    """Interactive coaching: ask AI about specific VC weaknesses."""
    print(f"\n{CYAN}{BOLD}╔══════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{CYAN}{BOLD}║          AI Bug-Fix Process Coaching (Interactive)         ║{RESET}")
    print(f"{CYAN}{BOLD}╚══════════════════════════════════════════════════════════════╝{RESET}\n")

    for card in cards:
        card_id = card.get("card_id", "?")
        issues = analyze_vc_for_deep_issues(card)

        print(f"{BOLD}{'─' * 60}{RESET}")
        print(f"{CYAN}{BOLD}[{card_id}]{RESET}  {card.get('bug_summary', 'No summary')[:50]}")
        print(f"{DIM}Classification: {card.get('classification', '?')}{RESET}")
        print(
            f"{DIM}Assumptions: {len(card.get('assumptions', []))} total "
            f"| {len([a for a in card.get('assumptions', []) if a.get('status') == 'verified_true'])} ✓ "
            f"| {len([a for a in card.get('assumptions', []) if a.get('status') == 'verified_false'])} ✗ "
            f"| {len([a for a in card.get('assumptions', []) if a.get('status') == 'unverified'])} ?{RESET}"
        )
        print()

        if not issues:
            print(f"  {GREEN}✓ No process issues detected for this VC{RESET}")
        else:
            high = [i for i in issues if i["severity"] == "HIGH"]
            med = [i for i in issues if i["severity"] == "MEDIUM"]
            crit = [i for i in issues if i["severity"] == "CRITICAL"]

            for group, color, label in [(crit, RED, "CRITICAL"), (high, YELLOW, "HIGH"), (med, MAGENTA, "MEDIUM")]:
                if not group:
                    continue
                print(f"{color}{BOLD}  {label} PRIORITY:{RESET}")
                for issue in group:
                    print(f"    {RED}✗{RESET} {issue['area']}: {issue['problem']}")
                    print(f"      {GREEN}→{RESET} {DIM}{issue['fix']}{RESET}")

        print()


# ── Main ────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AI Bug-Fix Process Coaching Tool",
        epilog=textwrap.dedent("""\
            Examples:
              python ai_coaching.py                          # Load all VCs, show summary
              python ai_coaching.py --pattern-report        # Show cross-bug patterns
              python ai_coaching.py --interactive           # Deep-dive per VC analysis
              python ai_coaching.py --session conv.jsonl    # Analyze conversation history
        """),
    )
    parser.add_argument("--vc-dir", type=Path, default=VC_DIR)
    parser.add_argument("--pattern-report", action="store_true", help="Show cross-bug pattern analysis")
    parser.add_argument("--interactive", "-i", action="store_true", help="Deep-dive per VC analysis")
    parser.add_argument("--session", type=Path, help="Path to conversation JSONL")
    args = parser.parse_args()

    cards = load_vcs(args.vc_dir)

    if not cards:
        print(f"{YELLOW}WARNING: No Verification Cards found in {args.vc_dir}{RESET}", file=sys.stderr)
        print("Run some bug fixes first, then run this tool to analyze the process.")
        return 0

    print(f"\n{CYAN}Loaded {len(cards)} Verification Card(s){RESET}\n")

    # Pattern report
    if args.pattern_report:
        report = build_pattern_report(cards)
        print(f"{BOLD}══════════════════════════════════════════════════════════{RESET}")
        print(f"{BOLD}  CROSS-BUG PATTERN ANALYSIS{RESET}")
        print(f"{BOLD}══════════════════════════════════════════════════════════{RESET}")

        print(f"\n{BLUE}Classification distribution:{RESET}")
        for cls, cnt in sorted(report["classification_distribution"].items(), key=lambda x: -x[1]):
            print(f"  {CYAN}{cls:<15}{RESET} {cnt}x")

        print(f"\n{BLUE}Assumption status totals:{RESET}")
        for status, cnt in sorted(report["assumption_status_totals"].items()):
            icon = {"verified_true": "✓", "verified_false": "✗", "unverified": "?", "uncertain": "~"}.get(status, "?")
            color = {"verified_true": GREEN, "verified_false": YELLOW, "unverified": RED, "uncertain": MAGENTA}.get(
                status, DIM
            )
            print(f"  {color}{icon}{RESET} {status:<20} {cnt}x")

        print(f"\n{BLUE}Most common assumption keywords:{RESET}")
        for kw, cnt in report["top_assumption_keywords"]:
            print(f"  {CYAN}{kw:<15}{RESET} {cnt}x")

        return 0

    # Interactive mode
    if args.interactive:
        interactive_mode(cards)
        return 0

    # Default: summary
    print(f"{BOLD}══════════════════════════════════════════════════════════{RESET}")
    print(f"{BOLD}  VERIFICATION CARD SUMMARY{RESET}")
    print(f"{BOLD}══════════════════════════════════════════════════════════{RESET}")

    for card in cards:
        card_id = card.get("card_id", "?")
        cls = card.get("classification", "?")
        n_assumptions = len(card.get("assumptions", []))
        has_adr = bool(card.get("related_adrs"))

        status_icon = GREEN + "✓" if has_adr or cls != "structural" else RED + "✗"
        print(f"\n  {status_icon}{RESET} [{card_id}]")
        print(f"      classification: {CYAN}{cls}{RESET}")
        print(f"      assumptions:    {n_assumptions}")

        for assumption in card.get("assumptions", []):
            status = assumption.get("status", "?")
            icon = {
                "verified_true": f"{GREEN}✓{RESET}",
                "verified_false": f"{YELLOW}✗{RESET}",
                "unverified": f"{RED}?{RESET}",
                "uncertain": f"{MAGENTA}~{RESET}",
            }.get(status, "?")
            print(f"        {icon} [{assumption.get('id', '?')}] {assumption.get('statement', '')[:55]}")

    print(f"\n{BOLD}{'─' * 60}{RESET}")
    print("\n  Run with:")
    print(f"    {CYAN}--pattern-report{RESET}  Cross-bug pattern analysis")
    print(f"    {CYAN}--interactive{RESET}   Deep-dive per VC coaching")
    print(f"    {CYAN}--interactive --pattern-report{RESET}  Both")
    return 0


if __name__ == "__main__":
    sys.exit(main())
