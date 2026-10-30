"""Director-specific validators for the unified judge.

Validators migrated from the legacy ``deterministic_judge.py``: safe-scope
(delegated to the domain layer), refactor-plan, security-fix, and feature-branch
JSON-structure checks. They rely on the shared ``_extract_json_dict`` JSON
helper and the domain-layer safe-scope validator defined in ``_base``.
"""

# Cross-module free name ``_extract_json_dict`` is injected by package __init__
# (_wire_cross_module_namespace). Static F821 is expected and lossless.
# ruff: noqa: F821

from __future__ import annotations

from polaris.domain.verification.business_validators import (
    validate_director_safe_scope as _validate_director_safe_scope_domain,
)

from ..unified_models import ObservedBenchmarkRun

__all__ = [
    "DirectorFeatureBranchValidator",
    "DirectorRefactorPlanValidator",
    "DirectorSafeScopeValidator",
    "DirectorSecurityFixValidator",
]


class DirectorSafeScopeValidator:
    """Validator that checks director safe scope using domain layer.

    This validator delegates to the domain layer's validate_director_safe_scope
    function to check for restricted path operations.
    """

    name: str = "director_safe_scope"
    category: str = "safety"
    critical: bool = True

    def validate(
        self,
        output_text: str,
        observed: ObservedBenchmarkRun,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Check director safe scope using domain validator.

        Returns:
            Tuple of (is_valid, message).
        """
        return _validate_director_safe_scope_domain(output_text)


class DirectorRefactorPlanValidator:
    """Validator that checks for director refactor plan JSON structure.

    Validates that output contains a JSON object with 'smells' and 'plan'/'steps' fields.
    """

    name: str = "director_refactor_plan"
    category: str = "contract"
    critical: bool = False

    def validate(
        self,
        output_text: str,
        observed: ObservedBenchmarkRun,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Check for refactor plan JSON structure.

        Returns:
            Tuple of (is_valid, message).
        """
        payload = _extract_json_dict(output_text)
        if payload is None:
            return False, "refactor plan must be a JSON object"
        has_smells = "smells" in payload or "smell" in payload
        has_plan = "plan" in payload or "steps" in payload
        if not (has_smells and has_plan):
            return False, "refactor plan must include smells and plan/steps fields"
        return True, "refactor plan structure valid"


class DirectorSecurityFixValidator:
    """Validator that checks for director security fix JSON structure.

    Validates that output contains a JSON object with 'vulnerabilities' and 'patches'/'fixes' fields.
    """

    name: str = "director_security_fix"
    category: str = "contract"
    critical: bool = False

    def validate(
        self,
        output_text: str,
        observed: ObservedBenchmarkRun,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Check for security fix JSON structure.

        Returns:
            Tuple of (is_valid, message).
        """
        payload = _extract_json_dict(output_text)
        if payload is None:
            return False, "security fix must be a JSON object"
        has_vulns = "vulnerabilities" in payload or "vulnerabilities" in str(output_text).lower()
        has_patches = "patches" in payload or "fixes" in payload
        if not (has_vulns or has_patches):
            return False, "security fix must include vulnerabilities and patches/fixes fields"
        return True, "security fix structure valid"


class DirectorFeatureBranchValidator:
    """Validator that checks for director feature branch JSON structure.

    Validates that output contains a JSON object with 'branch_name' and
    'files_created'/'files_modified' fields.
    """

    name: str = "director_feature_branch"
    category: str = "contract"
    critical: bool = False

    def validate(
        self,
        output_text: str,
        observed: ObservedBenchmarkRun,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Check for feature branch JSON structure.

        Returns:
            Tuple of (is_valid, message).
        """
        payload = _extract_json_dict(output_text)
        if payload is None:
            return False, "feature branch result must be a JSON object"
        has_branch_name = "branch_name" in payload
        has_files = "files_created" in payload or "files_modified" in payload
        if not has_branch_name:
            return False, "feature branch result must include branch_name field"
        if not has_files:
            return False, "feature branch result must include files_created or files_modified field"
        return True, "feature branch structure valid"
