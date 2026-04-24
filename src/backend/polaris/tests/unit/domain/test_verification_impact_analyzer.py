"""Tests for polaris.domain.verification.impact_analyzer."""

from __future__ import annotations

from polaris.domain.verification.impact_analyzer import (
    ImpactAnalyzer,
    ImpactResult,
    RiskLevel,
    analyze_impact,
    assess_patch_risk,
)


class TestRiskLevel:
    def test_values(self) -> None:
        assert RiskLevel.LOW.value == "low"
        assert RiskLevel.MEDIUM.value == "medium"
        assert RiskLevel.HIGH.value == "high"
        assert RiskLevel.CRITICAL.value == "critical"


class TestImpactResult:
    def test_score_clamped_low(self) -> None:
        result = ImpactResult(score=-5, level=RiskLevel.LOW, reasons=[])
        assert result.score == 0

    def test_score_clamped_high(self) -> None:
        result = ImpactResult(score=15, level=RiskLevel.CRITICAL, reasons=[])
        assert result.score == 10

    def test_requires_strict_verification(self) -> None:
        result = ImpactResult(score=7, level=RiskLevel.HIGH, reasons=[])
        assert result.requires_strict_verification is True

    def test_can_use_fast_lane(self) -> None:
        result = ImpactResult(score=3, level=RiskLevel.LOW, reasons=[])
        assert result.can_use_fast_lane is True

    def test_to_dict(self) -> None:
        result = ImpactResult(score=5, level=RiskLevel.MEDIUM, reasons=["r1"])
        d = result.to_dict()
        assert d["score"] == 5
        assert d["level"] == "medium"
        assert d["reasons"] == ["r1"]


class TestImpactAnalyzer:
    def test_empty_files(self) -> None:
        analyzer = ImpactAnalyzer()
        result = analyzer.analyze([])
        assert result.score == 0
        assert result.level == RiskLevel.LOW

    def test_test_file_low_risk(self) -> None:
        analyzer = ImpactAnalyzer()
        result = analyzer.analyze(["tests/test_main.py"])
        assert result.score <= 3
        assert result.level == RiskLevel.LOW
        assert any("Test file" in r for r in result.reasons)

    def test_doc_file_low_risk(self) -> None:
        analyzer = ImpactAnalyzer()
        result = analyzer.analyze(["README.md"])
        assert result.score <= 2
        assert result.level == RiskLevel.LOW

    def test_security_file_critical(self) -> None:
        analyzer = ImpactAnalyzer()
        result = analyzer.analyze(["src/auth/login.py"])
        assert result.score >= 9
        assert result.level == RiskLevel.CRITICAL
        assert any("Security" in r for r in result.reasons)

    def test_billing_file_critical(self) -> None:
        analyzer = ImpactAnalyzer()
        result = analyzer.analyze(["src/billing/payment.py"])
        assert result.score >= 9
        assert result.level == RiskLevel.CRITICAL

    def test_core_file_high(self) -> None:
        analyzer = ImpactAnalyzer()
        result = analyzer.analyze(["src/database/migration.py"])
        assert result.score >= 7
        assert result.level == RiskLevel.HIGH

    def test_api_file_medium_high(self) -> None:
        analyzer = ImpactAnalyzer()
        result = analyzer.analyze(["src/api/endpoints.py"])
        assert result.score >= 6

    def test_critical_infrastructure(self) -> None:
        analyzer = ImpactAnalyzer()
        result = analyzer.analyze(["Dockerfile"])
        assert result.score >= 9
        assert any("Critical" in r for r in result.reasons)

    def test_large_change_set_boost(self) -> None:
        analyzer = ImpactAnalyzer()
        files = [f"src/file_{i}.py" for i in range(25)]
        result = analyzer.analyze(files)
        assert any("Large change" in r for r in result.reasons)

    def test_content_analysis_security(self) -> None:
        analyzer = ImpactAnalyzer()
        result = analyzer.analyze(
            ["src/utils.py"],
            file_contents={"src/utils.py": "password = 'secret123'"},
        )
        assert result.score >= 8
        assert any("security-sensitive" in r for r in result.reasons)

    def test_content_analysis_migration(self) -> None:
        analyzer = ImpactAnalyzer()
        result = analyzer.analyze(
            ["src/db.py"],
            file_contents={"src/db.py": "def migration(): pass"},
        )
        assert result.score >= 8
        assert any("Database migration" in r for r in result.reasons)

    def test_content_analysis_breaking(self) -> None:
        analyzer = ImpactAnalyzer()
        result = analyzer.analyze(
            ["src/api.py"],
            file_contents={"src/api.py": "@deprecated"},
        )
        assert result.score >= 7
        assert any("breaking" in r for r in result.reasons)

    def test_recommendations_critical(self) -> None:
        analyzer = ImpactAnalyzer()
        result = analyzer.analyze(["src/auth.py"])
        assert any("CRITICAL" in r for r in result.recommendations)

    def test_recommendations_low(self) -> None:
        analyzer = ImpactAnalyzer()
        result = analyzer.analyze(["tests/test.py"])
        assert any("Fast lane" in r for r in result.recommendations)


class TestAnalyzeImpact:
    def test_convenience_function(self) -> None:
        result = analyze_impact(["src/main.py"])
        assert isinstance(result, ImpactResult)


class TestAssessPatchRisk:
    def test_returns_dict(self) -> None:
        result = assess_patch_risk(["src/main.py"])
        assert isinstance(result, dict)
        assert "score" in result
        assert "level" in result
        assert "total_files" in result
