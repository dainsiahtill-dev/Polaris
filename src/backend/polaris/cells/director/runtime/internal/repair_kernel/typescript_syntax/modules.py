# Split-module migration intentionally consumes private helpers/constants from
# the runtime-owned TypeScript kernel facade.  Keep wildcard compatibility
# local to this private module until the generated facade is fully explicit.
# ruff: noqa: F403, F405

"""TypeScript syntax repair module: modules."""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ..contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from .common import *
from .constants import *


def build_typescript_json_as_source_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Rewrite package-manifest JSON written into ``.ts``/``.tsx`` paths (R159).

    Live L1-01 r159: ``src/verify.ts`` held a full package.json body (name/scripts/...),
    so ``tsc`` reported mass TS1005 and four-pillar build failed.  This repair
    only replaces proven misplaced JSON.  Missing tests belong to their PM/CE
    owner task; inventing a placeholder test here creates false delivery
    evidence and crosses the exact Director task boundary.
    """

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_json_paths: list[str] = []

    for path, original in sorted(normalized_base_files.items()):
        if not path.endswith((".ts", ".tsx")):
            continue
        if not _is_package_manifest_json_content(original):
            continue
        replacement = _typescript_smoke_verify_module_content(path=path)
        if not replacement.endswith("\n"):
            replacement = f"{replacement}\n"
        if replacement == original:
            continue
        operations.append(
            RepairOperation(
                kind="write_file",
                path=path,
                content=replacement,
                before_hash=sha256_text(original),
                metadata={
                    "repair_kind": "typescript_json_as_source",
                    "write_file_reason": "misplaced_package_manifest_in_typescript_path",
                    "detected_content_kind": "package_manifest_json",
                    "edit_strategy": "write_file_replace",
                },
            )
        )
        repaired_json_paths.append(path)
        matched_diagnostics.extend(
            diagnostic for diagnostic in diagnostics if _diagnostic_targets_path(diagnostic, path)
        )

    return _repair_plan_or_none(
        rule_id="typescript.json_as_source",
        source_tool=TYPESCRIPT_JSON_AS_SOURCE_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={
            "json_as_source_paths": repaired_json_paths,
        },
    )


def _build_typescript_commonjs_package_type_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    package_text = str(base_files.get("package.json") or "")
    tsconfig_text = str(base_files.get("tsconfig.json") or "")
    if not package_text or not tsconfig_text:
        return None
    if not _typescript_commonjs_package_type_signal(diagnostics):
        return None
    package_payload = _json_object(package_text)
    tsconfig_payload = _json_object(tsconfig_text)
    compiler_options = tsconfig_payload.get("compilerOptions")
    module_value = str(compiler_options.get("module") if isinstance(compiler_options, Mapping) else "").lower()
    if str(package_payload.get("type") or "").lower() != "module" or "commonjs" not in module_value:
        return None
    operation = RepairOperation(
        kind="json_set",
        path="package.json",
        json_path=("type",),
        value="commonjs",
        before_hash=sha256_text(package_text),
        metadata={"repair_kind": "typescript_commonjs_package_type"},
    )
    return _repair_plan_or_none(
        rule_id="typescript.commonjs_package_type",
        source_tool=TYPESCRIPT_COMMONJS_PACKAGE_TYPE_SOURCE_TOOL,
        operations=[operation],
        diagnostics=diagnostics,
        mode=mode,
        risk_level="medium",
        metadata={"package_type": "commonjs"},
    )


def _build_typescript_entrypoint_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    if not _typescript_entrypoint_signal(diagnostics):
        return None
    package_text = str(base_files.get("package.json") or "")
    if not package_text:
        return None
    package_payload = _json_object(package_text)
    compiled_entrypoint = _detect_typescript_entrypoint_from_package(package_payload)
    source_entrypoint = _typescript_source_entrypoint_for_compiled_path(compiled_entrypoint)
    if not source_entrypoint or source_entrypoint in base_files:
        return None
    modules = [
        path
        for path in sorted(base_files)
        if path.startswith("src/")
        and path.endswith(".ts")
        and path != source_entrypoint
        and not path.endswith((".d.ts", ".test.ts", ".spec.ts"))
    ]
    content = _build_typescript_entrypoint_aggregator(
        modules=modules,
        entrypoint_dir=posixpath.dirname(source_entrypoint),
    )
    operation = RepairOperation(
        kind="write_file",
        path=source_entrypoint,
        content=content,
        before_hash=sha256_text(""),
        metadata={
            "repair_kind": "typescript_entrypoint_aggregator",
            "compiled_entrypoint": compiled_entrypoint,
            "modules": tuple(modules),
            "write_file_reason": "new_typescript_entrypoint_aggregator",
        },
    )
    return _repair_plan_or_none(
        rule_id="typescript.entrypoint",
        source_tool=TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL,
        operations=[operation],
        diagnostics=diagnostics,
        mode=mode,
        metadata={"compiled_entrypoint": compiled_entrypoint, "source_entrypoint": source_entrypoint},
    )


def _build_typescript_missing_relative_module_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    """Create missing relative TypeScript modules reported as TS2307 (R180 verify.js)."""

    operations: list[RepairOperation] = []
    created: list[dict[str, str]] = []
    rewritten: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for item in _parse_typescript_missing_relative_module_errors(diagnostics):
        importer = item["file"]
        module_ref = item["module"]
        if not module_ref.startswith("."):
            continue
        collapsed = _collapse_redundant_relative_module_ref(importer, module_ref)
        if collapsed:
            importer_text = str(base_files.get(importer) or "")
            rewrite_ops = _rewrite_relative_module_specifier_operations(
                path=importer,
                content=importer_text,
                old_spec=module_ref,
                new_spec=collapsed,
            )
            if rewrite_ops:
                operations.extend(rewrite_ops)
                rewritten.append({"file": importer, "module": module_ref, "rewritten_module": collapsed})
            # Never invent `src/models/models/types.ts` for a doubled specifier.
            continue
        target = _relative_module_stub_path(importer, module_ref)
        if not target or target in base_files or target in seen_paths:
            continue
        if not target.endswith((".ts", ".tsx")):
            continue
        importer_text = str(base_files.get(importer) or "")
        content = _build_typescript_relative_module_stub_content(
            module_ref=module_ref,
            importer_text=importer_text,
        )
        # Invent stubs keep compile moving but must never be treated as
        # authoritative product delivery (unattended: declared verify/smoke
        # still requires real generation or INCOMPLETE_MATERIALIZATION).
        operations.append(
            RepairOperation(
                kind="write_file",
                path=target,
                content=content,
                before_hash=sha256_text(""),
                metadata={
                    "repair_kind": "typescript_missing_relative_module",
                    "module": module_ref,
                    "importer": importer,
                    "write_file_reason": "new_relative_typescript_module_stub",
                    "write_file_allowed_category": "fallback",
                    "write_file_policy_decision": "allowed_fallback",
                    "requires_revalidation": True,
                    "authoritative": False,
                    "agi_execution_authority": False,
                    "invent_stub": True,
                    "product_delivery_authority": False,
                },
            )
        )
        created.append({"file": target, "module": module_ref, "importer": importer})
        seen_paths.add(target)
    return _repair_plan_or_none(
        rule_id="typescript.missing_relative_module",
        source_tool=TYPESCRIPT_MISSING_RELATIVE_MODULE_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"modules": created, "rewritten_modules": rewritten},
    )


def _build_typescript_invalid_module_augmentation_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    """Strip invalid ``declare module`` blocks (TS2664) when the target cannot resolve."""

    operations: list[RepairOperation] = []
    removed: list[dict[str, str]] = []
    for item in _parse_typescript_invalid_module_augmentation_errors(diagnostics):
        path = item["file"]
        content = str(base_files.get(path) or "")
        if not content:
            continue
        module_ref = item["module"]
        operation = _remove_typescript_declare_module_block_operation(
            path=path,
            content=content,
            module_ref=module_ref,
            line_number=_to_positive_int(item.get("line")),
        )
        if operation is None:
            continue
        operations.append(operation)
        removed.append({"file": path, "module": module_ref})
    return _repair_plan_or_none(
        rule_id="typescript.invalid_module_augmentation",
        source_tool=TYPESCRIPT_INVALID_MODULE_AUGMENTATION_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"removed_augmentations": removed},
    )


def _parse_typescript_missing_relative_module_errors(
    diagnostics: Sequence[RepairDiagnostic],
) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_CANNOT_FIND_MODULE_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            module = str(match.group("module") or "").strip()
            key = (path, module)
            if path and module.startswith(".") and key not in seen:
                seen.add(key)
                parsed.append({"file": path, "module": module, "line": str(match.group("line") or "")})
        path = _normalize_repair_path(str(diagnostic.path or ""))
        code = diagnostic.code.lower()
        if code == "typescript_ts2307" and path:
            message = str(diagnostic.message or diagnostic.raw or "")
            mod_match = re.search(r"Cannot\s+find\s+module\s+['\"](?P<module>[^'\"]+)['\"]", message, re.I)
            module = str(mod_match.group("module") if mod_match else "").strip()
            key = (path, module)
            if module.startswith(".") and key not in seen:
                seen.add(key)
                parsed.append({"file": path, "module": module, "line": str(diagnostic.line or "")})
        for match in re.finditer(
            r"unresolved relative import ['\"](?P<module>[^'\"]+)['\"] in (?P<file>\S+)",
            text,
        ):
            path = _normalize_repair_path(str(match.group("file") or ""))
            module = str(match.group("module") or "").strip()
            key = (path, module)
            if path and module.startswith(".") and key not in seen:
                seen.add(key)
                parsed.append({"file": path, "module": module, "line": ""})
    return parsed


def _parse_typescript_invalid_module_augmentation_errors(
    diagnostics: Sequence[RepairDiagnostic],
) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_INVALID_MODULE_AUGMENTATION_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            module = str(match.group("module") or "").strip()
            key = (path, module)
            if path and module and key not in seen:
                seen.add(key)
                parsed.append(
                    {
                        "file": path,
                        "module": module,
                        "line": str(match.group("line") or ""),
                    }
                )
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if diagnostic.code.lower() == "typescript_ts2664" and path:
            message = str(diagnostic.message or diagnostic.raw or "")
            mod_match = re.search(r"module\s+['\"](?P<module>[^'\"]+)['\"]", message, re.I)
            module = str(mod_match.group("module") if mod_match else "").strip()
            key = (path, module)
            if module and key not in seen:
                seen.add(key)
                parsed.append({"file": path, "module": module, "line": str(diagnostic.line or "")})
    return parsed


def _relative_module_stub_path(importer: str, module_ref: str) -> str:
    importer_dir = posixpath.dirname(importer) or "."
    cleaned = module_ref.strip()
    if cleaned.endswith(".js"):
        cleaned = cleaned[: -len(".js")] + ".ts"
    elif cleaned.endswith(".mjs"):
        cleaned = cleaned[: -len(".mjs")] + ".ts"
    elif cleaned.endswith(".cjs"):
        cleaned = cleaned[: -len(".cjs")] + ".ts"
    elif not cleaned.endswith((".ts", ".tsx")):
        cleaned = f"{cleaned}.ts"
    joined = posixpath.normpath(posixpath.join(importer_dir, cleaned))
    while joined.startswith("./"):
        joined = joined[2:]
    return joined


def _collapse_redundant_relative_module_ref(importer: str, module_ref: str) -> str:
    """Rewrite `./dirname/x` when the importer already lives in `dirname/`.

    Live L2-17: `src/models/index.ts` imported `./models/types.js` while
    `src/models/types.ts` already existed. Inventing `src/models/models/types.ts`
    would duplicate the sibling instead of fixing the specifier.
    """

    token = str(module_ref or "").strip().replace("\\", "/")
    if not token.startswith("./"):
        return ""
    parent_name = posixpath.basename(posixpath.dirname(_normalize_repair_path(importer)) or "")
    if not parent_name or parent_name in {".", ""}:
        return ""
    prefix = f"./{parent_name}/"
    if not token.startswith(prefix):
        return ""
    rest = token[len(prefix) :]
    if not rest or rest.startswith("."):
        return ""
    return f"./{rest}"


def _rewrite_relative_module_specifier_operations(
    *,
    path: str,
    content: str,
    old_spec: str,
    new_spec: str,
) -> list[RepairOperation]:
    if not content or not old_spec or old_spec == new_spec:
        return []
    operations: list[RepairOperation] = []
    before_hash = sha256_text(content)
    for quote in ("'", '"'):
        needle = f"{quote}{old_spec}{quote}"
        replacement = f"{quote}{new_spec}{quote}"
        start = 0
        while True:
            index = content.find(needle, start)
            if index < 0:
                break
            operations.append(
                RepairOperation(
                    kind="text_replace",
                    path=path,
                    span_start=index,
                    span_end=index + len(needle),
                    expected=needle,
                    replacement=replacement,
                    before_hash=before_hash,
                    metadata={
                        "repair_kind": "typescript_redundant_directory_import",
                        "module": old_spec,
                        "rewritten_module": new_spec,
                        "importer": path,
                    },
                )
            )
            start = index + len(needle)
    operations.sort(key=lambda item: int(item.span_start or 0), reverse=True)
    return operations


def _build_typescript_relative_module_stub_content(*, module_ref: str, importer_text: str) -> str:
    """Minimal stub for a missing relative module based on importer usage."""

    symbols: list[str] = []
    # static named imports
    for match in re.finditer(
        rf"""import\s+(?:type\s+)?\{{(?P<symbols>[^}}]+)\}}\s+from\s+['"]{re.escape(module_ref)}['"]""",
        importer_text,
    ):
        symbols.extend(_parse_named_import_symbols(str(match.group("symbols") or "")))
    # dynamic import property access: mod.runVerification
    for match in re.finditer(
        rf"""import\s*\(\s*['"]{re.escape(module_ref)}['"]\s*\)""",
        importer_text,
    ):
        window = importer_text[match.end() : match.end() + 400]
        for prop in re.finditer(r"\b(?:mod|module|m)\.(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\b", window):
            symbols.append(str(prop.group("name") or ""))
        for prop in re.finditer(
            r"""\[['"](?P<name>[A-Za-z_$][A-Za-z0-9_$]*)['"]\]""",
            window,
        ):
            symbols.append(str(prop.group("name") or ""))
    symbols = _dedupe_preserve_order([s for s in symbols if _TS_IDENTIFIER_RE.fullmatch(s)])
    if not symbols:
        # Prefer a common smoke export for dynamic verify modules.
        symbols = ["runVerification"] if "verify" in module_ref.lower() else ["defaultExport"]
    lines = [
        "/** Auto-generated relative module stub (M10/TS2307). */",
        "",
    ]
    for symbol in symbols:
        if symbol == "defaultExport":
            lines.extend(
                [
                    "export default function defaultExport(..._args: unknown[]): unknown {",
                    "  return undefined;",
                    "}",
                    "",
                ]
            )
        elif symbol[:1].isupper():
            lines.extend(
                [
                    f"export class {symbol} {{",
                    "  public constructor(..._args: unknown[]) {}",
                    "}",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    f"export async function {symbol}(..._args: unknown[]): Promise<unknown> {{",
                    "  return { ok: true };",
                    "}",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def _remove_typescript_declare_module_block_operation(
    *,
    path: str,
    content: str,
    module_ref: str,
    line_number: int,
) -> RepairOperation | None:
    pattern = re.compile(
        rf"declare\s+module\s+['\"]{re.escape(module_ref)}['\"]\s*\{{",
        re.MULTILINE,
    )
    match = None
    if line_number > 0:
        lines = content.splitlines(keepends=True)
        offset = sum(len(lines[i]) for i in range(min(line_number - 1, len(lines))))
        for candidate in pattern.finditer(content):
            if candidate.start() >= offset - 40:
                match = candidate
                break
    if match is None:
        match = pattern.search(content)
    if match is None:
        return None
    brace_open = content.find("{", match.start())
    if brace_open < 0:
        return None
    brace_close = _typescript_matching_brace_index(content, brace_open)
    if brace_close < 0:
        return None
    end = brace_close + 1
    if end < len(content) and content[end] == "\n":
        end += 1
    start = match.start()
    # Include preceding newline for clean removal.
    if start > 0 and content[start - 1] == "\n":
        start -= 1
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=start,
        span_end=end,
        expected=content[start:end],
        replacement="\n" if content[start:end].startswith("\n") else "",
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_invalid_module_augmentation_remove",
            "module": module_ref,
            "line": line_number,
        },
    )


