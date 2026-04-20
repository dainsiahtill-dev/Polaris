"""Impact Analyzer - Assess risk of file changes for governance decisions.

Provides impact scoring (0-10 scale) to determine verification strictness.
High-impact changes require more rigorous verification.

Migrated from: scripts/director/iteration/verification.py (assess_patch_risk)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RiskLevel(Enum):
    """Risk level classification."""

    LOW = "low"  # 0-3: Safe changes (tests, docs)
    MEDIUM = "medium"  # 4-6: Standard changes (features)
    HIGH = "high"  # 7-8: Risky changes (core modules)
    CRITICAL = "critical"  # 9-10: Dangerous changes (security, billing)


@dataclass(frozen=True)
class ImpactResult:
    """Result of impact analysis."""

    score: int  # 0-10
    level: RiskLevel
    reasons: list[str]
    file_scores: dict[str, int] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.score < 0:
            object.__setattr__(self, "score", 0)
        elif self.score > 10:
            object.__setattr__(self, "score", 10)

    @property
    def requires_strict_verification(self) -> bool:
        """Whether this change requires strict verification."""
        return self.score >= 7

    @property
    def can_use_fast_lane(self) -> bool:
        """Whether this change qualifies for fast lane processing."""
        return self.score <= 3

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "score": self.score,
            "level": self.level.value,
            "reasons": self.reasons,
            "file_scores": self.file_scores,
            "recommendations": self.recommendations,
            "requires_strict_verification": self.requires_strict_verification,
            "can_use_fast_lane": self.can_use_fast_lane,
        }


class ImpactAnalyzer:
    """Analyzes impact of file changes for risk assessment."""

    # High-risk patterns that require strict verification
    HIGH_RISK_PATTERNS = {
        "security": ["auth", "login", "password", "token", "secret", "credential", "permission", "acl"],
        "billing": ["billing", "payment", "invoice", "charge", "subscription", "price"],
        "core": ["database", "migration", "schema", "config", "core", "base"],
        "api": ["api", "endpoint", "router", "controller"],
    }

    # Low-risk patterns that can use fast lane
    LOW_RISK_PATTERNS = {
        "tests": ["test", "spec", "__tests__", "_test.", ".test."],
        "docs": [".md", ".rst", ".txt", "readme", "changelog", "license"],
        "assets": [".png", ".jpg", ".svg", ".css", ".scss", ".less"],
        "config": [".json", ".yaml", ".yml", ".toml"],
    }

    # Critical file patterns
    CRITICAL_PATTERNS = [
        ".env",
        "dockerfile",
        "docker-compose",
        "kubernetes",
        "terraform",
        "cdk",
        "infrastructure",
        "deploy",
    ]

    def __init__(self, workspace: str = ".") -> None:
        self.workspace = workspace

    def analyze(
        self,
        changed_files: list[str],
        file_contents: dict[str, str] | None = None,
    ) -> ImpactResult:
        """Analyze impact of changed files.

        Args:
            changed_files: List of changed file paths
            file_contents: Optional map of file path to content for deeper analysis

        Returns:
            ImpactResult with score and recommendations
        """
        if not changed_files:
            return ImpactResult(
                score=0,
                level=RiskLevel.LOW,
                reasons=["No files changed"],
            )

        file_scores: dict[str, int] = {}
        all_reasons: set[str] = set()
        recommendations: list[str] = []

        for file_path in changed_files:
            score, reasons = self._analyze_file(file_path, file_contents.get(file_path) if file_contents else None)
            file_scores[file_path] = score
            all_reasons.update(reasons)

        # Calculate overall score (max of file scores)
        overall_score = max(file_scores.values()) if file_scores else 0

        # Adjust based on number of files
        if len(changed_files) > 20:
            overall_score = min(10, overall_score + 1)
            all_reasons.add("Large change set (>20 files)")
        elif len(changed_files) > 50:
            overall_score = min(10, overall_score + 2)
            all_reasons.add("Very large change set (>50 files)")

        # Determine level
        level = self._score_to_level(overall_score)

        # Generate recommendations
        recommendations = self._generate_recommendations(overall_score, changed_files, list(all_reasons))

        return ImpactResult(
            score=overall_score,
            level=level,
            reasons=sorted(all_reasons),
            file_scores=file_scores,
            recommendations=recommendations,
        )

    def _analyze_file(self, file_path: str, content: str | None = None) -> tuple[int, list[str]]:
        """Analyze a single file for impact.

        Returns:
            Tuple of (score, reasons)
        """
        score = 5  # Default medium score
        reasons: list[str] = []

        normalized = file_path.lower().replace("\\", "/")
        basename = os.path.basename(normalized)

        # Check critical patterns
        for pattern in self.CRITICAL_PATTERNS:
            if pattern in normalized:
                score = 9
                reasons.append(f"Critical infrastructure file: {basename}")
                break

        # Check high-risk patterns
        for category, patterns in self.HIGH_RISK_PATTERNS.items():
            for pattern in patterns:
                if pattern in normalized:
                    if category == "security":
                        score = max(score, 9)
                        reasons.append(f"Security-related file: {basename}")
                    elif category == "billing":
                        score = max(score, 9)
                        reasons.append(f"Billing/payment file: {basename}")
                    elif category == "core":
                        score = max(score, 7)
                        reasons.append(f"Core infrastructure: {basename}")
                    elif category == "api":
                        score = max(score, 6)
                        reasons.append(f"API endpoint: {basename}")
                    break

        # Check low-risk patterns
        for category, patterns in self.LOW_RISK_PATTERNS.items():
            for pattern in patterns:
                if pattern in normalized:
                    if category == "tests":
                        score = min(score, 3)
                        reasons.append(f"Test file: {basename}")
                    elif category == "docs":
                        score = min(score, 2)
                        reasons.append(f"Documentation: {basename}")
                    elif category == "assets":
                        score = min(score, 2)
                        reasons.append(f"Static asset: {basename}")
                    elif category == "config" and score == 5:
                        score = min(score, 4)
                        reasons.append(f"Configuration file: {basename}")
                    break

        # Content analysis if available
        if content:
            content_score, content_reasons = self._analyze_content(content)
            score = max(score, content_score)
            reasons.extend(content_reasons)

        return min(10, max(0, score)), reasons

    def _analyze_content(self, content: str) -> tuple[int, list[str]]:
        """Analyze file content for impact indicators."""
        score = 0
        reasons: list[str] = []

        content_lower = content.lower()

        # Security indicators
        security_keywords = ["password", "secret", "token", "auth", "credential", "encrypt"]
        if any(kw in content_lower for kw in security_keywords):
            score = max(score, 8)
            reasons.append("Contains security-sensitive keywords")

        # Database migration indicators
        if "migration" in content_lower or "schema" in content_lower:
            score = max(score, 8)
            reasons.append("Database migration detected")

        # API breaking change indicators
        breaking_keywords = ["deprecated", "breaking", "removed", "deleted"]
        if any(kw in content_lower for kw in breaking_keywords):
            score = max(score, 7)
            reasons.append("Potential breaking change detected")

        return score, reasons

    def _score_to_level(self, score: int) -> RiskLevel:
        """Convert score to risk level."""
        if score <= 3:
            return RiskLevel.LOW
        elif score <= 6:
            return RiskLevel.MEDIUM
        elif score <= 8:
            return RiskLevel.HIGH
        else:
            return RiskLevel.CRITICAL

    def _generate_recommendations(
        self,
        score: int,
        changed_files: list[str],
        reasons: list[str],
    ) -> list[str]:
        """Generate verification recommendations based on impact."""
        recommendations: list[str] = []

        if score >= 9:
            recommendations.append("CRITICAL: Require senior review before deployment")
            recommendations.append("Run full integration test suite")
            recommendations.append("Verify backward compatibility")
        elif score >= 7:
            recommendations.append("HIGH: Run type checking and linting")
            recommendations.append("Verify no broken imports")
            recommendations.append("Check test coverage")
        elif score >= 4:
            recommendations.append("MEDIUM: Run basic verification")
            recommendations.append("Check for obvious errors")
        else:
            recommendations.append("LOW: Fast lane eligible")
            recommendations.append("Basic syntax check only")

        # File-specific recommendations
        test_files = [f for f in changed_files if "test" in f.lower()]
        if test_files and len(test_files) < len(changed_files):
            recommendations.append(f"Verify {len(test_files)} test files are properly linked to source")

        return recommendations


def analyze_impact(
    changed_files: list[str],
    workspace: str = ".",
    file_contents: dict[str, str] | None = None,
) -> ImpactResult:
    """Convenience function to analyze impact of file changes.

    Args:
        changed_files: List of changed file paths
        workspace: Workspace root
        file_contents: Optional map of file path to content

    Returns:
        ImpactResult with risk assessment
    """
    analyzer = ImpactAnalyzer(workspace)
    return analyzer.analyze(changed_files, file_contents)


def assess_patch_risk(
    changed_files: list[str],
    file_contents: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Legacy-compatible patch risk assessment.

    Returns dict format compatible with old Director's assess_patch_risk.
    """
    result = analyze_impact(changed_files, file_contents=file_contents)
    return {
        "score": result.score,
        "level": result.level.value,
        "max_file_score": max(result.file_scores.values()) if result.file_scores else 0,
        "total_files": len(changed_files),
        "high_risk_files": sum(1 for s in result.file_scores.values() if s >= 7),
    }
