#!/usr/bin/env python3
"""
AI Bug-Fix Process Retrospective Analyzer

诊断 AI 修 bug 的过程质量：
- 读取所有 Verification Card
- 检查 ADR 覆盖率
- 识别假设错误模式
- 对比 pre_mortem 准确性
- 输出教练建议

用法:
    python docs/governance/ci/scripts/bug_retro_analyzer.py
    python docs/governance/ci/scripts/bug_retro_analyzer.py --verbose
    python docs/governance/ci/scripts/bug_retro_analyzer.py --vc docs/governance/templates/verification-cards/
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

# ── 路径常量 ────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.resolve()
# ci/scripts -> ci -> governance -> docs -> src/backend  (4 levels up)
REPO_ROOT = (SCRIPT_DIR.parent.parent.parent.parent).resolve()
VC_DIR = REPO_ROOT / "docs" / "governance" / "templates" / "verification-cards"
ADR_DIR = REPO_ROOT / "docs" / "governance" / "decisions"
SCHEMA_PATH = REPO_ROOT / "docs" / "governance" / "schemas" / "verification-card.schema.yaml"

# ── 颜色输出 ────────────────────────────────────────────────────────────────

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
BAR = "█"
EMPTY = "░"


def bar_graph(count: int, max_count: int, width: int = 10) -> str:
    """Render a text bar graph."""
    filled = max(1, int(round(count / max_count * width))) if max_count > 0 else 0
    return BAR * filled + EMPTY * (width - filled)


def load_yaml(path: Path) -> dict[str, Any] | None:
    """Load and parse a YAML file with proper resource management."""
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return None


# ── 加载数据 ────────────────────────────────────────────────────────────────


def load_all_vcs(vc_dir: Path) -> list[dict[str, Any]]:
    """Load all Verification Card YAML files."""
    if not vc_dir.exists():
        return []
    cards = []
    for f in sorted(vc_dir.glob("vc-*.yaml")):
        data = load_yaml(f)
        if data:
            data["_source_file"] = f.name
            cards.append(data)
    return cards


def load_all_adrs(adr_dir: Path) -> list[dict[str, Any]]:
    """Load all ADR markdown files (by extracting front-matter or scanning)."""
    if not adr_dir.exists():
        return []
    adrs = []
    for f in sorted(adr_dir.glob("adr-*.md")):
        text = f.read_text(encoding="utf-8")
        adrs.append({"_source_file": f.name, "text": text, "_path": f})
    return adrs


# ── Section 1: Compliance ───────────────────────────────────────────────────


def section_compliance(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """Check VC compliance across all cards."""
    total = len(cards)
    if total == 0:
        return {"total": 0, "compliant": 0, "missing_vc": 0, "issues": []}

    issues = []
    compliant = 0

    for card in cards:
        card_issues = []
        if "assumptions" not in card or len(card.get("assumptions", [])) < 2:
            card_issues.append("needs >= 2 assumptions")
        if "pre_mortem" not in card:
            card_issues.append("missing pre_mortem")
        if "verification_plan" not in card:
            card_issues.append("missing verification_plan")
        if "sign_off" not in card:
            card_issues.append("missing sign_off")

        if card_issues:
            issues.append({"card_id": card.get("card_id", "?"), "problems": card_issues})
        else:
            compliant += 1

    return {
        "total": total,
        "compliant": compliant,
        "missing_vc": total - compliant,
        "issues": issues,
    }


# ── Section 2: Assumption Patterns ────────────────────────────────────────


def section_assumption_patterns(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyze assumption status distribution and content patterns."""
    status_counts: dict[str, int] = {}
    content_keywords: dict[str, int] = {}
    verified_wrong = []  # verified_false but AI thought it was true
    verified_right = []  # correctly identified

    for card in cards:
        card_id = card.get("card_id", "?")
        for assumption in card.get("assumptions", []):
            status = assumption.get("status", "unverified")
            status_counts[status] = status_counts.get(status, 0) + 1
            statement = assumption.get("statement", "").lower()

            # Extract keywords
            for kw in [
                "native_tool_calls",
                "clean_content",
                "raw_clean",
                "wrapper",
                "sanitize",
                "parse",
                "stream",
                "import",
                "loop",
            ]:
                if kw in statement:
                    content_keywords[kw] = content_keywords.get(kw, 0) + 1

            if status == "verified_false":
                verified_wrong.append(
                    {
                        "card_id": card_id,
                        "statement": assumption.get("statement", "")[:80],
                        "evidence": assumption.get("evidence", "")[:80],
                    }
                )
            elif status == "verified_true":
                verified_right.append(
                    {
                        "card_id": card_id,
                        "statement": assumption.get("statement", "")[:80],
                    }
                )

    # Sort keywords by frequency
    top_keywords = sorted(content_keywords.items(), key=lambda x: -x[1])[:5]

    return {
        "status_counts": status_counts,
        "top_keywords": top_keywords,
        "verified_wrong": verified_wrong,
        "verified_right": verified_right,
        "total_assumptions": sum(status_counts.values()),
    }