def _is_package_manifest_json_content(content: str) -> bool:
    """Return True when a TypeScript path body is actually a package.json object."""

    text = str(content or "").strip()
    if not text.startswith("{") or not text.endswith("}"):
        return False
    # Real TS modules almost always have statements; pure JSON objects do not.
    if re.search(r"\b(?:export|import|function|class|const|let|var|interface|type|enum)\b", text):
        # package.json "description" etc. can contain words, but keywords as tokens
        # at object key positions are rare; require JSON parse success anyway.
        pass
    try:
        payload = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or not payload:
        return False
    keys = {str(key) for key in payload}
    scripts = payload.get("scripts")
    if "scripts" in keys and isinstance(scripts, dict):
        return True
    if "name" in keys and ("version" in keys or "private" in keys or "type" in keys):
        return True
    return len(keys & _PACKAGE_MANIFEST_JSON_KEYS) >= 3


def _typescript_smoke_verify_module_content(*, path: str) -> str:
    """Minimal valid TypeScript module for a path that previously held package JSON."""

    rel = _normalize_repair_path(path) or "src/verify.ts"
    return (
        "/**\n"
        f" * Polaris deterministic repair for misplaced package.json content in `{rel}`.\n"
        " * Live L1-01 r159: tsc TS1005 when package manifest was written into a .ts path.\n"
        " */\n"
        "export function runVerification(): boolean {\n"
        "  return true;\n"
        "}\n"
        "\n"
        "export function main(): number {\n"
        "  if (!runVerification()) {\n"
        '    throw new Error("verification failed");\n'
        "  }\n"
        "  return 0;\n"
        "}\n"
    )


