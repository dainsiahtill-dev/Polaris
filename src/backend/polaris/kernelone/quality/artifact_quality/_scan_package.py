"""package.json / Cargo.toml / npm-script artifact quality scans."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any, Mapping

from polaris.kernelone.quality.artifact_quality._constants import (
    _ARTIFACT_QUALITY_ERROR_PREFIX,
    _COMMONJS_RUNTIME_TOKEN_RE,
    _NPM_MANIFEST_ONLY_TEST_SCRIPT_RE,
    _NPM_NODE_INLINE_CODE_FLAGS,
    _NPM_NODE_OPTION_VALUE_FLAGS,
    _NPM_PLACEHOLDER_TEST_SCRIPT_RE,
    _NPM_SCRIPT_ENTRYPOINT_SUBCOMMANDS,
    _NPM_SCRIPT_FAILURE_SWALLOW_RE,
    _NPM_SCRIPT_SEPARATORS,
    _NPM_SCRIPT_SHELL_SUBSTITUTION_RE,
    _NPM_SCRIPT_TSC_RE,
    _NPM_TEST_RUNNER_SCRIPT_RE,
    _PYTHON_COMMAND_IN_NPM_SCRIPT_RE,
    _PYTHON_PACKAGE_MANIFEST_DEPENDENCIES,
    _TS_JS_SOURCE_EXTS,
    _TS_SOURCE_EXTS,
)
from polaris.kernelone.quality.artifact_quality._helpers import (
    _is_test_like_artifact_path,
    _iter_workspace_relative_files,
)
from polaris.kernelone.quality.artifact_quality._issues import (
    _artifact_quality_issue_metadata,
)
from polaris.kernelone.quality.artifact_quality._models import (
    ArtifactQualityDeferredCheck,
    ArtifactQualityIssue,
    _FileArtifactQualityEvidence,
    _NodeEvalSyntaxIssue,
)
from polaris.kernelone.quality.artifact_quality._syntax import (
    _compress_node_syntax_error,
)
from polaris.kernelone.quality.package_scripts import (
    PackageScriptIssue,
    check_package_scripts,
)


def _package_manifest_quality_issue(
    error: str,
    relative_path: str,
    metadata_override: Mapping[str, Any] | None = None,
) -> ArtifactQualityIssue:
    message = str(error or "").strip()
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    metadata = _artifact_quality_issue_metadata(str(error or "").strip(), message, "npm_manifest_invalid")
    if isinstance(metadata_override, Mapping):
        metadata.update({str(key): value for key, value in metadata_override.items() if value})
    metadata["manifest_path"] = relative_path
    return ArtifactQualityIssue(
        code="npm_manifest_invalid",
        message=message,
        path=relative_path,
        source="package_manifest_scanner",
        metadata=metadata,
    )


def _append_package_manifest_issue(
    errors: list[str],
    issues: list[ArtifactQualityIssue],
    error: str,
    relative_path: str,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    normalized_error = str(error or "").strip()
    if not normalized_error:
        return
    errors.append(normalized_error)
    issues.append(_package_manifest_quality_issue(normalized_error, relative_path, metadata))


def _package_script_gate_artifact_error(issue: PackageScriptIssue, relative_path: str) -> str:
    if issue.code == "npm_placeholder_script" and issue.script_name and issue.command:
        return (
            "Artifact quality scan failed: npm package manifest script "
            f"{issue.script_name!r} is a placeholder command: {issue.command} in {relative_path}"
        )
    if issue.code == "npm_script_empty" and issue.script_name:
        return f"Artifact quality scan failed: npm package manifest script {issue.script_name!r} is empty in {relative_path}"
    if issue.code == "npm_script_missing_local_entrypoint" and issue.script_name and issue.entrypoint:
        return (
            "Artifact quality scan failed: npm package manifest script "
            f"{issue.script_name!r} references missing local entrypoint {issue.entrypoint!r} in {relative_path}"
        )
    return f"Artifact quality scan failed: {issue.message} in {relative_path}"


def _package_script_gate_artifact_issue(
    issue: PackageScriptIssue,
    relative_path: str,
    display_error: str,
) -> ArtifactQualityIssue:
    metadata = {
        "raw": display_error,
        "manifest_path": relative_path,
        **dict(issue.metadata or {}),
        "script_issue_source": "package_scripts",
        "package_script_issue_code": issue.code,
    }
    for key, value in issue.to_dict().items():
        if key in {"code", "message", "path", "severity", "source", "metadata"} or not value:
            continue
        metadata[str(key)] = value
    message = display_error
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    is_missing_entrypoint = issue.code == "npm_script_missing_local_entrypoint"
    return ArtifactQualityIssue(
        code=issue.code if is_missing_entrypoint else "npm_manifest_invalid",
        message=message,
        path=relative_path,
        source=issue.source if is_missing_entrypoint else "package_manifest_scanner",
        metadata=metadata,
    )


def _append_package_script_gate_issue(
    errors: list[str],
    issues: list[ArtifactQualityIssue],
    issue: PackageScriptIssue,
    relative_path: str,
) -> None:
    display_error = _package_script_gate_artifact_error(issue, relative_path)
    errors.append(display_error)
    issues.append(_package_script_gate_artifact_issue(issue, relative_path, display_error))


def _package_script_gate_issues_for_code(
    root_full: Path,
    *codes: str,
) -> tuple[PackageScriptIssue, ...]:
    allowed_codes = {str(code or "").strip() for code in codes if str(code or "").strip()}
    if not allowed_codes:
        return ()
    result = check_package_scripts(str(root_full))
    return tuple(issue for issue in result.issues if issue.code in allowed_codes)


def _first_package_script_gate_issue_for_script(
    issues: Iterable[PackageScriptIssue],
    *,
    script_name: str,
    codes: set[str],
) -> PackageScriptIssue | None:
    normalized_script = str(script_name or "").strip()
    for issue in issues:
        if issue.code not in codes:
            continue
        if str(issue.script_name or "").strip() == normalized_script:
            return issue
    return None


def _package_manifest_evidence_from_errors(
    errors: list[str],
    relative_path: str,
    direct_issues: list[ArtifactQualityIssue] | None = None,
    *,
    deferred_checks: Iterable[ArtifactQualityDeferredCheck] = (),
    incomplete_reasons: Iterable[str] = (),
) -> _FileArtifactQualityEvidence:
    issues = list(direct_issues or [])
    direct_issue_messages = {str((issue.metadata or {}).get("raw") or issue.message).strip() for issue in issues}
    issues.extend(
        _package_manifest_quality_issue(error, relative_path)
        for error in errors
        if str(error or "").strip() not in direct_issue_messages
    )
    return _FileArtifactQualityEvidence(
        errors=tuple(errors),
        issues=tuple(issues),
        deferred_checks=tuple(deferred_checks),
        incomplete_reasons=tuple(incomplete_reasons),
    )


_CARGO_BIN_SECTION_RE = re.compile(
    r"\[\[bin\]\](?P<body>.*?)(?=\n\[\[|\n\[(?:package|lib|dependencies|dev-dependencies|build-dependencies|features|profile|workspace|target|bench|test|example|package\.metadata)|\Z)",
    re.DOTALL | re.IGNORECASE,
)

_CARGO_BIN_NAME_RE = re.compile(r'(?m)^\s*name\s*=\s*["\']([^"\']+)["\']')

_CARGO_BIN_PATH_RE = re.compile(r'(?m)^\s*path\s*=\s*["\']([^"\']+)["\']')

_CARGO_PACKAGE_NAME_RE = re.compile(r'(?m)^\s*name\s*=\s*["\']([^"\']+)["\']')

_CARGO_DEFAULT_RUN_RE = re.compile(r'(?m)^\s*default-run\s*=\s*["\']([^"\']+)["\']')

_CARGO_RUNNABLE_NAME_RE = re.compile(
    r"(?i)(?:^|[_-])(cli|bin|app|tool|cmd|server|service|runner|demo|game|palette|client|daemon|bot)(?:$|[_-])"
)

_CARGO_RUNNABLE_TEXT_RE = re.compile(
    r"(?i)\b(cli|command[\s_-]?line|binary|entrypoint|entry[\s_-]?point|tool|server|service|"
    r"runner|daemon|application|console)\b|命令行|二进制|入口|工具"
)


def _cargo_package_name(cargo_text: str) -> str:
    package_name = "app"
    package_match = re.search(r"(?is)\[package\](.*?)(?:\n\[|\Z)", cargo_text)
    if package_match:
        name_match = _CARGO_PACKAGE_NAME_RE.search(package_match.group(1))
        if name_match:
            package_name = str(name_match.group(1) or "").strip() or package_name
    return package_name


def _normalize_cargo_relative_path(raw_path: str) -> str:
    bin_path = str(raw_path or "").strip().replace("\\", "/")
    while bin_path.startswith("./"):
        bin_path = bin_path[2:]
    if not bin_path or bin_path.startswith("/") or ".." in bin_path.split("/"):
        return ""
    return bin_path


def _cargo_declared_bin_targets(cargo_text: str, package_name: str) -> tuple[tuple[str, str], ...]:
    """Return ``(bin_name, bin_path)`` pairs declared via ``[[bin]]`` sections."""

    targets: list[tuple[str, str]] = []
    for section in _CARGO_BIN_SECTION_RE.finditer(cargo_text):
        body = str(section.group("body") or "")
        name_match = _CARGO_BIN_NAME_RE.search(body)
        path_match = _CARGO_BIN_PATH_RE.search(body)
        bin_name = str(name_match.group(1) if name_match else package_name).strip() or package_name
        bin_path = _normalize_cargo_relative_path(path_match.group(1) if path_match else "src/main.rs")
        if not bin_path:
            continue
        targets.append((bin_name, bin_path))
    return tuple(targets)


def _cargo_workspace_has_usable_binary(root_full: Path, cargo_text: str, package_name: str) -> bool:
    """True when at least one Cargo binary entrypoint exists on disk."""

    root_resolved = root_full.resolve()
    for _bin_name, bin_path in _cargo_declared_bin_targets(cargo_text, package_name):
        absolute = (root_full / bin_path).resolve()
        try:
            absolute.relative_to(root_resolved)
        except ValueError:
            continue
        if absolute.is_file():
            return True
    # Implicit auto-bin targets Cargo recognizes without [[bin]].
    for candidate in ("src/main.rs",):
        absolute = (root_full / candidate).resolve()
        try:
            absolute.relative_to(root_resolved)
        except ValueError:
            continue
        if absolute.is_file():
            return True
    bin_dir = root_full / "src" / "bin"
    if bin_dir.is_dir():
        with suppress(OSError):
            for rust_file in bin_dir.glob("*.rs"):
                if rust_file.is_file():
                    return True
    return False


def _cargo_runnable_entrypoint_expected(cargo_text: str, package_name: str) -> bool:
    """Whether a binary entrypoint is expected (CLI/app-shaped), not a pure lib.

    Pure library crates (lib-only, no ``[[bin]]``/default-run, no runnable name/text
    signal) stay non-diagnosed so ecosystem libs are not forced to grow a ``main``.
    """

    lowered = cargo_text.lower()
    if "[[bin]]" in lowered:
        return True
    if _CARGO_DEFAULT_RUN_RE.search(cargo_text):
        return True
    if _CARGO_RUNNABLE_NAME_RE.search(package_name):
        return True
    package_match = re.search(r"(?is)\[package\](.*?)(?:\n\[|\Z)", cargo_text)
    package_body = package_match.group(1) if package_match else ""
    if _CARGO_RUNNABLE_TEXT_RE.search(package_body):
        return True
    # Binary-only package (no [lib]): missing main is always a gap.
    return re.search(r"(?im)^\s*\[lib\]\s*$", cargo_text) is None


def _missing_binary_issue(
    *,
    root_full: Path,
    relative_path: str,
    bin_name: str,
    bin_path: str,
    reason: str,
) -> tuple[str, ArtifactQualityIssue]:
    absolute = (root_full / bin_path).resolve()
    message = f"error: can't find bin `{bin_name}` at path `{absolute.as_posix()}`"
    return message, ArtifactQualityIssue(
        code="rust_missing_binary_entrypoint",
        message=message,
        path=bin_path,
        source="cargo_manifest_scanner",
        metadata={
            "raw": message,
            "diagnostic_kind": "rust_missing_binary_entrypoint",
            "bin_name": bin_name,
            "bin_path": bin_path,
            "manifest_path": relative_path,
            "missing_bin_reason": reason,
        },
    )


def _scan_cargo_manifest_missing_binary_evidence(
    root_full: Path,
    text: str,
    relative_path: str,
) -> _FileArtifactQualityEvidence:
    """Detect missing Cargo binary entrypoints for materialization/repair planning.

    Covers:
    1. Declared ``[[bin]]`` paths that are absent on disk (R66/R71).
    2. Runnable / CLI-shaped packages with **no usable binary target at all**
       (lib-only manifests that still need ``cargo run`` / entrypoint smoke).

    Pure library crates without runnability signals stay silent. Emits
    cargo-shaped diagnostics so Director materialization quality can plan
    ``deterministic_rust_missing_binary_entrypoint_repair`` without waiting for
    a later bench ``cargo check`` measurement.
    """

    cargo_text = str(text or "")
    if not cargo_text.strip() or not re.search(r"(?im)^\s*\[package\]\s*$", cargo_text):
        return _FileArtifactQualityEvidence()

    package_name = _cargo_package_name(cargo_text)
    errors: list[str] = []
    issues: list[ArtifactQualityIssue] = []

    declared = _cargo_declared_bin_targets(cargo_text, package_name)
    root_resolved = root_full.resolve()
    for bin_name, bin_path in declared:
        absolute = (root_full / bin_path).resolve()
        try:
            absolute.relative_to(root_resolved)
        except ValueError:
            continue
        if absolute.is_file():
            continue
        message, issue = _missing_binary_issue(
            root_full=root_full,
            relative_path=relative_path,
            bin_name=bin_name,
            bin_path=bin_path,
            reason="declared_bin_path_missing",
        )
        errors.append(message)
        issues.append(issue)

    if (
        not errors
        and not _cargo_workspace_has_usable_binary(root_full, cargo_text, package_name)
        and _cargo_runnable_entrypoint_expected(cargo_text, package_name)
    ):
        default_run = _CARGO_DEFAULT_RUN_RE.search(cargo_text)
        bin_name = str(default_run.group(1) if default_run else package_name).strip() or package_name
        message, issue = _missing_binary_issue(
            root_full=root_full,
            relative_path=relative_path,
            bin_name=bin_name,
            bin_path="src/main.rs",
            reason="no_usable_binary_target",
        )
        errors.append(message)
        issues.append(issue)

    return _FileArtifactQualityEvidence(errors=tuple(errors), issues=tuple(issues))


def _scan_package_manifest_evidence(
    root_full: Path, text: str, relative_path: str, *, pure_static: bool = False
) -> _FileArtifactQualityEvidence:
    """Return package-manifest findings as legacy strings and direct typed issues."""

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _FileArtifactQualityEvidence()
    if not isinstance(payload, dict):
        return _FileArtifactQualityEvidence()
    errors: list[str] = []
    issues: list[ArtifactQualityIssue] = []
    deferred_checks: list[ArtifactQualityDeferredCheck] = []
    incomplete_reasons: list[str] = []
    scripts = payload.get("scripts")
    if isinstance(scripts, dict):
        if pure_static:
            # Discover intents before any diagnostic short-circuit. Shell parsing
            # stays static; malformed/ambiguous snippets cannot be inferred safely.
            for script_name, script_value in scripts.items():
                try:
                    sources, invocation_unknown = _static_shell_node_sources(
                        str(script_value or ""), declared_scripts=frozenset(str(name) for name in scripts)
                    )
                except ValueError:
                    incomplete_reasons.append(f"script_invocation_unknown:{relative_path}:{script_name}:shell_syntax")
                    continue
                incomplete_reasons.extend(
                    f"script_invocation_unknown:{relative_path}:{script_name}:{reason}" for reason in invocation_unknown
                )
                if _NPM_SCRIPT_SHELL_SUBSTITUTION_RE.search(str(script_value or "")):
                    incomplete_reasons.append(
                        f"script_invocation_unknown:{relative_path}:{script_name}:shell_substitution"
                    )
                if sources:
                    deferred_checks.append(
                        ArtifactQualityDeferredCheck("node_eval_parser", (relative_path,), script_name=str(script_name))
                    )
        package_script_gate_issues = _package_script_gate_issues_for_code(
            root_full,
            "npm_script_cycle",
            "npm_placeholder_script",
            "npm_script_empty",
            "npm_script_missing_local_entrypoint",
        )
        for issue in package_script_gate_issues:
            if issue.code != "npm_script_cycle":
                continue
            _append_package_script_gate_issue(
                errors,
                issues,
                issue,
                relative_path,
            )
        test_script = str(scripts.get("test") or "")
        lowered = test_script.lower()
        if "no test specified" in lowered or "no tests specified" in lowered:
            _append_package_manifest_issue(
                errors,
                issues,
                f"Artifact quality scan failed: npm default failing test script in {relative_path}",
                relative_path,
                {
                    "script_name": "test",
                    "script_issue": "default_failing_test_script",
                    "script_issue_source": "package_manifest_scanner",
                },
            )
        if _NPM_PLACEHOLDER_TEST_SCRIPT_RE.search(test_script):
            _append_package_manifest_issue(
                errors,
                issues,
                f"Artifact quality scan failed: npm placeholder test script in {relative_path}",
                relative_path,
                {
                    "script_name": "test",
                    "script_issue": "placeholder_test_script",
                    "script_issue_source": "package_manifest_scanner",
                },
            )
        if _NPM_MANIFEST_ONLY_TEST_SCRIPT_RE.search(test_script):
            _append_package_manifest_issue(
                errors,
                issues,
                f"Artifact quality scan failed: npm manifest-only test script in {relative_path}",
                relative_path,
                {
                    "script_name": "test",
                    "script_issue": "manifest_only_test_script",
                    "script_issue_source": "package_manifest_scanner",
                },
            )
        if (
            _NPM_TEST_RUNNER_SCRIPT_RE.search(test_script)
            and _workspace_has_node_source_files(root_full)
            and not _workspace_has_node_test_files(root_full)
        ):
            _append_package_manifest_issue(
                errors,
                issues,
                "Artifact quality scan failed: npm package manifest has test runner script "
                f"but no test/spec files exist in {relative_path}",
                relative_path,
                {
                    "script_name": "test",
                    "script_issue": "missing_node_test_files",
                    "script_issue_source": "package_manifest_scanner",
                },
            )
        for script_name, script_value in scripts.items():
            script_text = str(script_value or "")
            try:
                tokens = shlex.split(script_text, posix=(os.name != "nt"))
            except ValueError as exc:
                _append_package_manifest_issue(
                    errors,
                    issues,
                    "Artifact quality scan failed: npm package manifest script "
                    f"{str(script_name)!r} has invalid shell syntax in {relative_path}: {exc}",
                    relative_path,
                    {
                        "script_name": str(script_name),
                        "script_issue": "invalid_shell_syntax",
                        "script_issue_source": "package_manifest_scanner",
                    },
                )
                continue
            placeholder_issue = _first_package_script_gate_issue_for_script(
                package_script_gate_issues,
                script_name=str(script_name),
                codes={"npm_placeholder_script", "npm_script_empty"},
            )
            if placeholder_issue is not None:
                _append_package_script_gate_issue(
                    errors,
                    issues,
                    placeholder_issue,
                    relative_path,
                )
                continue
            missing_entrypoint_issue = _first_package_script_gate_issue_for_script(
                package_script_gate_issues,
                script_name=str(script_name),
                codes={"npm_script_missing_local_entrypoint"},
            )
            if missing_entrypoint_issue is not None:
                _append_package_script_gate_issue(
                    errors,
                    issues,
                    missing_entrypoint_issue,
                    relative_path,
                )
            if _NPM_SCRIPT_FAILURE_SWALLOW_RE.search(script_text):
                _append_package_manifest_issue(
                    errors,
                    issues,
                    "Artifact quality scan failed: npm package manifest script "
                    f"{str(script_name)!r} swallows command failures in {relative_path}",
                    relative_path,
                    {
                        "script_name": str(script_name),
                        "script_issue": "swallows_command_failures",
                        "script_issue_source": "package_manifest_scanner",
                    },
                )
                continue
            if _NPM_SCRIPT_SHELL_SUBSTITUTION_RE.search(script_text):
                _append_package_manifest_issue(
                    errors,
                    issues,
                    "Artifact quality scan failed: npm package manifest script "
                    f"{str(script_name)!r} uses shell command substitution in {relative_path}",
                    relative_path,
                    {
                        "script_name": str(script_name),
                        "script_issue": "shell_command_substitution",
                        "script_issue_source": "package_manifest_scanner",
                    },
                )
                continue
            if _PYTHON_COMMAND_IN_NPM_SCRIPT_RE.search(script_text):
                _append_package_manifest_issue(
                    errors,
                    issues,
                    "Artifact quality scan failed: npm package manifest contains "
                    f"Python command in script {str(script_name)!r} in {relative_path}",
                    relative_path,
                    {
                        "script_name": str(script_name),
                        "script_issue": "python_command",
                        "script_issue_source": "package_manifest_scanner",
                    },
                )
                break
            node_eval_issue = (
                None if pure_static else _scan_npm_script_node_eval_syntax(tokens, str(script_name), relative_path)
            )
            if node_eval_issue is not None:
                _append_package_manifest_issue(
                    errors,
                    issues,
                    node_eval_issue.display_error,
                    relative_path,
                    {
                        "script_name": node_eval_issue.script_name,
                        "script_issue": "invalid_node_eval_syntax",
                        "script_issue_source": "package_manifest_scanner",
                        "diagnostic_detail": node_eval_issue.diagnostic_detail,
                        "diagnostic_kind": "node_eval_syntax",
                    },
                )
                continue
            test_directory_evidence = _scan_npm_script_node_test_directory_target_evidence(
                root_full,
                tokens,
                str(script_name),
                relative_path,
            )
            errors.extend(test_directory_evidence.errors)
            issues.extend(test_directory_evidence.issues)
            config_evidence = _scan_npm_script_missing_local_config_evidence(
                root_full,
                tokens,
                str(script_name),
                relative_path,
            )
            errors.extend(config_evidence.errors)
            issues.extend(config_evidence.issues)
        if _package_manifest_requires_typescript(root_full, payload) and not _package_declares_dependency(
            payload, "typescript"
        ):
            _append_package_manifest_issue(
                errors,
                issues,
                "Artifact quality scan failed: TypeScript project requires 'typescript' "
                f"devDependency in {relative_path}",
                relative_path,
                {
                    "manifest_issue": "typescript_dependency_missing",
                    "manifest_issue_source": "package_manifest_scanner",
                    "package_name": "typescript",
                    "dependency_section": "devDependencies",
                },
            )
    main_entry = str(payload.get("main") or "").strip().replace("\\", "/").lower()
    if main_entry.endswith(".py"):
        _append_package_manifest_issue(
            errors,
            issues,
            f"Artifact quality scan failed: npm package manifest contains Python runtime entrypoint in {relative_path}",
            relative_path,
            {
                "manifest_issue": "python_runtime_entrypoint",
                "manifest_issue_source": "package_manifest_scanner",
                "entrypoint": main_entry,
            },
        )
    module_type_evidence = _scan_package_module_type_mismatch_evidence(root_full, payload, relative_path)
    errors.extend(module_type_evidence.errors)
    issues.extend(module_type_evidence.issues)
    for section_name in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        section = payload.get(section_name)
        if not isinstance(section, dict):
            continue
        for package_name in section:
            normalized = str(package_name or "").strip().lower()
            if normalized in _PYTHON_PACKAGE_MANIFEST_DEPENDENCIES:
                _append_package_manifest_issue(
                    errors,
                    issues,
                    "Artifact quality scan failed: npm package manifest declares "
                    f"Python package dependency {package_name!r} in {relative_path}",
                    relative_path,
                    {
                        "manifest_issue": "python_package_dependency",
                        "manifest_issue_source": "package_manifest_scanner",
                        "package_name": str(package_name),
                        "dependency_section": section_name,
                    },
                )
                if not pure_static:
                    return sys.modules[__package__]._package_manifest_evidence_from_errors(
                        errors, relative_path, issues
                    )
                break
    if pure_static:
        return _package_manifest_evidence_from_errors(
            errors, relative_path, issues, deferred_checks=deferred_checks, incomplete_reasons=incomplete_reasons
        )
    return sys.modules[__package__]._package_manifest_evidence_from_errors(errors, relative_path, issues)


def _scan_npm_script_node_eval_syntax(
    tokens: list[str],
    script_name: str,
    relative_path: str,
) -> _NodeEvalSyntaxIssue | None:
    for source in _iter_node_eval_sources(tokens):
        detail = _check_javascript_snippet_syntax(source)
        if detail:
            diagnostic_detail = detail[:200]
            return _NodeEvalSyntaxIssue(
                display_error=(
                    "Artifact quality scan failed: npm package manifest script "
                    f"{script_name!r} has invalid node eval syntax in {relative_path}: {diagnostic_detail}"
                ),
                diagnostic_detail=diagnostic_detail,
                script_name=script_name,
                relative_path=relative_path,
            )
    return None


def _iter_node_eval_sources(tokens: list[str], *, incomplete_reasons: list[str] | None = None) -> Iterable[str]:
    """Yield JavaScript source snippets passed to ``node --eval`` / ``-e``.

    Scans one ``node`` invocation at a time, skipping safe Node options such as
    ``--no-warnings`` or ``--enable-source-maps`` that may appear between
    ``node`` and the eval flag. It fails closed on shell operators and on a
    positional script path / command, so snippets are never inferred from a
    later clause or from code meant to run from a file.
    """

    length = len(tokens)
    index = 0
    while index < length:
        if os.path.basename(str(tokens[index] or "").strip().lower()) not in {"node", "node.exe"}:
            index += 1
            continue
        index += 1
        if incomplete_reasons is not None and _static_node_test_invocation(tokens[index:], incomplete_reasons):
            # Test source remains covered by file parsers and the controlled
            # verifier. Never reinterpret test paths as inline eval snippets.
            continue
        while index < length:
            token = str(tokens[index] or "").strip()
            if token in _NPM_SCRIPT_SEPARATORS:
                break
            lowered = token.lower()
            eval_flags = {"-e", "--eval"} if incomplete_reasons is None else {"-e", "--eval", "-p", "--print", "-pe"}
            if token == "--":
                if incomplete_reasons is not None and index + 1 < length:
                    script = str(tokens[index + 1] or "").strip()
                    if Path(script).suffix.lower() in {".md", ".markdown"}:
                        incomplete_reasons.append(f"node_executable_document:{script}")
                break
            if token == "-" and incomplete_reasons is not None:
                yield ""
                break
            if lowered in eval_flags:
                index += 1
                if index < length:
                    source = str(tokens[index] or "")
                    yield source
                    index += 1
                elif incomplete_reasons is not None:
                    incomplete_reasons.append(f"missing_eval_argument:{token}")
                continue
            equal_flags = ("-e=", "--eval=") if incomplete_reasons is None else ("-e=", "--eval=", "--print=")
            if lowered.startswith(equal_flags):
                source = token.split("=", 1)[1]
                yield source
                if incomplete_reasons is not None and (
                    lowered.startswith("-e=") or (not source and lowered.startswith("--eval="))
                ):
                    incomplete_reasons.append(f"invalid_eval_argument:{token}")
                index += 1
                continue
            if token in _NPM_NODE_OPTION_VALUE_FLAGS:
                if incomplete_reasons is not None and index + 1 >= length:
                    incomplete_reasons.append(f"missing_node_option_argument:{token}")
                if incomplete_reasons is not None and token in {"--require", "-r"} and index + 1 < length:
                    loader = str(tokens[index + 1] or "").strip()
                    if Path(loader).suffix.lower() in {".md", ".markdown"}:
                        incomplete_reasons.append(f"node_executable_document:{loader}")
                index += 2
                continue
            if lowered == "--import":
                if incomplete_reasons is not None and index + 1 >= length:
                    incomplete_reasons.append(f"missing_node_option_argument:{token}")
                if incomplete_reasons is not None and index + 1 < length:
                    loader = str(tokens[index + 1] or "").strip()
                    if Path(loader).suffix.lower() in {".md", ".markdown"}:
                        incomplete_reasons.append(f"node_executable_document:{loader}")
                index += 2
                continue
            if token.startswith((*(flag + "=" for flag in _NPM_NODE_OPTION_VALUE_FLAGS), "--import=")):
                if incomplete_reasons is not None and token.startswith(("--require=", "--import=", "-r=")):
                    loader = token.split("=", 1)[1]
                    if Path(loader).suffix.lower() in {".md", ".markdown"}:
                        incomplete_reasons.append(f"node_executable_document:{loader}")
                index += 1
                continue
            if lowered.startswith("-"):
                # Safe boolean option such as --no-warnings or --enable-source-maps.
                if incomplete_reasons is not None and lowered not in {
                    "--no-warnings",
                    "--enable-source-maps",
                    "--trace-warnings",
                    "--trace-deprecation",
                    "--no-deprecation",
                    "--throw-deprecation",
                    "--use-strict",
                    "--experimental-modules",
                    "--experimental-strip-types",
                    "--experimental-transform-types",
                }:
                    incomplete_reasons.append(f"node_option_unknown:{token}")
                    break
                index += 1
                continue
            # Positional script path / command: stop scanning this node invocation.
            if incomplete_reasons is not None and Path(token).suffix.lower() in {".md", ".markdown"}:
                incomplete_reasons.append(f"node_executable_document:{token}")
            break


def _static_node_test_invocation(tokens: list[str], unknown: list[str]) -> bool:
    """Cover a finite Node test CLI grammar, without granting execution."""
    if "--test" not in tokens:
        return False
    boolean_flags = {"--test", "--no-warnings", "--enable-source-maps"}
    value_flags = {"--import", "--require", "-r"}
    index = 0
    saw_test = False
    while index < len(tokens):
        token = tokens[index]
        if token in boolean_flags:
            saw_test = saw_test or token == "--test"
        elif token in value_flags:
            if index + 1 >= len(tokens) or not tokens[index + 1] or tokens[index + 1].startswith("-"):
                unknown.append(f"invalid_node_test_option_argument:{token}")
                return True
            index += 1
            if tokens[index] not in {"tsx", "tsx/esm", "tsx/cjs"}:
                unknown.append(f"node_test_loader_unknown:{tokens[index]}")
        elif token.startswith(tuple(flag + "=" for flag in value_flags if flag.startswith("--"))):
            value = token.split("=", 1)[1]
            if value not in {"tsx", "tsx/esm", "tsx/cjs"}:
                unknown.append(f"invalid_node_test_option_argument:{token}")
        elif token.startswith("-"):
            unknown.append(f"node_test_option_unknown:{token}")
        else:
            # A positional script before --test makes the latter a script arg.
            if not saw_test:
                return False
            for path in tokens[index:]:
                if path.startswith("-") or Path(path).suffix.lower() not in _TS_JS_SOURCE_EXTS:
                    unknown.append(f"node_test_source_unknown:{path}")
            break
        index += 1
    return True


def _static_shell_node_sources(
    script: str, *, declared_scripts: frozenset[str] = frozenset()
) -> tuple[tuple[str, ...], list[str]]:
    """Bounded lexical discovery, not a shell evaluator or full shell parser.

    Preserve quoted words and operator boundaries, then inspect only executable
    positions. Unsupported syntax and expansion carry explicit uncertainty.
    """
    lexical = re.compile(
        r"""(?P<operator>[;&|()\n]+)|(?P<word>(?:[^\s;&|()'"\\]+|\\[\s\S]|'[^']*'|"(?:\\[\s\S]|[^"\\])*")+)"""
    )
    words: list[tuple[str, bool]] = []
    unknown: list[str] = []
    glob_word_positions: set[int] = set()
    cursor = 0
    for match in lexical.finditer(script):
        if match.start() < cursor:
            continue
        if script[cursor : match.start()].strip():
            raise ValueError("unsupported shell lexical syntax")
        raw = match.group()
        operator = match.group("operator") is not None
        if operator:
            words.append((raw, True))
        else:
            if raw.startswith("#"):
                newline = script.find("\n", match.start())
                cursor = newline if newline >= 0 else len(script)
                continue
            tokenized = shlex.split(raw, posix=True)
            if len(tokenized) != 1:
                raise ValueError("unknown shell word")
            words.append((tokenized[0], False))
            # Single-quoted code is literal. Double-quoted expansion is dynamic.
            if _shell_word_has_expansion(raw):
                unknown.append("shell_expansion")
            unquoted = re.sub(r"""("(?:\\.|[^"\\])*"|'[^']*')""", "", raw)
            if re.search(r"[<> {}]", unquoted):
                unknown.append("unsupported_shell_form")
            if re.search(r"(?<!\\)[*?\[]", unquoted):
                glob_word_positions.add(len(words) - 1)
        cursor = match.end()
    if script[cursor:].strip():
        raise ValueError("unsupported shell lexical syntax")
    sources: list[str] = []
    has_node_test = any(value == "--test" and not operator for value, operator in words)
    index = 0
    command_position = True
    while index < len(words):
        word, operator = words[index]
        if operator:
            if has_node_test and command_position:
                unknown.append("node_test_shell_clause_missing")
            if has_node_test and word != "&&":
                unknown.append(f"node_test_shell_operator_unknown:{word}")
            if word not in {";", "&&", "||", "|", "(", ")", "\n"}:
                unknown.append(f"shell_operator_unknown:{word}")
            command_position = True
            index += 1
            continue
        if not command_position:
            index += 1
            continue
        if re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", word) or word in {"exec", "env"}:
            if has_node_test:
                unknown.append(f"node_test_shell_prefix_unknown:{word}")
            index += 1
            continue
        command_position = False
        end = index + 1
        while end < len(words) and not words[end][1]:
            end += 1
        args = [value for value, _ in words[index + 1 : end]]
        executable = os.path.basename(word).lower()
        if (
            has_node_test
            and executable in {"npm", "npm.cmd"}
            and (len(args) != 2 or args[0] != "run" or args[1] not in declared_scripts)
        ):
            unknown.append("node_test_npm_clause_unknown")
        if (
            has_node_test
            and executable == "tsc"
            and args
            and (
                args != ["--noEmit"]
                and not (len(args) == 2 and args[0] in {"-p", "--project"} and Path(args[1]).suffix.lower() == ".json")
            )
        ):
            unknown.append("node_test_tsc_clause_unknown")
        if has_node_test and os.path.basename(word).lower() not in {"node", "node.exe", "npm", "npm.cmd", "tsc"}:
            unknown.append(f"node_test_shell_command_unknown:{word}")
        if Path(word).suffix.lower() in {".md", ".markdown"}:
            unknown.append(f"shell_executable_document:{word}")
        if word.startswith("-"):
            unknown.append(f"shell_executable_unknown:{word}")
        if word in {
            "if",
            "then",
            "else",
            "elif",
            "fi",
            "for",
            "while",
            "until",
            "do",
            "done",
            "case",
            "esac",
            "function",
            "sh",
            "bash",
            "zsh",
            "eval",
            "source",
            ".",
            "command",
            "npx",
        }:
            unknown.append(f"shell_command_unknown:{word}")
        if os.path.basename(word).lower() not in {"node", "node.exe"}:
            index += 1
            continue
        tokens = [value for value, _ in words[index:end]]
        test_unknown: list[str] = []
        if _static_node_test_invocation(args, test_unknown) and not test_unknown:
            # The finite Node test grammar identifies these as source-file
            # selectors. Runtime owns expansion and actual execution; ordinary
            # test globs do not make the executable identity unknown.
            glob_word_positions.difference_update(range(index + 1, end))
        sources.extend(_iter_node_eval_sources(tokens, incomplete_reasons=unknown))
        index = end
    if has_node_test and command_position:
        unknown.append("node_test_shell_clause_missing")
    if glob_word_positions:
        unknown.append("shell_glob_expansion")
    return tuple(sources), unknown


def _shell_word_has_expansion(raw: str) -> bool:
    quote = ""
    escaped = False
    for char in raw:
        if escaped:
            escaped = False
            continue
        if quote == "'":
            if char == "'":
                quote = ""
            continue
        if char == "\\":
            escaped = True
        elif char in {"$", "`"}:
            return True
        elif char == quote:
            quote = ""
        elif not quote and char in {"'", '"'}:
            quote = char
    return False


def _check_javascript_snippet_syntax(source: str) -> str:
    node = shutil.which("node")
    if not node:
        return ""
    input_source = source if source.endswith("\n") else f"{source}\n"
    try:
        proc = subprocess.run(
            [node, "--check", "-"],
            input=input_source,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        return f"syntax check could not run: {exc}"
    if proc.returncode == 0:
        return ""
    return _compress_node_syntax_error(proc.stderr or proc.stdout, "[stdin]")


def _package_declares_dependency(payload: dict[str, Any], package_name: str) -> bool:
    target = str(package_name or "").strip()
    if not target:
        return False
    for section_name in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        section = payload.get(section_name)
        if isinstance(section, dict) and target in {str(name).strip() for name in section}:
            return True
    return False


def _package_manifest_requires_typescript(root_full: Path, payload: dict[str, Any]) -> bool:
    if not (root_full / "tsconfig.json").is_file():
        return False
    scripts = payload.get("scripts")
    if isinstance(scripts, dict):
        for script_value in scripts.values():
            if _NPM_SCRIPT_TSC_RE.search(str(script_value or "")):
                return True
    for relative_path in _iter_workspace_relative_files(root_full):
        if Path(relative_path).suffix.lower() in _TS_SOURCE_EXTS:
            return True
    return False


def _workspace_has_node_source_files(root_full: Path) -> bool:
    for relative_path in _iter_workspace_relative_files(root_full):
        if _is_test_like_artifact_path(relative_path):
            continue
        if Path(relative_path).suffix.lower() in _TS_JS_SOURCE_EXTS:
            return True
    return False


def _workspace_has_node_test_files(root_full: Path) -> bool:
    return any(
        _is_test_like_artifact_path(relative_path) for relative_path in _iter_workspace_relative_files(root_full)
    )


def _npm_script_node_test_directory_target_issue(
    error: str,
    relative_path: str,
    *,
    script_name: str,
    target_directory: str,
) -> ArtifactQualityIssue:
    message = str(error or "").strip()
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    return ArtifactQualityIssue(
        code="npm_script_node_test_directory_target",
        message=message,
        path=relative_path,
        source="npm_script_test_target_scanner",
        metadata={
            "raw": str(error or "").strip(),
            "manifest_path": relative_path,
            "script_name": script_name,
            "target_directory": target_directory,
            "script_issue": "node_test_directory_target",
            "script_issue_source": "npm_script_test_target_scanner",
            "diagnostic_kind": "npm_script_node_test_directory_target",
        },
    )


def _scan_npm_script_node_test_directory_target_evidence(
    root_full: Path,
    tokens: list[str],
    script_name: str,
    relative_path: str,
) -> _FileArtifactQualityEvidence:
    """Return npm script node-test directory target findings as strings and typed issues."""

    if str(script_name or "").strip().lower() != "test":
        return _FileArtifactQualityEvidence()
    errors: list[str] = []
    issues: list[ArtifactQualityIssue] = []
    for index, token in enumerate(tokens[:-1]):
        command = os.path.basename(str(token or "").strip().lower())
        if command not in {"node", "node.exe"}:
            continue
        if not any(_node_token_enables_test_runner(item) for item in tokens[index + 1 :]):
            continue
        entrypoint = _npm_script_entrypoint_after_command(tokens, index)
        normalized = entrypoint.replace("\\", "/").strip().strip("'\"")
        if not normalized or normalized.startswith(("/", "http://", "https://")) or ".." in normalized.split("/"):
            continue
        if Path(normalized).suffix:
            continue
        target_dir = root_full / normalized
        if not target_dir.is_dir():
            continue
        if not _directory_has_node_test_files(target_dir):
            continue
        error = (
            "Artifact quality scan failed: npm package manifest script "
            f"{script_name!r} references test directory {normalized!r} instead of concrete test files in "
            f"{relative_path}"
        )
        errors.append(error)
        issues.append(
            _npm_script_node_test_directory_target_issue(
                error,
                relative_path,
                script_name=script_name,
                target_directory=normalized,
            )
        )
    return _FileArtifactQualityEvidence(errors=tuple(errors), issues=tuple(issues))


def _node_token_enables_test_runner(token: str) -> bool:
    normalized = str(token or "").strip().lower()
    return normalized == "--test" or normalized.startswith("--test=")


def _directory_has_node_test_files(directory: Path) -> bool:
    try:
        for path in directory.rglob("*"):
            if path.is_file() and _is_test_like_artifact_path(path.as_posix()):
                return True
    except OSError:
        return False
    return False


def _package_module_type_mismatch_issue(error: str, relative_path: str, *, source_path: str) -> ArtifactQualityIssue:
    message = str(error or "").strip()
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    return ArtifactQualityIssue(
        code="package_module_type_commonjs_mismatch",
        message=message,
        path=relative_path,
        source="package_module_type_scanner",
        metadata={
            "raw": str(error or "").strip(),
            "manifest_path": relative_path,
            "source_path": source_path,
            "declared_type": "module",
            "runtime_syntax": "commonjs",
            "diagnostic_kind": "package_module_type_commonjs_mismatch",
        },
    )


def _scan_package_module_type_mismatch_evidence(
    root_full: Path,
    payload: dict[str, Any],
    relative_path: str,
) -> _FileArtifactQualityEvidence:
    """Return package module-type mismatch findings as strings and direct typed issues."""

    if str(payload.get("type") or "").strip().lower() != "module":
        return _FileArtifactQualityEvidence()
    errors: list[str] = []
    issues: list[ArtifactQualityIssue] = []
    for candidate in _iter_workspace_relative_files(root_full):
        if Path(candidate).suffix.lower() not in {".js", ".jsx"}:
            continue
        full_path = root_full / candidate
        try:
            text = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _COMMONJS_RUNTIME_TOKEN_RE.search(text):
            error = (
                "Artifact quality scan failed: JavaScript source "
                f"{candidate} uses CommonJS runtime syntax; npm package manifest declares type=module but workspace "
                f"JavaScript uses CommonJS runtime syntax in {relative_path}"
            )
            errors.append(error)
            issues.append(_package_module_type_mismatch_issue(error, relative_path, source_path=candidate))
    return _FileArtifactQualityEvidence(errors=tuple(errors[:20]), issues=tuple(issues[:20]))


def _npm_script_missing_local_config_issue(
    error: str,
    relative_path: str,
    *,
    script_name: str,
    config_path: str,
) -> ArtifactQualityIssue:
    message = str(error or "").strip()
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    return ArtifactQualityIssue(
        code="npm_script_missing_local_config",
        message=message,
        path=relative_path,
        source="npm_script_config_scanner",
        metadata={
            "raw": str(error or "").strip(),
            "manifest_path": relative_path,
            "script_issue": "missing_local_config",
            "script_issue_source": "npm_script_config_scanner",
            "script_name": script_name,
            "config_path": config_path,
            "diagnostic_kind": "npm_script_missing_local_config",
        },
    )


def _scan_npm_script_missing_local_config_evidence(
    root_full: Path,
    tokens: list[str],
    script_name: str,
    relative_path: str,
) -> _FileArtifactQualityEvidence:
    """Return npm script missing-config findings as strings and typed issues."""

    errors: list[str] = []
    issues: list[ArtifactQualityIssue] = []
    for index, token in enumerate(tokens):
        config_path = ""
        if token == "--config" and index + 1 < len(tokens):
            config_path = str(tokens[index + 1] or "")
        elif token.startswith("--config="):
            config_path = token.split("=", 1)[1]
        config_path = config_path.strip().strip("'\"")
        if not config_path:
            continue
        normalized = config_path.replace("\\", "/")
        if normalized.startswith(("/", "http://", "https://")) or ".." in normalized.split("/"):
            continue
        if Path(normalized).suffix.lower() not in {".js", ".mjs", ".cjs", ".ts", ".mts", ".cts"}:
            continue
        if not (root_full / normalized).is_file():
            error = (
                "Artifact quality scan failed: npm package manifest script "
                f"{script_name!r} references missing config file {normalized!r} in {relative_path}"
            )
            errors.append(error)
            issues.append(
                _npm_script_missing_local_config_issue(
                    error,
                    relative_path,
                    script_name=script_name,
                    config_path=normalized,
                )
            )
    return _FileArtifactQualityEvidence(errors=tuple(errors), issues=tuple(issues))


def _npm_script_entrypoint_after_command(tokens: list[str], command_index: int) -> str:
    command = str(tokens[command_index] or "").strip().lower()
    index = command_index + 1
    while index < len(tokens):
        token = str(tokens[index] or "").strip()
        if not token or token in _NPM_SCRIPT_SEPARATORS:
            return ""
        lowered = token.lower()
        if lowered in _NPM_SCRIPT_ENTRYPOINT_SUBCOMMANDS.get(command, set()):
            index += 1
            continue
        if lowered in _NPM_NODE_INLINE_CODE_FLAGS:
            return ""
        if lowered in _NPM_NODE_OPTION_VALUE_FLAGS:
            index += 2
            continue
        if lowered.startswith("--loader=") or lowered.startswith("--require=") or lowered.startswith("--import="):
            index += 1
            continue
        if lowered.startswith("-"):
            index += 1
            continue
        return token
    return ""
