"""Phase 3: SkillValidationFramework - Three-Layer Defense Model

Integrates L1 (AST), L1.5 (Dependencies), L2 (Semantic), L3 (LLM Judge)
with production-grade features: Borderline triggering, Prompt Jitter,
Cost Circuit Breaker, and Progressive Validation.
"""

from __future__ import annotations

import ast
import logging
import re
import statistics
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from polaris.cells.context.catalog.internal.skill_validator.dependency_verifier import (
    DependencyVerificationEngine,
)

logger = logging.getLogger(__name__)


class ValidationStatus(Enum):
    """Validation status"""
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"  # Borderline case


class ValidationTier(Enum):
    """Validation tier that produced the result"""
    L1_SYNTAX = "L1_syntax"
    L1_5_DEPENDENCY = "L1.5_dependency"
    L2_SEMANTIC = "L2_semantic"
    L3_EXPERT = "L3_expert"


@dataclass(frozen=True)
class ValidationResult:
    """Unified validation result across all tiers"""
    status: ValidationStatus
    tier: ValidationTier
    score: float  # 0.0 - 1.0
    passed: bool
    evidence: dict[str, Any]
    failed_rules: list[str] = field(default_factory=list)
    remediation_hints: list[str] = field(default_factory=list)


@dataclass
class ValidationConfig:
    """Configuration for three-layer defense"""
    # L1 Configuration
    enabled_l1: bool = True

    # L1.5 Configuration
    enabled_l1_5: bool = True
    project_root: Path = field(default_factory=lambda: Path("."))
    internal_modules: list[str] = field(default_factory=list)
    allowed_third_party: list[str] = field(default_factory=list)

    # L2 Configuration
    enabled_l2: bool = True
    l2_threshold_pass: float = 0.95      # [0.95, 1.0] - Pass without L3
    l2_threshold_borderline_low: float = 0.80  # [0.80, 0.95) - Borderline
    # [0.00, 0.80) - Fail immediately

    # L3 Configuration
    enabled_l3: bool = True
    l3_model_strategy: str = "fast"  # fast/standard/premium
    l3_max_daily_calls: int = 100
    l3_daily_budget_usd: float = 10.0
    l3_samples: int = 3  # Number of perspective samples
    l3_temperature: float = 0.3  # Prompt Jitter temperature
    l3_discretize_thresholds: list[float] = field(
        default_factory=lambda: [0.0, 0.6, 0.75, 0.85, 1.0]
    )
    l3_variance_threshold: float = 0.1  # Max stdev for confidence

    # Risk triggers for L3 (always review if matches)
    l3_always_review_patterns: list[str] = field(
        default_factory=lambda: [
            r"core.*contract",
            r"public.*api",
            r"turn.*transaction",
        ]
    )


class InvariantRule(Protocol):
    """Protocol for L2 semantic invariant rules"""
    rule_id: str
    severity: str  # "blocking" | "warning"

    def validate(self, content: str, context: dict[str, Any]) -> tuple[bool, float, dict]:
        """Returns (passed, score, evidence)"""
        ...