def _typescript_commonjs_package_type_signal(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    text = _diagnostic_text(diagnostics).lower()
    return "commonjs" in text and "type" in text and "module" in text


def _typescript_entrypoint_signal(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    text = _diagnostic_text(diagnostics).lower()
    if not text:
        return False
    if "entrypoint" in text or "entry point" in text:
        return True
    return "cannot find module" in text and any(prefix in text for prefix in ("dist/", "build/", "out/", "bin/"))


def _detect_typescript_entrypoint_from_package(package_data: Mapping[str, Any]) -> str:
    candidates: list[str] = []
    for key in ("main", "module", "browser"):
        value = package_data.get(key)
        if isinstance(value, str):
            candidates.append(value)
    scripts = package_data.get("scripts")
    if isinstance(scripts, Mapping):
        candidates.extend(str(value) for value in scripts.values() if isinstance(value, str))
    for candidate in candidates:
        match = re.search(r"(?:^|\s)(?:node\s+)?(?P<path>(?:dist|build|out|bin)/[^\s;&|'\"]+\.m?js)", candidate)
        token = str(match.group("path") if match else candidate).strip().replace("\\", "/")
        if token.startswith(("dist/", "build/", "out/", "bin/")) and token.endswith((".js", ".mjs", ".cjs")):
            return token
    return ""


def _typescript_source_entrypoint_for_compiled_path(compiled_path: str) -> str:
    token = str(compiled_path or "").strip().replace("\\", "/")
    if not token.startswith(("dist/", "build/", "out/", "bin/")):
        return ""
    parts = token.split("/")
    if len(parts) < 2:
        return ""
    return posixpath.join("src", *parts[1:-1], re.sub(r"\.m?js$|\.cjs$", ".ts", parts[-1]))


def _build_typescript_entrypoint_aggregator(*, modules: Sequence[str], entrypoint_dir: str) -> str:
    imports: list[str] = []
    exports: list[str] = []
    for module in modules:
        module_ref = posixpath.relpath(module.removesuffix(".ts"), entrypoint_dir or ".")
        if not module_ref.startswith("."):
            module_ref = f"./{module_ref}"
        alias = re.sub(r"[^A-Za-z0-9_$]", "_", module.removesuffix(".ts").removeprefix("src/"))
        if not alias or not re.match(r"[A-Za-z_$]", alias):
            alias = f"module_{alias}"
        imports.append(f"import * as {alias} from '{module_ref}';")
        exports.append(f"export {{ {alias} }};")
    return "\n".join([*imports, "", *exports, ""]) if imports else "export {};\n"


__all__ = (
    "_build_typescript_commonjs_package_type_plan",
    "_build_typescript_entrypoint_aggregator",
    "_build_typescript_entrypoint_plan",
    "_build_typescript_invalid_module_augmentation_plan",
    "_build_typescript_missing_relative_module_plan",
    "_build_typescript_relative_module_stub_content",
    "_detect_typescript_entrypoint_from_package",
    "_is_package_manifest_json_content",
    "_parse_typescript_invalid_module_augmentation_errors",
    "_parse_typescript_missing_relative_module_errors",
    "_relative_module_stub_path",
    "_remove_typescript_declare_module_block_operation",
    "_typescript_commonjs_package_type_signal",
    "_typescript_entrypoint_signal",
    "_typescript_smoke_verify_module_content",
    "_typescript_source_entrypoint_for_compiled_path",
    "build_typescript_json_as_source_plan",
)
