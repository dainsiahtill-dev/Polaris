"""npm_scripts domain for JavaScript/Node syntax repairs."""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from ..contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ._shared import (
    _compiled_typescript_entrypoint,
    _compiled_typescript_entrypoint_for_missing,
    _diagnostic_script_name,
    _fallback_script_for_missing_entrypoint,
    _has_typescript_context,
    _missing_entrypoints,
    _missing_entrypoints_from_diagnostics,
    _missing_node_dist_entrypoints,
    _missing_node_dist_entrypoints_from_diagnostics,
    _normalize_base_files,
    _normalize_repair_path,
    _parse_package_json,
)
from .constants import (
    _HTTP_SERVER_FIXED_PORT_RE,
    _PLACEHOLDER_NPM_SCRIPT_RE,
    _PYTHON_COMMAND_NPM_SCRIPT_RE,
    _PYTHON_COMMAND_TOKEN_RE,
    _RECURSIVE_NPM_SCRIPT_RE,
    _REPAIRABLE_TEST_SCRIPT_ISSUES,
    NPM_SCRIPT_CONTRACT_SOURCE_TOOL,
)


def build_npm_script_contract_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build structured JSON operations for safe package script contract repairs."""

    normalized_base = _normalize_base_files(base_files)
    package_text = normalized_base.get("package.json")
    if package_text is None:
        return None
    package_payload = _parse_package_json(package_text)
    if package_payload is None:
        return None
    matched_diagnostics = tuple(
        diagnostic for diagnostic in diagnostics if _is_npm_script_contract_diagnostic(diagnostic)
    )
    if not matched_diagnostics:
        return None

    scripts_raw = package_payload.get("scripts")
    scripts: dict[str, Any] = dict(scripts_raw) if isinstance(scripts_raw, dict) else {}
    updates: dict[tuple[str, ...], str] = {}
    raw_errors = [str(diagnostic.raw or diagnostic.message or "") for diagnostic in matched_diagnostics]
    missing_entrypoints = _missing_entrypoints(raw_errors)
    missing_entrypoints.update(_missing_entrypoints_from_diagnostics(matched_diagnostics))
    has_typescript_context = _has_typescript_context(normalized_base, package_payload)
    has_node_test_runner_contract = _has_node_test_runner_contract_error(
        raw_errors
    ) or _has_node_test_runner_contract_diagnostic(matched_diagnostics)

    for script_name in _script_names_for_manifest_issue(
        matched_diagnostics,
        "placeholder_command",
        fallback_names=_placeholder_scripts(raw_errors),
    ):
        replacement = _fallback_script_for_placeholder_script(
            script_name,
            normalized_base,
            package_payload,
            has_typescript_context=has_typescript_context,
        )
        if replacement:
            updates[("scripts", script_name)] = replacement

    python_command_script_names = _script_names_for_manifest_issue(
        matched_diagnostics,
        "python_command",
    )
    for script_name in _python_command_scripts(
        raw_errors,
        scripts,
        known_script_names=python_command_script_names,
    ):
        replacement = _fallback_script_for_python_command_script(
            script_name,
            normalized_base,
            package_payload,
            has_typescript_context=has_typescript_context,
        )
        if replacement:
            updates[("scripts", script_name)] = replacement

    if has_typescript_context:
        for script_name in _script_names_for_manifest_issue(
            matched_diagnostics,
            "recursive_script",
            fallback_names=_recursive_scripts(raw_errors),
        ):
            replacement = _fallback_script_for_recursive_script(script_name, normalized_base, package_payload)
            if replacement:
                updates[("scripts", script_name)] = replacement

    if has_typescript_context and "build" not in scripts:
        compile_script = str(scripts.get("compile") or "").strip()
        updates[("scripts", "build")] = "npm run compile" if compile_script else "tsc"

    missing_node_dist_entrypoints = tuple(
        dict.fromkeys(
            (
                *_missing_node_dist_entrypoints(raw_errors),
                *_missing_node_dist_entrypoints_from_diagnostics(matched_diagnostics),
            )
        )
    )
    for missing_entrypoint in missing_node_dist_entrypoints:
        replacement_entrypoint = _compiled_typescript_entrypoint_for_missing(
            normalized_base,
            package_payload,
            missing_entrypoint=missing_entrypoint,
        )
        if not replacement_entrypoint or replacement_entrypoint == missing_entrypoint:
            continue
        for script_name, script_value in scripts.items():
            script_text = str(script_value or "")
            if missing_entrypoint in script_text:
                updates[("scripts", str(script_name))] = script_text.replace(missing_entrypoint, replacement_entrypoint)

    if has_node_test_runner_contract:
        updates[("scripts", "test")] = _node_test_runner_script(normalized_base)
    elif has_typescript_context and (
        _has_repairable_test_script_error(raw_errors) or _has_repairable_test_script_diagnostic(matched_diagnostics)
    ):
        updates[("scripts", "test")] = (
            _fallback_script_for_recursive_script("test", normalized_base, package_payload) or "npm run build"
        )

    if _has_fixed_port_start_script_error(raw_errors) or _has_fixed_port_start_script_diagnostic(matched_diagnostics):
        for script_name in ("start", "serve", "dev", "preview"):
            script_text = str(scripts.get(script_name) or "").strip()
            replacement = _http_server_dynamic_port_script(script_text)
            if replacement and replacement != script_text:
                updates[("scripts", script_name)] = replacement

    if has_typescript_context and (
        _has_typescript_source_loader_start_error(raw_errors)
        or _has_typescript_source_loader_start_diagnostic(matched_diagnostics)
    ):
        replacement = _fallback_script_for_recursive_script("start", normalized_base, package_payload)
        if replacement:
            updates[("scripts", "start")] = replacement

    if has_typescript_context and missing_entrypoints.get("verify"):
        updates[("scripts", "verify")] = "npm run build"
        test_script = str(scripts.get("test") or "")
        if "verify" in test_script:
            updates[("scripts", "test")] = "npm run verify"

    if has_typescript_context and missing_entrypoints.get("start"):
        entrypoint = _compiled_typescript_entrypoint(normalized_base, package_payload)
        updates[("scripts", "start")] = f"npm run build && node {entrypoint}" if entrypoint else "npm run build"

    for script_name, _entrypoint in missing_entrypoints.items():
        if script_name in {"test", "start", "verify"}:
            continue
        if has_typescript_context:
            updates[("scripts", script_name)] = _fallback_script_for_missing_entrypoint(script_name)
            continue
        # Live L2-18: extra ``test:py`` pointed at missing
        # ``tests/run_python_tests.js``. Official ``test`` already existed.
        # Extra-script fallback used to be TypeScript-only, so JS materialization
        # stalled on the dangling helper instead of rewriting to the official
        # Node test contract.
        replacement = _fallback_script_for_python_command_script(
            script_name,
            normalized_base,
            package_payload,
            has_typescript_context=False,
        )
        if replacement:
            updates[("scripts", script_name)] = replacement

    if not updates:
        return None

    before_hash = sha256_text(package_text)
    operations = tuple(
        RepairOperation(
            kind="json_set",
            path="package.json",
            json_path=json_path,
            value=value,
            before_hash=before_hash,
            metadata={
                "repair_kind": "npm_script_contract",
                "structured_operation": "json",
                "diagnostic_ids": [diagnostic.diagnostic_id for diagnostic in matched_diagnostics],
            },
        )
        for json_path, value in sorted(updates.items())
    )
    return RepairPlan(
        rule_id="javascript.npm_script_contract",
        source_tool=NPM_SCRIPT_CONTRACT_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={
            "structured_manifest_repair": True,
            "updated_json_paths": [".".join(path) for path in sorted(updates)],
        },
    )


def _is_npm_script_contract_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    raw = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    metadata = diagnostic.metadata if isinstance(diagnostic.metadata, Mapping) else {}
    if str(diagnostic.code or "").strip() == "npm_manifest_invalid":
        return True
    if _diagnostic_script_name(diagnostic) or str(metadata.get("script_issue") or "").strip():
        return True
    return (
        "npm default failing test script" in raw
        or "npm placeholder test script" in raw
        or "npm manifest-only test script" in raw
        or "npm package manifest contains python command in script" in raw
        or "npm package manifest script" in raw
        or "references missing local entrypoint:" in raw
        or "test script must use node --test" in raw
        or "cannot find module './src/" in raw
        or ("cannot find module" in raw and "/dist/" in raw)
        or "node --import tsx/esm" in raw
        or "err_require_cycle_module" in raw
        or "cannot require() es module" in raw
        or ("npm run test" in raw and "strip-types" in raw)
        or _has_fixed_port_start_script_error((raw,))
    )


def _script_names_for_manifest_issue(
    diagnostics: Sequence[RepairDiagnostic],
    issue: str,
    *,
    fallback_names: Sequence[str] = (),
) -> tuple[str, ...]:
    script_names: list[str] = [str(name or "").strip() for name in fallback_names if str(name or "").strip()]
    for diagnostic in diagnostics:
        metadata = diagnostic.metadata if isinstance(diagnostic.metadata, Mapping) else {}
        script_issue = str(metadata.get("script_issue") or "").strip()
        script_name = _diagnostic_script_name(diagnostic)
        if script_issue == issue and script_name:
            script_names.append(script_name)
    return tuple(dict.fromkeys(script_names))


def _has_node_test_runner_contract_error(errors: Sequence[str]) -> bool:
    joined = "\n".join(str(error or "") for error in errors).lower()
    return "test script must use node --test" in joined


def _has_node_test_runner_contract_diagnostic(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    for diagnostic in diagnostics:
        metadata = diagnostic.metadata if isinstance(diagnostic.metadata, Mapping) else {}
        script_name = _diagnostic_script_name(diagnostic)
        script_issue = str(metadata.get("script_issue") or "").strip()
        if script_name == "test" and script_issue == "node_test_runner_contract":
            return True
    return False


def _has_fixed_port_start_script_error(errors: Sequence[str]) -> bool:
    joined = "\n".join(str(error or "") for error in errors).lower()
    start_invoked = "npm run start" in joined or "npm start" in joined or "npm run serve" in joined
    port_conflict = "eaddrinuse" in joined or "address already in use" in joined
    return start_invoked and port_conflict


def _has_fixed_port_start_script_diagnostic(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    for diagnostic in diagnostics:
        metadata = diagnostic.metadata if isinstance(diagnostic.metadata, Mapping) else {}
        script_name = _diagnostic_script_name(diagnostic)
        script_issue = str(metadata.get("script_issue") or "").strip()
        if script_name in {"start", "serve", "dev", "preview"} and script_issue == "fixed_port_conflict":
            return True
    return False


def _has_typescript_source_loader_start_error(errors: Sequence[str]) -> bool:
    joined = "\n".join(str(error or "") for error in errors).lower()
    start_invoked = "npm run start" in joined or "npm start" in joined
    source_loader = "ts-node" in joined or "node --loader" in joined or ".ts" in joined
    require_cycle = "err_require_cycle_module" in joined or "cannot require() es module" in joined
    return start_invoked and source_loader and require_cycle


def _has_typescript_source_loader_start_diagnostic(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    for diagnostic in diagnostics:
        metadata = diagnostic.metadata if isinstance(diagnostic.metadata, Mapping) else {}
        script_name = _diagnostic_script_name(diagnostic)
        script_issue = str(metadata.get("script_issue") or "").strip()
        if script_name == "start" and script_issue == "typescript_source_loader_require_cycle":
            return True
    return False


def _http_server_dynamic_port_script(script_text: str) -> str:
    script = str(script_text or "").strip()
    if "http-server" not in script or "PORT" in script:
        return ""
    replaced = _HTTP_SERVER_FIXED_PORT_RE.sub(r"\g<flag>${PORT:-0}", script, count=1)
    return replaced if replaced != script else ""


def _has_repairable_test_script_error(errors: Sequence[str]) -> bool:
    joined = "\n".join(str(error or "") for error in errors).lower()
    markers = (
        "npm default failing test script",
        "npm placeholder test script",
        "npm manifest-only test script",
        "npm package manifest script 'test' has invalid shell syntax",
        "npm package manifest script 'test' has invalid node eval syntax",
        "npm package manifest script 'test' uses shell command substitution",
        "npm package manifest script 'test' references missing local entrypoint",
        "script 'test' references missing local entrypoint",
        "npm package manifest script 'test' is a placeholder command",
        "npm package manifest script 'test' swallows command failures",
        "cannot find module './src/",
        "strip-types",
    )
    return any(marker in joined for marker in markers)


def _has_repairable_test_script_diagnostic(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    for diagnostic in diagnostics:
        metadata = diagnostic.metadata if isinstance(diagnostic.metadata, Mapping) else {}
        script_name = _diagnostic_script_name(diagnostic)
        script_issue = str(metadata.get("script_issue") or "").strip()
        if script_name == "test" and script_issue in _REPAIRABLE_TEST_SCRIPT_ISSUES:
            return True
    return False


def _node_test_runner_script(base_files: Mapping[str, str]) -> str:
    test_paths = sorted(
        path
        for path in base_files
        if path.startswith("tests/")
        and path.endswith(".js")
        and (PurePosixPath(path).name.startswith("test_") or PurePosixPath(path).name.endswith(".test.js"))
    )
    if "tests/test_basic.js" in test_paths:
        test_paths.remove("tests/test_basic.js")
        test_paths.insert(0, "tests/test_basic.js")
    return "node --test" if not test_paths else "node --test " + " ".join(test_paths)


def _placeholder_scripts(errors: Sequence[str]) -> tuple[str, ...]:
    scripts: list[str] = []
    for error in errors:
        for match in _PLACEHOLDER_NPM_SCRIPT_RE.finditer(str(error or "")):
            script_name = str(match.group(1) or "").strip()
            if script_name:
                scripts.append(script_name)
    return tuple(dict.fromkeys(scripts))


def _python_command_scripts(
    errors: Sequence[str],
    scripts: Mapping[str, Any],
    *,
    known_script_names: Sequence[str] = (),
) -> tuple[str, ...]:
    script_names: list[str] = [
        str(script_name or "").strip() for script_name in known_script_names if str(script_name or "").strip()
    ]
    for error in errors:
        for match in _PYTHON_COMMAND_NPM_SCRIPT_RE.finditer(str(error or "")):
            script_name = str(match.group(1) or "").strip()
            if script_name:
                script_names.append(script_name)
    if script_names:
        for script_name, script_value in scripts.items():
            if _PYTHON_COMMAND_TOKEN_RE.search(str(script_value or "")):
                script_names.append(str(script_name))
    return tuple(dict.fromkeys(script_names))


def _fallback_script_for_python_command_script(
    script_name: str,
    base_files: Mapping[str, str],
    package_payload: Mapping[str, Any],
    *,
    has_typescript_context: bool,
) -> str:
    normalized_script_name = str(script_name or "").strip().lower()
    if not normalized_script_name:
        return ""
    if "test" in normalized_script_name:
        return _node_test_runner_script(base_files)
    if normalized_script_name in {"verify", "build"} and has_typescript_context:
        return _fallback_script_for_recursive_script(normalized_script_name, base_files, package_payload)
    if normalized_script_name in {"lint", "check", "typecheck"}:
        if has_typescript_context:
            return _fallback_script_for_missing_entrypoint(normalized_script_name)
        return _node_source_syntax_check_script(base_files)
    return _node_source_syntax_check_script(base_files)


def _fallback_script_for_placeholder_script(
    script_name: str,
    base_files: Mapping[str, str],
    package_payload: Mapping[str, Any],
    *,
    has_typescript_context: bool,
) -> str:
    normalized_script_name = str(script_name or "").strip()
    if not normalized_script_name:
        return ""
    if normalized_script_name == "test":
        if not has_typescript_context and not _has_node_test_files(base_files):
            return ""
        return _node_test_runner_script(base_files)
    if normalized_script_name in {"lint", "check", "typecheck"}:
        if has_typescript_context:
            return _fallback_script_for_missing_entrypoint(normalized_script_name)
        return _node_source_syntax_check_script(base_files)
    if normalized_script_name in {"build", "verify"}:
        scripts_raw = package_payload.get("scripts")
        scripts: Mapping[str, Any] = scripts_raw if isinstance(scripts_raw, Mapping) else {}
        if has_typescript_context:
            return _fallback_script_for_recursive_script(normalized_script_name, base_files, package_payload)
        if normalized_script_name == "verify":
            for upstream_script_name in ("test", "build", "lint", "check", "typecheck"):
                upstream_script = _non_placeholder_script(scripts, upstream_script_name)
                if upstream_script:
                    return f"npm run {upstream_script_name}"
        return _node_source_syntax_check_script(base_files)
    return ""


def _node_source_syntax_check_script(base_files: Mapping[str, str]) -> str:
    source_paths = [path for path in sorted(base_files) if _is_plain_javascript_source_path(path)]
    if not source_paths:
        return ""
    return " && ".join(f"node --check {shlex.quote(path)}" for path in source_paths)


def _is_plain_javascript_source_path(path: str) -> bool:
    normalized = _normalize_repair_path(path)
    if not normalized.endswith((".js", ".mjs", ".cjs")):
        return False
    excluded_prefixes = ("node_modules/", "dist/", "build/", "out/", "coverage/", "tests/")
    if normalized.startswith(excluded_prefixes):
        return False
    name = PurePosixPath(normalized).name
    return not (
        name.startswith(".") or name.endswith(".test.js") or name.endswith(".spec.js") or name.startswith("test_")
    )


def _non_placeholder_script(scripts: Mapping[str, Any], script_name: str) -> str:
    script = str(scripts.get(script_name) or "").strip()
    if not script or _looks_like_placeholder_script(script):
        return ""
    return script


def _looks_like_placeholder_script(script: str) -> bool:
    lowered = script.lower()
    placeholder_markers = (
        "no test specified",
        "placeholder",
        "todo",
        "not implemented",
        "stub",
        "wire ",
        "coming soon",
    )
    if any(marker in lowered for marker in placeholder_markers):
        return True
    return lowered.startswith("echo ") and "exit 0" in lowered


def _has_node_test_files(base_files: Mapping[str, str]) -> bool:
    return any(
        path.startswith("tests/")
        and path.endswith(".js")
        and (PurePosixPath(path).name.startswith("test_") or PurePosixPath(path).name.endswith(".test.js"))
        for path in base_files
    )


def _recursive_scripts(errors: Sequence[str]) -> tuple[str, ...]:
    scripts: list[str] = []
    for error in errors:
        match = _RECURSIVE_NPM_SCRIPT_RE.search(str(error or ""))
        if not match:
            continue
        script_name = str(match.group(1) or "").strip()
        if script_name:
            scripts.append(script_name)
    return tuple(dict.fromkeys(scripts))


def _fallback_script_for_recursive_script(
    script_name: str,
    base_files: Mapping[str, str],
    package_payload: Mapping[str, Any],
) -> str:
    normalized = str(script_name or "").strip().lower()
    if normalized in {"build", "compile"}:
        return "tsc -p tsconfig.json" if "tsconfig.json" in base_files else "tsc"
    if normalized in {"check", "typecheck"}:
        return "tsc --noEmit"
    if normalized == "verify":
        if "src/verify.ts" in base_files:
            return "npm run build && node dist/verify.js"
        return "npm run build"
    if normalized == "test":
        if "src/verify.ts" in base_files:
            return "npm run build && node dist/verify.js"
        return "npm run build"
    if normalized in {"start", "serve", "dev", "preview"}:
        entrypoint = _compiled_typescript_entrypoint(base_files, package_payload)
        return f"npm run build && node {entrypoint}" if entrypoint else "npm run build"
    return ""
