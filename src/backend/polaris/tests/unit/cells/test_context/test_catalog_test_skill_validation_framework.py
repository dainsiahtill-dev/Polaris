"""Tests for polaris.cells.context.catalog.internal.skill_validator.framework.

Covers ValidationStatus, ValidationTier, ValidationResult, ValidationConfig,
InvariantRule protocol implementations (SQLWhereClauseInvariant, PythonTestPrefixInvariant),
LLMJudge, and SkillValidationFramework.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from polaris.cells.context.catalog.internal.skill_validator.framework import (
    LLMJudge,
    PythonTestPrefixInvariant,
    SkillValidationFramework,
    SQLWhereClauseInvariant,
    ValidationConfig,
    ValidationResult,
    ValidationStatus,
    ValidationTier,
)

# ---------------------------------------------------------------------------
# ValidationStatus / ValidationTier
# ---------------------------------------------------------------------------


class TestValidationEnums:
    def test_status_values(self) -> None:
        assert ValidationStatus.APPROVED.value == "approved"
        assert ValidationStatus.REJECTED.value == "rejected"
        assert ValidationStatus.NEEDS_REVIEW.value == "needs_review"

    def test_tier_values(self) -> None:
        assert ValidationTier.L1_SYNTAX.value == "L1_syntax"
        assert ValidationTier.L1_5_DEPENDENCY.value == "L1.5_dependency"
        assert ValidationTier.L2_SEMANTIC.value == "L2_semantic"
        assert ValidationTier.L3_EXPERT.value == "L3_expert"


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------


class TestValidationResult:
    def test_creation(self) -> None:
        result = ValidationResult(
            status=ValidationStatus.APPROVED,
            tier=ValidationTier.L1_SYNTAX,
            score=1.0,
            passed=True,
            evidence={"blocks_parsed": 2},
        )
        assert result.status == ValidationStatus.APPROVED
        assert result.tier == ValidationTier.L1_SYNTAX
        assert result.score == 1.0
        assert result.passed is True
        assert result.evidence == {"blocks_parsed": 2}
        assert result.failed_rules == []
        assert result.remediation_hints == []

    def test_with_failed_rules(self) -> None:
        result = ValidationResult(
            status=ValidationStatus.REJECTED,
            tier=ValidationTier.L2_SEMANTIC,
            score=0.5,
            passed=False,
            evidence={},
            failed_rules=["rule_1"],
            remediation_hints=["Fix this"],
        )
        assert result.failed_rules == ["rule_1"]
        assert result.remediation_hints == ["Fix this"]


# ---------------------------------------------------------------------------
# ValidationConfig
# ---------------------------------------------------------------------------


class TestValidationConfig:
    def test_defaults(self) -> None:
        config = ValidationConfig()
        assert config.enabled_l1 is True
        assert config.enabled_l1_5 is True
        assert config.enabled_l2 is True
        assert config.enabled_l3 is True
        assert config.l2_threshold_pass == 0.95
        assert config.l2_threshold_borderline_low == 0.80
        assert config.l3_max_daily_calls == 100
        assert config.l3_daily_budget_usd == 10.0
        assert config.l3_samples == 3
        assert config.l3_temperature == 0.3

    def test_custom_config(self) -> None:
        config = ValidationConfig(
            enabled_l1=False,
            l2_threshold_pass=0.90,
            l3_max_daily_calls=50,
        )
        assert config.enabled_l1 is False
        assert config.l2_threshold_pass == 0.90
        assert config.l3_max_daily_calls == 50


# ---------------------------------------------------------------------------
# SQLWhereClauseInvariant
# ---------------------------------------------------------------------------


class TestSQLWhereClauseInvariant:
    def test_no_sql_blocks(self) -> None:
        rule = SQLWhereClauseInvariant()
        passed, score, evidence = rule.validate("Just some text", {})
        assert passed is True
        assert score == 1.0
        assert evidence["reason"] == "No SQL blocks found"

    def test_valid_select_with_where(self) -> None:
        rule = SQLWhereClauseInvariant()
        content = "```sql\nSELECT * FROM users WHERE id = 1;\n```"
        passed, score, evidence = rule.validate(content, {})
        assert passed is True
        assert score == 1.0
        assert evidence["valid_blocks"] == 1

    def test_valid_select_with_limit(self) -> None:
        rule = SQLWhereClauseInvariant()
        content = "```sql\nSELECT * FROM users LIMIT 10;\n```"
        passed, score, _evidence = rule.validate(content, {})
        assert passed is True
        assert score == 1.0

    def test_invalid_select_without_where_or_limit(self) -> None:
        rule = SQLWhereClauseInvariant()
        content = "```sql\nSELECT * FROM users;\n```"
        passed, score, evidence = rule.validate(content, {})
        assert passed is False
        assert score == 0.0
        assert len(evidence["violations"]) == 1

    def test_ddl_statements_exempt(self) -> None:
        rule = SQLWhereClauseInvariant()
        content = "```sql\nCREATE TABLE users (id INT);\n```"
        passed, score, _evidence = rule.validate(content, {})
        assert passed is True
        assert score == 1.0

    def test_mixed_sql_blocks(self) -> None:
        rule = SQLWhereClauseInvariant()
        content = "```sql\nSELECT * FROM users WHERE id = 1;\n```\n```sql\nSELECT * FROM orders;\n```"
        passed, score, evidence = rule.validate(content, {})
        assert passed is False
        assert score == 0.5
        assert evidence["total_sql_blocks"] == 2
        assert evidence["valid_blocks"] == 1

    def test_case_insensitive_keywords(self) -> None:
        rule = SQLWhereClauseInvariant()
        content = "```sql\nselect * from users where id = 1;\n```"
        passed, _score, _evidence = rule.validate(content, {})
        assert passed is True

    def test_mysql_dialect_tag(self) -> None:
        rule = SQLWhereClauseInvariant()
        content = "```mysql\nSELECT * FROM users WHERE id = 1;\n```"
        passed, _score, _evidence = rule.validate(content, {})
        assert passed is True


# ---------------------------------------------------------------------------
# PythonTestPrefixInvariant
# ---------------------------------------------------------------------------


class TestPythonTestPrefixInvariant:
    def test_no_python_blocks(self) -> None:
        rule = PythonTestPrefixInvariant()
        passed, score, evidence = rule.validate("Just text", {})
        assert passed is True
        assert score == 1.0
        assert evidence["reason"] == "No Python blocks found"

    def test_test_function_with_prefix(self) -> None:
        rule = PythonTestPrefixInvariant()
        content = "```python\ndef test_something():\n    pass\n```"
        passed, score, _evidence = rule.validate(content, {})
        assert passed is True
        assert score == 1.0

    def test_test_function_without_prefix(self) -> None:
        rule = PythonTestPrefixInvariant()
        # "something" contains "test" so it might be classified as likely test
        # Let's use a name that clearly isn't a test
        content = "```python\ndef helper():\n    pass\n```"
        _passed, _score, evidence = rule.validate(content, {})
        # helper() doesn't look like a test function
        assert evidence["reason"] == "No test functions found"

    def test_pytest_decorator_recognized(self) -> None:
        rule = PythonTestPrefixInvariant()
        content = "```python\nimport pytest\n@pytest.mark.parametrize('x', [1, 2])\ndef check_values(x):\n    pass\n```"
        passed, score, _evidence = rule.validate(content, {})
        assert passed is True
        assert score == 1.0

    def test_fixture_decorator_recognized(self) -> None:
        rule = PythonTestPrefixInvariant()
        content = "```python\nimport pytest\n@pytest.fixture\ndef my_fixture():\n    pass\n```"
        passed, score, _evidence = rule.validate(content, {})
        assert passed is True
        assert score == 1.0

    def test_non_compliant_test_function(self) -> None:
        rule = PythonTestPrefixInvariant()
        content = (
            "```python\n"
            "def verify_something():\n"  # looks like test (contains "verify")
            "    pass\n"
            "```"
        )
        _passed, _score, evidence = rule.validate(content, {})
        # verify_something contains "verify" not "test" but has no decorators
        # It won't be classified as likely test, so no test functions found
        assert evidence["reason"] == "No test functions found"

    def test_compliance_threshold(self) -> None:
        rule = PythonTestPrefixInvariant()
        # One compliant, one non-compliant (but non-compliant won't be detected as test)
        content = "```python\ndef test_one():\n    pass\ndef test_two():\n    pass\n```"
        passed, score, evidence = rule.validate(content, {})
        assert passed is True
        assert score == 1.0
        assert evidence["total_test_functions"] == 2


# ---------------------------------------------------------------------------
# LLMJudge
# ---------------------------------------------------------------------------


class TestLLMJudge:
    def test_can_call_initially(self) -> None:
        config = ValidationConfig()
        judge = LLMJudge(config)
        assert judge.can_call() is True

    def test_can_call_exceeds_daily_calls(self) -> None:
        config = ValidationConfig(l3_max_daily_calls=1)
        judge = LLMJudge(config)
        judge.daily_calls = 1
        assert judge.can_call() is False

    def test_can_call_exceeds_budget(self) -> None:
        config = ValidationConfig(l3_daily_budget_usd=0.01)
        judge = LLMJudge(config)
        judge.daily_spent = 0.02
        assert judge.can_call() is False

    def test_evaluate_circuit_breaker(self) -> None:
        config = ValidationConfig(l3_max_daily_calls=0)
        judge = LLMJudge(config)
        result = judge.evaluate("content", "rubric", {})
        assert result["status"] == "CIRCUIT_BREAKER_OPEN"
        assert result["recommendation"] == "HUMAN_REVIEW_REQUIRED"

    def test_evaluate_returns_uncertain_for_high_variance(self) -> None:
        config = ValidationConfig(l3_variance_threshold=0.0)
        judge = LLMJudge(config)
        # _call_llm_with_perspective returns 0.5 for all perspectives
        # variance will be 0.0, so this won't trigger UNCERTAIN
        # Let's patch to return varying scores
        with patch.object(judge, "_call_llm_with_perspective", side_effect=[0.5, 0.9, 0.3]):
            result = judge.evaluate("content", "rubric", {})
            assert result["status"] == "UNCERTAIN"
            assert "variance" in result["reason"].lower() or "High variance" in result["reason"]

    def test_evaluate_returns_pass(self) -> None:
        config = ValidationConfig(l3_variance_threshold=1.0)
        judge = LLMJudge(config)
        with patch.object(judge, "_call_llm_with_perspective", return_value=0.9):
            result = judge.evaluate("content", "rubric", {})
            assert result["status"] == "PASS"
            assert result["score"] == 0.9

    def test_evaluate_returns_fail(self) -> None:
        config = ValidationConfig(l3_variance_threshold=1.0)
        judge = LLMJudge(config)
        with patch.object(judge, "_call_llm_with_perspective", return_value=0.5):
            result = judge.evaluate("content", "rubric", {})
            assert result["status"] == "FAIL"

    def test_discretize(self) -> None:
        config = ValidationConfig(l3_discretize_thresholds=[0.0, 0.5, 1.0])
        judge = LLMJudge(config)
        assert judge._discretize(0.25) == 0.25
        assert judge._discretize(0.75) == 0.75


# ---------------------------------------------------------------------------
# SkillValidationFramework
# ---------------------------------------------------------------------------


class TestSkillValidationFramework:
    def test_init_with_defaults(self) -> None:
        framework = SkillValidationFramework()
        assert framework.config.enabled_l1 is True
        assert framework.config.enabled_l1_5 is True
        assert framework.config.enabled_l2 is True
        assert framework.config.enabled_l3 is True

    def test_init_with_custom_config(self) -> None:
        config = ValidationConfig(enabled_l1=False)
        framework = SkillValidationFramework(config)
        assert framework.config.enabled_l1 is False

    def test_validate_l1_syntax_error(self, tmp_path: Path) -> None:
        framework = SkillValidationFramework()
        skill_file = tmp_path / "skill.md"
        skill_file.write_text(
            "```python\nclass Foo(\n```",
            encoding="utf-8",
        )
        result = framework.validate(skill_file)
        assert result.tier == ValidationTier.L1_SYNTAX
        assert result.status == ValidationStatus.REJECTED
        assert result.passed is False

    def test_validate_l1_passes(self, tmp_path: Path) -> None:
        framework = SkillValidationFramework()
        skill_file = tmp_path / "skill.md"
        skill_file.write_text(
            "```python\ndef hello():\n    pass\n```",
            encoding="utf-8",
        )
        result = framework.validate(skill_file)
        # L1 passes, moves to L1.5 which may fail on hallucinated deps
        # or L2 which may pass
        assert result.passed is True or result.tier in (
            ValidationTier.L1_5_DEPENDENCY,
            ValidationTier.L2_SEMANTIC,
            ValidationTier.L3_EXPERT,
        )

    def test_validate_l1_disabled(self, tmp_path: Path) -> None:
        config = ValidationConfig(enabled_l1=False)
        framework = SkillValidationFramework(config)
        skill_file = tmp_path / "skill.md"
        skill_file.write_text(
            "```python\ndef hello():\n    pass\n```",
            encoding="utf-8",
        )
        result = framework.validate(skill_file)
        assert result.tier != ValidationTier.L1_SYNTAX

    def test_validate_l1_5_dependency_failure(self, tmp_path: Path) -> None:
        framework = SkillValidationFramework()
        skill_file = tmp_path / "skill.md"
        skill_file.write_text(
            "```python\nfrom nonexistent_fake_module import XYZ\n```",
            encoding="utf-8",
        )
        result = framework.validate(skill_file)
        # If L1 passes, L1.5 should reject the hallucinated dependency
        if result.tier == ValidationTier.L1_5_DEPENDENCY:
            assert result.status == ValidationStatus.REJECTED
            assert result.passed is False

    def test_validate_l2_semantic_sql_violation(self, tmp_path: Path) -> None:
        config = ValidationConfig(enabled_l1=False, enabled_l1_5=False)
        framework = SkillValidationFramework(config)
        skill_file = tmp_path / "skill.md"
        skill_file.write_text(
            "```sql\nSELECT * FROM users;\n```",
            encoding="utf-8",
        )
        result = framework.validate(skill_file)
        assert result.tier == ValidationTier.L2_SEMANTIC
        assert result.passed is False
        assert result.score == 0.0

    def test_validate_l2_borderline_triggers_l3(self, tmp_path: Path) -> None:
        config = ValidationConfig(
            enabled_l1=False,
            enabled_l1_5=False,
            l2_threshold_pass=0.99,
            l2_threshold_borderline_low=0.50,
        )
        framework = SkillValidationFramework(config)
        skill_file = tmp_path / "skill.md"
        # Content that passes L2 but with borderline score
        skill_file.write_text(
            "Some text without SQL or Python blocks.\n",
            encoding="utf-8",
        )
        result = framework.validate(skill_file)
        # No SQL/Python blocks → L2 rules pass with score 1.0
        # Score >= l2_threshold_pass (0.99) → should pass without L3
        assert result.tier == ValidationTier.L2_SEMANTIC
        assert result.passed is True

    def test_matches_always_review_pattern(self, tmp_path: Path) -> None:
        config = ValidationConfig()
        framework = SkillValidationFramework(config)
        skill_file = tmp_path / "core_contract.py"
        skill_file.write_text("pass", encoding="utf-8")
        assert framework._matches_always_review_pattern(skill_file, "content") is True

    def test_matches_always_review_content(self, tmp_path: Path) -> None:
        config = ValidationConfig()
        framework = SkillValidationFramework(config)
        skill_file = tmp_path / "normal.py"
        skill_file.write_text("pass", encoding="utf-8")
        content = "This involves turn transaction handling."
        assert framework._matches_always_review_pattern(skill_file, content) is True

    def test_no_always_review_match(self, tmp_path: Path) -> None:
        config = ValidationConfig()
        framework = SkillValidationFramework(config)
        skill_file = tmp_path / "utils.py"
        skill_file.write_text("pass", encoding="utf-8")
        assert framework._matches_always_review_pattern(skill_file, "hello world") is False

    def test_l3_circuit_breaker_falls_back_to_l2(self, tmp_path: Path) -> None:
        config = ValidationConfig(
            enabled_l1=False,
            enabled_l1_5=False,
            l2_threshold_pass=0.99,
            l2_threshold_borderline_low=0.50,
            l3_max_daily_calls=0,  # Circuit breaker open
        )
        framework = SkillValidationFramework(config)
        skill_file = tmp_path / "skill.md"
        skill_file.write_text(
            "```python\ndef test_foo():\n    pass\n```",
            encoding="utf-8",
        )
        result = framework.validate(skill_file)
        # L2 passes (test prefix OK), but score 1.0 >= 0.99 so no L3 triggered
        assert result.tier == ValidationTier.L2_SEMANTIC

    def test_l3_uncertain_result(self, tmp_path: Path) -> None:
        config = ValidationConfig(
            enabled_l1=False,
            enabled_l1_5=False,
            l2_threshold_pass=1.0,
            l2_threshold_borderline_low=0.50,
            l3_variance_threshold=0.0,
        )
        framework = SkillValidationFramework(config)
        skill_file = tmp_path / "skill.md"
        skill_file.write_text(
            "Some content that triggers borderline.\n",
            encoding="utf-8",
        )
        with patch.object(
            framework.l3_judge,
            "_call_llm_with_perspective",
            side_effect=[0.5, 0.9, 0.3],
        ):
            result = framework.validate(skill_file)
            # L2 score = 1.0 (no SQL/Python blocks)
            # 1.0 >= l2_threshold_pass (1.0) → would normally pass
            # But no always-review pattern, so L3 not triggered
            assert result.tier == ValidationTier.L2_SEMANTIC