# ── Section 3: Pre-mortem Accuracy ───────────────────────────────────────


def section_premortem(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """Assess pre_mortem quality: does failure_point actually match what went wrong?"""
    results = []
    for card in cards:
        card_id = card.get("card_id", "?")
        pm = card.get("pre_mortem", {})
        failure_point = pm.get("failure_point", "")
        risk_zone = pm.get("risk_zone", [])

        # Score pre_mortem quality
        score = 0
        if len(failure_point) < 10:
            score = 0
        elif "if" in failure_point.lower() and len(failure_point) > 30:
            score = 1  # Basic conditional
        elif any(kw in failure_point.lower() for kw in ["native_tool_calls", "empty", "null", "none", "sanitize"]):
            score = 2  # Specific technical detail

        results.append(
            {
                "card_id": card_id,
                "failure_point_len": len(failure_point),
                "score": score,
                "risk_zone_count": len(risk_zone),
                "failure_point_preview": failure_point[:80],
            }
        )

    avg_score = sum(r["score"] for r in results) / len(results) if results else 0
    return {
        "cards": results,
        "avg_score": round(avg_score, 2),
        "max_score": 2,
    }


# ── Section 4: ADR Coverage ──────────────────────────────────────────────


def section_adr_coverage(cards: list[dict[str, Any]], adrs: list[dict[str, Any]]) -> dict[str, Any]:
    """Check if all structural bugs have corresponding ADRs."""
    structural_cards = [c for c in cards if c.get("classification") == "structural"]
    related_adrs: dict[str, list[str]] = {}

    for card in cards:
        related = card.get("related_adrs", [])
        if related:
            for adr_id in related:
                related_adrs.setdefault(adr_id, []).append(card.get("card_id", "?"))

    covered = len([c for c in structural_cards if c.get("related_adrs")])
    missing = len(structural_cards) - covered

    return {
        "structural_count": len(structural_cards),
        "covered": covered,
        "missing": missing,
        "coverage_pct": round(covered / len(structural_cards) * 100) if structural_cards else 100,
        "related_adrs": related_adrs,
    }


# ── Section 5: Classification Accuracy ──────────────────────────────────


def section_classification(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyze bug classification patterns."""
    classification_counts: dict[str, int] = {}
    for card in cards:
        cls = card.get("classification", "unknown")
        classification_counts[cls] = classification_counts.get(cls, 0) + 1
    return {"counts": classification_counts}


# ── Section 6: Coaching Recommendations ─────────────────────────────────


def section_coaching(
    compliance: dict[str, Any],
    assumptions: dict[str, Any],
    premortem: dict[str, Any],
    adr_cov: dict[str, Any],
    classification: dict[str, Any],
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """Generate AI coaching recommendations based on all data."""
    recs = []

    # 1. Compliance
    if compliance["missing_vc"] > 0:
        recs.append(
            {
                "severity": "CRITICAL",
                "area": "VERIFICATION CARD",
                "finding": f"{compliance['missing_vc']} fix(es) missing or incomplete Verification Card",
                "recommendation": "Enforce: must complete VC before delivering fix. Run: python bug_retro_analyzer.py --check-compliance",
                "rule": "AGENTS.md §8.6",
            }
        )

    # 2. Assumption quality
    unverified = assumptions["status_counts"].get("unverified", 0)
    verified_false = assumptions["status_counts"].get("verified_false", 0)
    verified_true = assumptions["status_counts"].get("verified_true", 0)

    if unverified > 0:
        recs.append(
            {
                "severity": "HIGH",
                "area": "ASSUMPTION REGISTER",
                "finding": f"{unverified} assumption(s) still in 'unverified' status",
                "recommendation": "Each assumption must be verified (status: verified_true/verified_false) before fix. Add evidence from file:line.",
                "rule": "AGENTS.md §8.6.3 step 2",
            }
        )

    if verified_false == 0 and assumptions["total_assumptions"] > 0:
        recs.append(
            {
                "severity": "MEDIUM",
                "area": "ASSUMPTION ACCURACY",
                "finding": "AI has not yet misidentified an assumption (verified_false=0). This is unusual for complex bugs — double-check all assumptions.",
                "recommendation": "Review each assumption carefully. Bugs like '[TOOL_CALL] loop' often have subtle wrong assumptions about content flow.",
                "rule": "AGENTS.md §8.6.3",
            }
        )

    # 3. Pre-mortem quality
    if premortem["avg_score"] < 1.5:
        recs.append(
            {
                "severity": "HIGH",
                "area": "PRE-MORTEM",
                "finding": f"Avg pre_mortem score: {premortem['avg_score']}/{premortem['max_score']} — failure_point too vague",
                "recommendation": "Pre_mortem.failure_point must: (1) use 'if' conditional, (2) name specific variables/data, (3) describe the broken path",
                "rule": "AGENTS.md §8.6.3 step 3",
            }
        )

    # 4. ADR coverage
    if adr_cov["missing"] > 0:
        recs.append(
            {
                "severity": "HIGH",
                "area": "STRUCTURAL BUGS",
                "finding": f"{adr_cov['missing']} structural bug(s) missing ADR",
                "recommendation": "All structural bugs must have an ADR in docs/governance/decisions/adr-*.md",
                "rule": "AGENTS.md §8.6.3 step 6",
            }
        )

    # 5. Keyword-based recommendations
    top_kw = dict(assumptions["top_keywords"])
    if "native_tool_calls" in top_kw:
        recs.append(
            {
                "severity": "INFO",
                "area": "STREAMING VS NON-STREAMING",
                "finding": "'native_tool_calls' mentioned in assumptions — indicates careful handling of stream vs batch differences",
                "recommendation": "Always document whether run() vs run_stream() behaves differently for the relevant code path",
                "rule": "ADR-0042 pattern",
            }
        )

    if "clean_content" in top_kw or "raw_clean" in top_kw:
        recs.append(
            {
                "severity": "INFO",
                "area": "CONTENT SANITIZATION",
                "finding": "'clean_content' / 'raw_clean' distinction mentioned — correctly separating parse vs output concerns",
                "recommendation": "This pattern should be codified: parser input = raw, output = sanitized. See ADR-0042.",
                "rule": "ADR-0042",
            }
        )

    # 6. Classification patterns
    if "pattern" not in classification["counts"]:
        recs.append(
            {
                "severity": "LOW",
                "area": "BUG CLASSIFICATION",
                "finding": "No 'pattern' classification yet — either no recurring bugs found, or misclassified as 'one_off'",
                "recommendation": "Review recent fixes: if the same mistake appears twice, it is 'pattern', not 'one_off'",
                "rule": "AGENTS.md §8.6.2",
            }
        )

    if recs:
        return recs

    # Default: all good
    recs.append(
        {
            "severity": "INFO",
            "area": "OVERALL",
            "finding": "No process issues detected.",
            "recommendation": "Continue following Pre-Fix Thinking Protocol.",
            "rule": "AGENTS.md §8.6",
        }
    )
    return recs


# ── Main Output ──────────────────────────────────────────────────────────


def print_banner(title: str) -> None:
    width = 68
    print()
    print(CYAN + BOLD + "═" * width + RESET)
    print(CYAN + BOLD + f"  {title}".ljust(width) + RESET)
    print(CYAN + BOLD + "═" * width + RESET)


def print_section(num: int, title: str, color: str = BLUE) -> None:
    print()
    print(color + BOLD + f"[{num}] {title}" + RESET)


def print_kv(key: str, value: str, indent: int = 2) -> None:
    prefix = " " * indent
    print(f"{prefix}{DIM}{key}:{RESET} {value}")


def print_severity(severity: str) -> str:
    color = {"CRITICAL": RED, "HIGH": YELLOW, "MEDIUM": MAGENTA, "LOW": DIM, "INFO": DIM}.get(severity, DIM)
    return f"{color}{severity}{RESET}"


def run_analyzer(vc_dir: Path, adr_dir: Path, schema_path: Path, verbose: bool = False) -> int:
    """Run the full retrospective analysis."""
    date = datetime.now().strftime("%Y-%m-%d")
    print_banner(f"AI BUG-FIX PROCESS DIAGNOSTIC  ({date})")

    cards = load_all_vcs(vc_dir)
    adrs = load_all_adrs(adr_dir)

    print_kv("VC files analyzed", str(len(cards)))
    print_kv("ADR files found", str(len(adrs)))
    print_kv("VC directory", str(vc_dir))
    print_kv("ADR directory", str(adr_dir))

    # ── Section 1: Compliance ──────────────────────────────────────────
    compliance = section_compliance(cards)
    print_section(1, "VERIFICATION CARD COMPLIANCE")
    print_kv("Total fixes analyzed", str(compliance["total"]))
    print_kv("Cards compliant", f"{GREEN if compliance['missing_vc'] == 0 else YELLOW}{compliance['compliant']}{RESET}")
    print_kv("Cards with issues", f"{RED if compliance['missing_vc'] > 0 else GREEN}{compliance['missing_vc']}{RESET}")
    if compliance["issues"]:
        for issue in compliance["issues"][:5]:
            print(f"  {YELLOW}⚠{RESET} {issue['card_id']}: {', '.join(issue['problems'])}")

    # ── Section 2: Assumption Patterns ────────────────────────────────────
    assumptions = section_assumption_patterns(cards)
    print_section(2, "ASSUMPTION ERROR PATTERNS")
    print_kv("Total assumptions", str(assumptions["total_assumptions"]))
    sc = assumptions["status_counts"]
    print_kv("  verified_true", f"{GREEN}{sc.get('verified_true', 0)}{RESET}")
    print_kv("  verified_false", f"{YELLOW}{sc.get('verified_false', 0)}{RESET}")
    print_kv("  unverified", f"{RED if sc.get('unverified', 0) > 0 else GREEN}{sc.get('unverified', 0)}{RESET}")
    print_kv("  uncertain", f"{MAGENTA}{sc.get('uncertain', 0)}{RESET}")

    print(f"\n  {DIM}Top assumption keywords:{RESET}")
    for kw, cnt in assumptions["top_keywords"]:
        bar = bar_graph(cnt, assumptions["total_assumptions"], 10)
        print(f"    {CYAN}{kw:<20}{RESET} {bar} {cnt}x")

    if assumptions["verified_wrong"] and verbose:
        print(f"\n  {RED}Verified False (wrong initial assumptions):{RESET}")
        for v in assumptions["verified_wrong"]:
            print(f"    [{v['card_id']}] {v['statement'][:60]}")

    # ── Section 3: Pre-mortem Accuracy ───────────────────────────────────
    premortem = section_premortem(cards)
    print_section(3, "PRE-MORTEM ACCURACY")
    score = premortem["avg_score"]
    max_s = premortem["max_score"]
    score_color = GREEN if score >= 1.5 else YELLOW if score >= 1.0 else RED
    print_kv("Avg pre_mortem score", f"{score_color}{score}/{max_s}{RESET}")
    print_kv("Score guide", "0=vague, 1=conditional, 2=specific variables")

    if verbose:
        for card_pm in premortem["cards"]:
            bar = bar_graph(card_pm["score"], max_s, 6)
            print(f"  {card_pm['card_id']:<40} [{bar}] {card_pm['failure_point_preview']}")

    # ── Section 4: ADR Coverage ──────────────────────────────────────────
    adr_cov = section_adr_coverage(cards, adrs)
    print_section(4, "STRUCTURAL BUG ADR COVERAGE")
    cov_pct = adr_cov["coverage_pct"]
    cov_color = GREEN if cov_pct == 100 else YELLOW if cov_pct >= 50 else RED
    print_kv("Structural fixes", str(adr_cov["structural_count"]))
    print_kv("With ADR", f"{GREEN if adr_cov['missing'] == 0 else YELLOW}{adr_cov['covered']}{RESET}")
    print_kv("Missing ADR", f"{RED if adr_cov['missing'] > 0 else GREEN}{adr_cov['missing']}{RESET}")
    print_kv("Coverage", f"{cov_color}{cov_pct}%{RESET}")
    if adr_cov["related_adrs"]:
        print(f"  {DIM}Related ADRs:{RESET}")
        for adr_id, card_ids in adr_cov["related_adrs"].items():
            print(f"    {CYAN}{adr_id}{RESET} ← {', '.join(card_ids)}")

    # ── Section 5: Classification ─────────────────────────────────────────
    classification = section_classification(cards)
    print_section(5, "BUG CLASSIFICATION DISTRIBUTION")
    for cls, cnt in sorted(classification["counts"].items(), key=lambda x: -x[1]):
        bar = bar_graph(cnt, len(cards), 12)
        print(f"  {CYAN}{cls:<15}{RESET} {bar} {cnt}")

    # ── Section 6: Coaching ────────────────────────────────────────────────
    coaching = section_coaching(compliance, assumptions, premortem, adr_cov, classification, verbose)
    print_section(6, "AI COACHING RECOMMENDATIONS", color=MAGENTA)
    for i, rec in enumerate(coaching, 1):
        sev = print_severity(rec["severity"])
        print(f"\n  {MAGENTA}{i}. [{sev}] {rec['area']}{RESET}")
        print(f"     {DIM}Finding:{RESET} {rec['finding']}")
        print(f"     {DIM}Action:{RESET} {rec['recommendation']}")
        print(f"     {DIM}Rule:{RESET} {DIM}{rec['rule']}{RESET}")

    # ── Summary Bar ──────────────────────────────────────────────────────
    print_banner("PROCESS QUALITY SUMMARY")
    overall = "GOOD" if compliance["missing_vc"] == 0 and adr_cov["missing"] == 0 else "NEEDS WORK"
    overall_color = GREEN if overall == "GOOD" else YELLOW
    print(
        f"  Verification Cards: {'✓' if compliance['missing_vc'] == 0 else '✗'} "
        f"({compliance['compliant']}/{compliance['total']} compliant)"
    )
    print(f"  ADR Coverage:      {'✓' if adr_cov['missing'] == 0 else '✗'} ({adr_cov['coverage_pct']}% covered)")
    print(f"  Overall:          {overall_color}{BOLD}{overall}{RESET}")

    return 0 if overall == "GOOD" else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AI Bug-Fix Process Retrospective Analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python bug_retro_analyzer.py
              python bug_retro_analyzer.py --verbose
              python bug_retro_analyzer.py --vc-dir docs/governance/templates/verification-cards/
        """),
    )
    parser.add_argument("--vc-dir", type=Path, default=VC_DIR, help="Verification Card directory")
    parser.add_argument("--adr-dir", type=Path, default=ADR_DIR, help="ADR directory")
    parser.add_argument("--schema", type=Path, default=SCHEMA_PATH, help="VC schema path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed per-card output")
    parser.add_argument(
        "--check-compliance", action="store_true", help="Exit non-zero if any VC is missing or incomplete"
    )
    args = parser.parse_args()

    exit_code = run_analyzer(args.vc_dir, args.adr_dir, args.schema, args.verbose)

    if args.check_compliance:
        cards = load_all_vcs(args.vc_dir)
        compliance = section_compliance(cards)
        if compliance["missing_vc"] > 0:
            print(f"\n{RED}✗ Compliance check FAILED: {compliance['missing_vc']} VC incomplete{RESET}", file=sys.stderr)
            return 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
