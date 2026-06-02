"""Tests for structured Director write policy gate."""

from __future__ import annotations

import json

from polaris.domain.verification.director_policy_gate import (
    diff_package_manifest,
    parse_agents_write_policy,
    validate_director_write_policy,
)


def test_parse_agents_write_policy_extracts_forbidden_files() -> None:
    policy = parse_agents_write_policy(
        """
        # Project Rules
        禁止修改 package.json 和 src/generated/schema.ts
        Do not write docs/locked or webpack.config.js.
        """
    )

    paths = {rule.path for rule in policy.forbidden_paths}

    assert "package.json" in paths
    assert "src/generated/schema.ts" in paths
    assert "docs/locked" in paths
    assert "webpack.config.js" in paths


def test_diff_package_manifest_reports_scripts_and_dependencies() -> None:
    before = json.dumps(
        {
            "scripts": {"test": "vitest run", "build": "vite build"},
            "dependencies": {"react": "18.2.0"},
            "devDependencies": {"vite": "6.0.0"},
        },
        ensure_ascii=False,
    )
    after = json.dumps(
        {
            "scripts": {"test": "vitest run --coverage", "lint": "eslint ."},
            "dependencies": {"react": "18.3.0", "zod": "3.25.0"},
            "devDependencies": {},
        },
        ensure_ascii=False,
    )

    diff = diff_package_manifest(before, after)

    assert diff.parse_error == ""
    assert diff.sections["scripts"].added == {"lint": "eslint ."}
    assert diff.sections["scripts"].removed == {"build": "vite build"}
    assert diff.sections["scripts"].changed["test"] == {
        "before": "vitest run",
        "after": "vitest run --coverage",
    }
    assert diff.sections["dependencies"].added == {"zod": "3.25.0"}
    assert diff.sections["dependencies"].changed["react"] == {"before": "18.2.0", "after": "18.3.0"}
    assert diff.sections["devDependencies"].removed == {"vite": "6.0.0"}


def test_validate_director_write_policy_blocks_scope_and_agents_forbidden_paths() -> None:
    verdict = validate_director_write_policy(
        changed_files=["src/generated/schema.ts", "src/other.ts"],
        allowed_scope=["src/allowed.ts"],
        agents_md="禁止修改 src/generated/schema.ts",
        operation="tool_write",
    )

    assert verdict.allowed is False
    assert any("AGENTS.md forbids writing src/generated/schema.ts" in reason for reason in verdict.reasons)
    assert any("exceed" in reason or "not within" in reason for reason in verdict.reasons)
    assert "src/other.ts" in verdict.extra_files


def test_validate_director_write_policy_requires_package_before_after() -> None:
    verdict = validate_director_write_policy(
        changed_files=["package.json"],
        allowed_scope=["package.json"],
        agents_md="",
        operation="repo_apply_diff",
    )

    assert verdict.allowed is False
    assert any("package.json writes require before/after content" in reason for reason in verdict.reasons)


def test_validate_director_write_policy_requires_nested_package_before_after() -> None:
    verdict = validate_director_write_policy(
        changed_files=["packages/web/package.json"],
        allowed_scope=["packages/web/package.json"],
        agents_md="",
        operation="repo_apply_diff",
    )

    assert verdict.allowed is False
    assert any("package.json writes require before/after content" in reason for reason in verdict.reasons)


def test_validate_director_write_policy_allows_scoped_package_diff_with_evidence() -> None:
    before = json.dumps({"scripts": {"test": "vitest run"}}, ensure_ascii=False)
    after = json.dumps({"scripts": {"test": "vitest run --coverage"}}, ensure_ascii=False)

    verdict = validate_director_write_policy(
        changed_files=["package.json"],
        allowed_scope=["package.json"],
        agents_md="",
        operation="repo_apply_diff",
        package_before=before,
        package_after=after,
    )

    assert verdict.allowed is True
    assert verdict.package_diff is not None
    assert verdict.package_diff.sections["scripts"].changed["test"] == {
        "before": "vitest run",
        "after": "vitest run --coverage",
    }
