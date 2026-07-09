"""Generic AGENTS.md and package-manifest write policy for tool writes."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from polaris.kernelone.llm.toolkit.write_scope import WriteGate

_FORBIDDEN_LINE_RE = re.compile(
    r"(?:禁止|不得|不要|严禁|do\s+not|never|forbidden|disallow|deny).{0,80}",
    re.IGNORECASE,
)
_FORBIDDEN_WRITE_ACTION_RE = re.compile(
    r"(?:修改|改动|写入|编辑|删除|覆盖|创建|新增|write|edit|modify|change|delete|remove|overwrite|touch|create)",
    re.IGNORECASE,
)
_PATH_TOKEN_RE = re.compile(
    r"(?:[A-Za-z]:[\\/])?(?:[\w.@~+-]+[\\/])+[\w.@~+-]+(?:\.[A-Za-z0-9_-]+)?|"
    r"(?:^|[\s:：])(?:package\.json|AGENTS\.md|Cargo\.toml|webpack\.config\.js|jest\.config\.js|tsconfig\.json)(?=$|[\s,，;；。.])",
)
_PACKAGE_SECTIONS = ("scripts", "dependencies", "devDependencies", "peerDependencies", "optionalDependencies")


@dataclass(frozen=True)
class ForbiddenPathRule:
    """A forbidden write target parsed from AGENTS.md."""

    path: str
    source_line: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ForbiddenFilePatternRule:
    """A forbidden file pattern derived from AGENTS.md language/tooling policy."""

    pattern: str
    source_line: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class AgentWritePolicyObject:
    """Structured policy object derived from project guidance."""

    forbidden_paths: tuple[ForbiddenPathRule, ...] = field(default_factory=tuple)
    forbidden_file_patterns: tuple[ForbiddenFilePatternRule, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "forbidden_paths": [rule.to_dict() for rule in self.forbidden_paths],
            "forbidden_file_patterns": [rule.to_dict() for rule in self.forbidden_file_patterns],
        }


@dataclass(frozen=True)
class SectionDiff:
    """Before/after diff for one package.json section."""

    added: dict[str, Any] = field(default_factory=dict)
    removed: dict[str, Any] = field(default_factory=dict)
    changed: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PackageManifestDiff:
    """Structured package.json scripts/dependencies diff."""

    sections: dict[str, SectionDiff] = field(default_factory=dict)
    parse_error: str = ""

    @property
    def has_changes(self) -> bool:
        return any(section.has_changes for section in self.sections.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "sections": {name: diff.to_dict() for name, diff in self.sections.items()},
            "parse_error": self.parse_error,
            "has_changes": self.has_changes,
        }


@dataclass(frozen=True)
class ToolWritePolicyVerdict:
    """Unified write-policy verdict for tool/direct/diff writes."""

    allowed: bool
    operation: str
    changed_files: tuple[str, ...]
    reasons: tuple[str, ...] = field(default_factory=tuple)
    policy: AgentWritePolicyObject = field(default_factory=AgentWritePolicyObject)
    package_diff: PackageManifestDiff | None = None
    write_gate_reason: str = ""
    extra_files: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "operation": self.operation,
            "changed_files": list(self.changed_files),
            "reasons": list(self.reasons),
            "policy": self.policy.to_dict(),
            "package_diff": self.package_diff.to_dict() if self.package_diff else None,
            "write_gate_reason": self.write_gate_reason,
            "extra_files": list(self.extra_files),
        }


def parse_agents_write_policy(agents_md: str | None) -> AgentWritePolicyObject:
    """Parse forbidden file/path rules from AGENTS.md text."""
    rules: list[ForbiddenPathRule] = []
    pattern_rules: list[ForbiddenFilePatternRule] = []
    seen: set[str] = set()
    seen_patterns: set[str] = set()
    for raw_line in str(agents_md or "").splitlines():
        line = raw_line.strip()
        if not line or not _FORBIDDEN_LINE_RE.search(line):
            continue
        if _FORBIDDEN_WRITE_ACTION_RE.search(line):
            for match in _PATH_TOKEN_RE.finditer(line):
                path_token = match.group(0).strip(" \t:：,，;；。")
                normalized = _normalize_policy_path(path_token)
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                rules.append(ForbiddenPathRule(path=normalized, source_line=line))
        for pattern in _derived_forbidden_file_patterns(line):
            if pattern in seen_patterns:
                continue
            seen_patterns.add(pattern)
            pattern_rules.append(ForbiddenFilePatternRule(pattern=pattern, source_line=line))
    return AgentWritePolicyObject(
        forbidden_paths=tuple(rules),
        forbidden_file_patterns=tuple(pattern_rules),
    )


def diff_package_manifest(before_text: str | None, after_text: str | None) -> PackageManifestDiff:
    """Compare package.json scripts/dependencies before and after a write."""
    try:
        before = _parse_json_object(before_text or "{}")
        after = _parse_json_object(after_text or "{}")
    except ValueError as exc:
        return PackageManifestDiff(parse_error=str(exc))

    sections: dict[str, SectionDiff] = {}
    for section_name in _PACKAGE_SECTIONS:
        before_section = _dict_section(before.get(section_name))
        after_section = _dict_section(after.get(section_name))
        added = {key: after_section[key] for key in sorted(set(after_section) - set(before_section))}
        removed = {key: before_section[key] for key in sorted(set(before_section) - set(after_section))}
        changed = {
            key: {"before": before_section[key], "after": after_section[key]}
            for key in sorted(set(before_section) & set(after_section))
            if before_section[key] != after_section[key]
        }
        sections[section_name] = SectionDiff(added=added, removed=removed, changed=changed)
    return PackageManifestDiff(sections=sections)


def validate_tool_write_policy(
    *,
    changed_files: list[str],
    allowed_scope: list[str],
    agents_md: str | None = None,
    operation: str = "tool_write",
    package_before: str | None = None,
    package_after: str | None = None,
    require_change: bool = True,
) -> ToolWritePolicyVerdict:
    """Validate tool writes through a deterministic policy gate."""
    normalized_changed = tuple(_normalize_policy_path(path) for path in changed_files if _normalize_policy_path(path))
    policy = parse_agents_write_policy(agents_md)
    reasons: list[str] = []

    write_gate = WriteGate.validate(
        changed_files=list(normalized_changed),
        act_files=allowed_scope,
        pm_target_files=allowed_scope,
        require_change=require_change,
    )
    if not write_gate.allowed:
        reasons.append(write_gate.reason)

    for changed in normalized_changed:
        for path_rule in policy.forbidden_paths:
            if _matches_forbidden_path(changed, path_rule.path):
                reasons.append(f"AGENTS.md forbids writing {changed} (rule: {path_rule.path})")
        for pattern_rule in policy.forbidden_file_patterns:
            if _matches_forbidden_file_pattern(changed, pattern_rule.pattern):
                reasons.append(f"AGENTS.md forbids writing {changed} (rule: {pattern_rule.pattern})")

    package_diff: PackageManifestDiff | None = None
    if any(_is_package_manifest_path(path) for path in normalized_changed):
        if package_before is None or package_after is None:
            reasons.append("package.json writes require before/after content for structured scripts/dependencies diff")
        else:
            package_diff = diff_package_manifest(package_before, package_after)
            if package_diff.parse_error:
                reasons.append(f"package.json structured diff failed: {package_diff.parse_error}")
            else:
                scripts_diff = package_diff.sections.get("scripts")
                if (
                    scripts_diff is not None
                    and scripts_diff.removed
                    and not scripts_diff.added
                    and not scripts_diff.changed
                ):
                    reasons.append("package.json writes may not remove all existing scripts")

    return ToolWritePolicyVerdict(
        allowed=not reasons,
        operation=operation,
        changed_files=normalized_changed,
        reasons=tuple(reasons),
        policy=policy,
        package_diff=package_diff,
        write_gate_reason=write_gate.reason,
        extra_files=tuple(write_gate.extra_files or ()),
    )


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("package manifest is not a JSON object")
    return payload


def _dict_section(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_policy_path(value: str) -> str:
    token = str(value or "").strip().replace("\\", "/")
    token = re.sub(r"^[A-Za-z]:/", "", token)
    while token.startswith("./"):
        token = token[2:]
    token = token.strip("/")
    token = os.path.normpath(token).replace("\\", "/")
    return "" if token in {".", ""} else token


def _matches_forbidden_path(candidate: str, forbidden: str) -> bool:
    changed = _normalize_policy_path(candidate).lower()
    rule = _normalize_policy_path(forbidden).lower().rstrip("/")
    if not changed or not rule:
        return False
    return changed == rule or changed.startswith(rule + "/")


def _matches_forbidden_file_pattern(candidate: str, pattern: str) -> bool:
    changed = _normalize_policy_path(candidate).lower()
    rule = str(pattern or "").strip().lower()
    if not changed or not rule:
        return False
    if rule.startswith("*."):
        return changed.endswith(rule[1:])
    return changed == rule or changed.endswith(f"/{rule}")


def _derived_forbidden_file_patterns(line: str) -> tuple[str, ...]:
    lowered = str(line or "").lower()
    patterns: list[str] = []
    if "rust" in lowered or "cargo" in lowered:
        patterns.extend(["Cargo.toml", "*.rs"])
    if re.search(r"\bgo\b|golang|go modules?", lowered):
        patterns.extend(["go.mod", "go.sum", "*.go"])
    if "python" in lowered:
        patterns.extend(["*.py", "requirements.txt", "pyproject.toml", "setup.py"])
    if "webpack" in lowered:
        patterns.append("webpack.config.js")
    if "jest" in lowered:
        patterns.append("jest.config.js")
    if "vite" in lowered:
        patterns.append("vite.config.ts")
    if "vitest" in lowered:
        patterns.append("vitest.config.ts")
    return tuple(dict.fromkeys(patterns))


def _is_package_manifest_path(path: str) -> bool:
    normalized = _normalize_policy_path(path).lower()
    return normalized == "package.json" or normalized.endswith("/package.json")


__all__ = [
    "AgentWritePolicyObject",
    "ForbiddenFilePatternRule",
    "ForbiddenPathRule",
    "PackageManifestDiff",
    "SectionDiff",
    "ToolWritePolicyVerdict",
    "diff_package_manifest",
    "parse_agents_write_policy",
    "validate_tool_write_policy",
]
