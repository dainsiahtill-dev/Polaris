"""Structured Director write policy gate.

This module keeps deterministic write-policy checks outside LLM/tool handlers.
It converts AGENTS.md constraints and package.json before/after content into
structured evidence that tool writes and diff writes can share.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from polaris.domain.verification.write_gate import WriteGate

_FORBIDDEN_LINE_RE = re.compile(
    r"(?:禁止|不得|不要|严禁|do\s+not|never|forbidden|disallow|deny).{0,80}",
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
class DirectorPolicyObject:
    """Structured policy object derived from project guidance."""

    forbidden_paths: tuple[ForbiddenPathRule, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {"forbidden_paths": [rule.to_dict() for rule in self.forbidden_paths]}


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
class DirectorWritePolicyVerdict:
    """Unified write-policy verdict for Director tool/direct/diff writes."""

    allowed: bool
    operation: str
    changed_files: tuple[str, ...]
    reasons: tuple[str, ...] = field(default_factory=tuple)
    policy: DirectorPolicyObject = field(default_factory=DirectorPolicyObject)
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


def parse_agents_write_policy(agents_md: str | None) -> DirectorPolicyObject:
    """Parse forbidden file/path rules from AGENTS.md text."""
    rules: list[ForbiddenPathRule] = []
    seen: set[str] = set()
    for raw_line in str(agents_md or "").splitlines():
        line = raw_line.strip()
        if not line or not _FORBIDDEN_LINE_RE.search(line):
            continue
        for match in _PATH_TOKEN_RE.finditer(line):
            path_token = match.group(0).strip(" \t:：,，;；。")
            normalized = _normalize_policy_path(path_token)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            rules.append(ForbiddenPathRule(path=normalized, source_line=line))
    return DirectorPolicyObject(forbidden_paths=tuple(rules))


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


def validate_director_write_policy(
    *,
    changed_files: list[str],
    allowed_scope: list[str],
    agents_md: str | None = None,
    operation: str = "tool_write",
    package_before: str | None = None,
    package_after: str | None = None,
    require_change: bool = True,
) -> DirectorWritePolicyVerdict:
    """Validate Director writes through one deterministic policy gate."""
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
        for rule in policy.forbidden_paths:
            if _matches_forbidden_path(changed, rule.path):
                reasons.append(f"AGENTS.md forbids writing {changed} (rule: {rule.path})")

    package_diff: PackageManifestDiff | None = None
    if any(_is_package_manifest_path(path) for path in normalized_changed):
        if package_before is None or package_after is None:
            reasons.append("package.json writes require before/after content for structured scripts/dependencies diff")
        else:
            package_diff = diff_package_manifest(package_before, package_after)
            if package_diff.parse_error:
                reasons.append(f"package.json structured diff failed: {package_diff.parse_error}")

    return DirectorWritePolicyVerdict(
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


def _is_package_manifest_path(path: str) -> bool:
    normalized = _normalize_policy_path(path).lower()
    return normalized == "package.json" or normalized.endswith("/package.json")