class SQLWhereClauseInvariant:
    """L2: SQL must have WHERE or LIMIT"""

    rule_id = "sql_must_have_where_or_limit"
    severity = "blocking"

    SQL_BLOCK_PATTERN = re.compile(
        r'```(?:sql|mysql|postgres|sqlite)?\s*'
        r'(.*?)'
        r'```',
        re.DOTALL | re.IGNORECASE
    )

    def validate(self, content: str, context: dict[str, Any]) -> tuple[bool, float, dict]:
        """Validate SQL blocks for WHERE/LIMIT clauses"""
        violations = []
        valid_count = 0
        total_count = 0

        for match in self.SQL_BLOCK_PATTERN.finditer(content):
            sql = match.group(1).strip()
            if not sql:
                continue

            total_count += 1

            # Normalize: remove comments, standardize whitespace
            sql_clean = self._normalize_sql(sql)

            # Check for DDL statements
            if any(sql_clean.upper().startswith(cmd) for cmd in ['CREATE', 'DROP', 'ALTER', 'INSERT']):
                valid_count += 1
                continue

            # Check for WHERE or LIMIT
            has_where = re.search(r'\bWHERE\b', sql_clean, re.IGNORECASE) is not None
            has_limit = re.search(r'\bLIMIT\b', sql_clean, re.IGNORECASE) is not None

            if has_where or has_limit:
                valid_count += 1
            else:
                violations.append({
                    "line": content[:match.start()].count('\n'),
                    "sql_preview": sql[:100] + "..." if len(sql) > 100 else sql
                })

        if total_count == 0:
            return True, 1.0, {"reason": "No SQL blocks found"}

        score = valid_count / total_count
        passed = len(violations) == 0

        return passed, score, {
            "total_sql_blocks": total_count,
            "valid_blocks": valid_count,
            "violations": violations
        }

    def _normalize_sql(self, sql: str) -> str:
        """Normalize SQL for analysis"""
        # Remove comments
        sql = re.sub(r'--.*?$|/\*.*?\*/', '', sql, flags=re.MULTILINE | re.DOTALL)
        # Normalize whitespace
        sql = re.sub(r'\s+', ' ', sql).strip()
        return sql


class PythonTestPrefixInvariant:
    """L2: Python test functions must start with 'test_' or use pytest decorators"""

    rule_id = "python_tests_must_have_test_prefix"
    severity = "warning"

    def validate(self, content: str, context: dict[str, Any]) -> tuple[bool, float, dict]:
        """Validate Python test function naming"""
        # Extract Python code blocks
        python_blocks = re.findall(
            r'```(?:python|py)\s*\n(.*?)\n```',
            content,
            re.DOTALL | re.IGNORECASE
        )

        if not python_blocks:
            return True, 1.0, {"reason": "No Python blocks found"}

        total_functions = 0
        compliant_functions = 0
        non_compliant = []

        for block in python_blocks:
            try:
                tree = ast.parse(block)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    # Check if looks like a test function
                    is_likely_test = (
                        'test' in node.name.lower() or
                        any(isinstance(d, (ast.Call, ast.Attribute))
                            for d in node.decorator_list)
                    )

                    if not is_likely_test:
                        continue

                    total_functions += 1

                    # Check compliance
                    has_test_prefix = node.name.startswith('test_')
                    has_pytest_decorator = self._has_pytest_decorator(node)

                    if has_test_prefix or has_pytest_decorator:
                        compliant_functions += 1
                    else:
                        non_compliant.append({
                            "name": node.name,
                            "line": node.lineno
                        })

        if total_functions == 0:
            return True, 1.0, {"reason": "No test functions found"}

        score = compliant_functions / total_functions
        passed = score >= 0.95  # Allow 5% exception rate

        return passed, score, {
            "total_test_functions": total_functions,
            "compliant": compliant_functions,
            "non_compliant": non_compliant
        }

    def _has_pytest_decorator(self, node: ast.FunctionDef) -> bool:
        """Check if function has pytest-related decorators"""
        for decorator in node.decorator_list:
            decorator_str = ast.dump(decorator)
            # Check for pytest marks (parametrize, fixture, etc.)
            if any(marker in decorator_str for marker in ['parametrize', 'fixture', 'mark']):
                return True
        return False


class LLMJudge:
    """L3: LLM-as-a-Judge with Prompt Jitter and statistical convergence"""

    def __init__(self, config: ValidationConfig) -> None:
        self.config = config
        self.daily_calls = 0
        self.daily_spent = 0.0

    def can_call(self, estimated_cost: float = 0.05) -> bool:
        """Check if we can afford another L3 call"""
        if self.daily_calls >= self.config.l3_max_daily_calls:
            logger.warning("L3 daily call limit exceeded")
            return False
        if self.daily_spent + estimated_cost > self.config.l3_daily_budget_usd:
            logger.warning("L3 daily budget exceeded")
            return False
        return True

    def evaluate(
        self,
        skill_content: str,
        rubric: str,
        context: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Evaluate skill with Prompt Jitter technique.

        3 samples with different perspectives:
        1. Security Auditor (strict, safety-focused)
        2. Performance Architect (efficiency-focused)
        3. QA Engineer (edge-case focused)
        """
        if not self.can_call():
            return {
                "status": "CIRCUIT_BREAKER_OPEN",
                "recommendation": "HUMAN_REVIEW_REQUIRED",
                "reason": "Cost limits exceeded"
            }

        perspectives = [
            {
                "role": "Security Auditor",
                "focus": "security vulnerabilities, input validation, injection risks",
                "strictness": "very_high"
            },
            {
                "role": "Performance Architect",
                "focus": "algorithmic complexity, resource leaks, scalability issues",
                "strictness": "high"
            },
            {
                "role": "QA Engineer",
                "focus": "edge cases, error handling, boundary conditions",
                "strictness": "high"
            }
        ]

        samples = []
        for perspective in perspectives:
            score = self._call_llm_with_perspective(
                skill_content,
                rubric,
                perspective,
                temperature=self.config.l3_temperature
            )
            samples.append(score)

        self.daily_calls += len(samples)
        self.daily_spent += len(samples) * 0.05  # $0.05 per call estimate

        # Statistical analysis
        discrete_scores = [self._discretize(s) for s in samples]
        majority_vote = max(set(discrete_scores), key=discrete_scores.count)
        variance = statistics.stdev(samples) if len(samples) > 1 else 0.0

        # High variance = low confidence
        if variance > self.config.l3_variance_threshold:
            return {
                "status": "UNCERTAIN",
                "confidence": 1.0 - variance,
                "raw_scores": samples,
                "recommendation": "HUMAN_REVIEW_REQUIRED",
                "reason": f"High variance in perspective scores: {variance:.3f}"
            }

        return {
            "status": "PASS" if majority_vote >= 0.85 else "FAIL",
            "score": statistics.mean(samples),
            "confidence": 1.0 - variance,
            "raw_scores": samples,
            "perspectives": [p["role"] for p in perspectives]
        }

    def _call_llm_with_perspective(
        self,
        content: str,
        rubric: str,
        perspective: dict[str, str],
        temperature: float
    ) -> float:
        """Call LLM with specific perspective"""
        # This would integrate with Polaris LLM client
        # For now, return placeholder
        f"""
You are a {perspective['role']} evaluating code quality.
Focus: {perspective['focus']}
Strictness: {perspective['strictness']}

Evaluate the following skill implementation:

{content}

Evaluation Rubric:
{rubric}

Score from 0.0 to 1.0. Return ONLY the numeric score.
"""
        # Placeholder - would call actual LLM
        logger.debug(f"LLM Judge call with {perspective['role']}")
        return 0.9  # Placeholder

    def _discretize(self, score: float) -> float:
        """Discretize score to reduce boundary jitter"""
        thresholds = self.config.l3_discretize_thresholds
        for i in range(len(thresholds) - 1):
            if thresholds[i] <= score < thresholds[i + 1]:
                # Return midpoint of bucket
                return (thresholds[i] + thresholds[i + 1]) / 2
        return score


class SkillValidationFramework:
    """
    Three-Layer Defense Skill Validation Framework

    Orchestrates L1 → L1.5 → L2 → L3 validation with early exit
    and borderline triggering logic.
    """

    def __init__(self, config: ValidationConfig | None = None) -> None:
        self.config = config or ValidationConfig()

        # Initialize engines
        self.l1_5_engine = DependencyVerificationEngine(
            project_root=self.config.project_root,
            internal_modules=self.config.internal_modules,
            allowed_third_party=self.config.allowed_third_party,
        )

        self.l2_rules: list[InvariantRule] = [
            SQLWhereClauseInvariant(),
            PythonTestPrefixInvariant(),
        ]

        self.l3_judge = LLMJudge(self.config)

    def validate(self, skill_file: Path) -> ValidationResult:
        """
        Execute three-layer defense validation.

        Flow:
        L1 (Syntax) → L1.5 (Deps) → L2 (Semantic) → [Borderline?] → L3 (Expert)
        """
        content = skill_file.read_text(encoding='utf-8')

        # L1: Syntax Validation
        if self.config.enabled_l1:
            l1_result = self._validate_l1_syntax(content)
            if not l1_result.passed:
                return l1_result

        # L1.5: Dependency Validation
        if self.config.enabled_l1_5:
            l1_5_result = self._validate_l1_5_dependencies(content, str(skill_file))
            if not l1_5_result.passed:
                return l1_5_result

        # L2: Semantic Validation
        if self.config.enabled_l2:
            l2_result = self._validate_l2_semantic(content)

            # Borderline logic
            if l2_result.score < self.config.l2_threshold_borderline_low:
                # [0.00, 0.80) - Fatal fail
                return ValidationResult(
                    status=ValidationStatus.REJECTED,
                    tier=ValidationTier.L2_SEMANTIC,
                    score=l2_result.score,
                    passed=False,
                    evidence=l2_result.evidence,
                    failed_rules=l2_result.failed_rules,
                    remediation_hints=l2_result.remediation_hints
                )

            elif (l2_result.score >= self.config.l2_threshold_pass and
                  not self._matches_always_review_pattern(skill_file, content)):
                # [0.95, 1.00] - Excellent, pass without L3
                # Unless matches always-review patterns
                return l2_result

            # [0.80, 0.95) - Borderline, or excellent but high-risk file
            # Trigger L3
            if self.config.enabled_l3:
                return self._validate_l3_expert(content, l2_result)

        # If L2 disabled or L3 disabled, return L2 result
        return ValidationResult(
            status=ValidationStatus.APPROVED,
            tier=ValidationTier.L2_SEMANTIC,
            score=1.0,
            passed=True,
            evidence={"reason": "Validation tiers disabled or skipped"}
        )

    def _validate_l1_syntax(self, content: str) -> ValidationResult:
        """L1: AST Syntax validation"""
        errors = []

        # Try to parse Python code blocks
        python_blocks = re.findall(
            r'```(?:python|py)\s*\n(.*?)\n```',
            content,
            re.DOTALL | re.IGNORECASE
        )

        for i, block in enumerate(python_blocks):
            try:
                ast.parse(block)
            except SyntaxError as e:
                errors.append(f"Block {i+1}: {e}")

        if errors:
            return ValidationResult(
                status=ValidationStatus.REJECTED,
                tier=ValidationTier.L1_SYNTAX,
                score=0.0,
                passed=False,
                evidence={"syntax_errors": errors},
                failed_rules=["valid_python_syntax"],
                remediation_hints=["Fix syntax errors in Python code blocks"]
            )

        return ValidationResult(
            status=ValidationStatus.APPROVED,
            tier=ValidationTier.L1_SYNTAX,
            score=1.0,
            passed=True,
            evidence={"blocks_parsed": len(python_blocks)}
        )

    def _validate_l1_5_dependencies(
        self,
        content: str,
        source_file: str
    ) -> ValidationResult:
        """L1.5: Dependency validation"""
        dep_results = self.l1_5_engine.verify_dependencies(content, source_file)

        hallucinated = [r for r in dep_results if not r.exists]

        if hallucinated:
            return ValidationResult(
                status=ValidationStatus.REJECTED,
                tier=ValidationTier.L1_5_DEPENDENCY,
                score=0.0,
                passed=False,
                evidence={
                    "hallucinated_imports": [
                        {
                            "module": r.node.module_name,
                            "line": r.node.line_number,
                            "suggestion": r.suggestion
                        }
                        for r in hallucinated
                    ]
                },
                failed_rules=["valid_dependencies"],
                remediation_hints=[r.suggestion for r in hallucinated if r.suggestion]
            )

        return ValidationResult(
            status=ValidationStatus.APPROVED,
            tier=ValidationTier.L1_5_DEPENDENCY,
            score=1.0,
            passed=True,
            evidence={"dependencies_checked": len(dep_results)}
        )

    def _validate_l2_semantic(self, content: str) -> ValidationResult:
        """L2: Semantic invariant validation"""
        all_passed = True
        total_score = 1.0
        failed_rules = []
        all_evidence = {}
        all_hints = []

        for rule in self.l2_rules:
            passed, score, evidence = rule.validate(content, {})

            if not passed:
                all_passed = False
                failed_rules.append(rule.rule_id)
                if hasattr(rule, 'remediation_hint'):
                    all_hints.append(rule.remediation_hint)

            # Multiply scores (penalize failures)
            total_score *= score
            all_evidence[rule.rule_id] = evidence

        status = (
            ValidationStatus.APPROVED if all_passed
            else ValidationStatus.NEEDS_REVIEW if total_score >= self.config.l2_threshold_borderline_low
            else ValidationStatus.REJECTED
        )

        return ValidationResult(
            status=status,
            tier=ValidationTier.L2_SEMANTIC,
            score=total_score,
            passed=all_passed,
            evidence=all_evidence,
            failed_rules=failed_rules,
            remediation_hints=all_hints
        )

    def _validate_l3_expert(
        self,
        content: str,
        l2_result: ValidationResult
    ) -> ValidationResult:
        """L3: Expert review with LLM Judge"""
        rubric = """
Evaluate this skill implementation for:
1. Correctness: Does it solve the stated problem?
2. Safety: Are there security risks or injection vulnerabilities?
3. Performance: Are there algorithmic inefficiencies?
4. Edge Cases: Does it handle boundary conditions?
5. Maintainability: Is the code readable and well-structured?

Score: 0.0 (completely wrong) to 1.0 (perfect)
"""

        l3_result = self.l3_judge.evaluate(content, rubric, l2_result.evidence)

        if l3_result.get("status") == "CIRCUIT_BREAKER_OPEN":
            # Budget exceeded, rely on L2
            return ValidationResult(
                status=l2_result.status,
                tier=ValidationTier.L2_SEMANTIC,
                score=l2_result.score,
                passed=l2_result.passed,
                evidence={
                    **l2_result.evidence,
                    "l3_skipped": "Cost circuit breaker open"
                },
                failed_rules=l2_result.failed_rules,
                remediation_hints=l2_result.remediation_hints
            )

        if l3_result.get("status") == "UNCERTAIN":
            return ValidationResult(
                status=ValidationStatus.NEEDS_REVIEW,
                tier=ValidationTier.L3_EXPERT,
                score=l3_result.get("score", 0.0),
                passed=False,
                evidence=l3_result,
                failed_rules=["expert_consensus_uncertain"],
                remediation_hints=["Human review required due to high variance in expert scores"]
            )

        passed = l3_result.get("status") == "PASS"

        return ValidationResult(
            status=ValidationStatus.APPROVED if passed else ValidationStatus.REJECTED,
            tier=ValidationTier.L3_EXPERT,
            score=l3_result.get("score", 0.0),
            passed=passed,
            evidence=l3_result,
            failed_rules=[] if passed else ["expert_review_failed"],
            remediation_hints=[] if passed else ["Address issues identified by expert review"]
        )

    def _matches_always_review_pattern(self, skill_file: Path, content: str) -> bool:
        """Check if file matches high-risk patterns requiring L3 review"""
        file_str = str(skill_file)
        for pattern in self.config.l3_always_review_patterns:
            if re.search(pattern, file_str, re.IGNORECASE):
                return True
            if re.search(pattern, content[:1000], re.IGNORECASE):  # Check first 1000 chars
                return True
        return False


# Example usage
if __name__ == "__main__":
    config = ValidationConfig(
        project_root=Path("/path/to/polaris"),
        internal_modules=["polaris", "kernelone"],
    )

    framework = SkillValidationFramework(config)

    # Example skill file content
    test_skill = """
# My Skill

Here's a SQL query:

```sql
SELECT * FROM users WHERE id = 1;
```

And a test:

```python
def test_user_query():
    result = execute_query("SELECT * FROM users WHERE id = 1")
    assert len(result) == 1
```
"""

    # Write to temp file and validate
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
        f.write(test_skill)
        temp_path = Path(f.name)

    result = framework.validate(temp_path)
    print(f"Validation Result: {result}")

    temp_path.unlink()
