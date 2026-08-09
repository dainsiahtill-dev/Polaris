from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from typing import Any

from ..contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ..javascript_syntax import repair_javascript_export_contract_placeholders
from ..path_files import normalize_base_files_strict, normalize_repair_path_strict
from .constants import *  # noqa: F403

"""TypeScript syntax repair module: common."""

def _typescript_diagnostic_line(diagnostic: RepairDiagnostic) -> int | None:
    if diagnostic.line:
        return diagnostic.line
    raw = str(diagnostic.raw or diagnostic.message or "")
    path = _normalize_repair_path(str(diagnostic.path or ""))
    if not raw or not path:
        return None
    match = re.search(rf"{re.escape(path)}\((?P<line>\d+),(?P<col>\d+)\)", raw)
    if not match:
        return None
    try:
        return int(match.group("line"))
    except (TypeError, ValueError):
        return None

def _typescript_global_guard_precedes(repaired_lines: Sequence[str], symbol: str) -> bool:
    guard_fragments = (
        f'typeof {symbol} === "undefined"',
        f"typeof {symbol} === 'undefined'",
        f'typeof {symbol} !== "undefined"',
        f"typeof {symbol} !== 'undefined'",
        f"if (!{symbol})",
    )
    for previous in reversed(repaired_lines):
        stripped = previous.strip()
        if re.match(r"(?:export\s+)?(?:async\s+)?function\b", stripped):
            return False
        if any(fragment in previous for fragment in guard_fragments):
            return True
    return False

def _typescript_enum_has_runtime_member_access(
    *,
    base_files: Mapping[str, str],
    type_name: str,
    declaration_path: str,
    declaration_span: tuple[int, int],
) -> bool:
    member_access = re.compile(rf"\b{re.escape(type_name)}\s*\.")
    for path, content in base_files.items():
        candidate = str(content or "")
        if path == declaration_path:
            candidate = candidate[: declaration_span[0]] + candidate[declaration_span[1] :]
        if member_access.search(candidate):
            return True
    return False

def _functions_accepting_type(*, base_files: Mapping[str, str], type_name: str) -> list[str]:
    """Return exported function names whose first parameter is ``type_name``."""

    if not _TS_IDENTIFIER_RE.fullmatch(type_name):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for content in base_files.values():
        for match in _TS_EXPORTED_FUNCTION_PARAM_TYPE_RE.finditer(str(content or "")):
            if str(match.group("type") or "") != type_name:
                continue
            name = str(match.group("name") or "")
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return names

def _pick_function_alias(*, current: str, candidates: Sequence[str]) -> str:
    """Pick the best alternative function name for a wrong-domain callee (R161)."""

    if not current or not candidates:
        return ""
    current_l = current.lower()
    # Prefer Humidity↔Hydration style near-misses and shared stems.
    scored: list[tuple[int, str]] = []
    for candidate in candidates:
        if candidate == current:
            continue
        cand_l = candidate.lower()
        score = 0
        if cand_l.replace("hydration", "humidity") == current_l.replace("hydration", "humidity"):
            score += 100
        if cand_l.replace("humidity", "hydration") == current_l.replace("humidity", "hydration"):
            score += 100
        # shared prefix length
        prefix = 0
        for left, right in zip(current_l, cand_l, strict=False):
            if left != right:
                break
            prefix += 1
        score += prefix * 2
        # length proximity
        score -= abs(len(cand_l) - len(current_l))
        scored.append((score, candidate))
    if not scored:
        return ""
    scored.sort(key=lambda item: (-item[0], item[1]))
    best_score, best = scored[0]
    return best if best_score >= 4 else ""

def _rewrite_named_import_binding_lines(
    *,
    content: str,
    old_name: str,
    new_name: str,
) -> list[tuple[int, str]]:
    """Return (line_index, repaired_line) for named-import bindings old_name → new_name.

    Keeps multi-line ``import { a, b } from '…'`` forms working so call-site
    renames do not leave TS2304 (missing import for the new callee).
    """

    if not old_name or not new_name or old_name == new_name:
        return []
    if not _TS_IDENTIFIER_RE.fullmatch(old_name) or not _TS_IDENTIFIER_RE.fullmatch(new_name):
        return []
    lines = content.splitlines(keepends=True)
    # Track whether we are inside an ``import { … }`` brace group.
    in_named_import = False
    results: list[tuple[int, str]] = []
    name_re = re.compile(rf"\b{re.escape(old_name)}\b")
    already_new_re = re.compile(rf"\b{re.escape(new_name)}\b")
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if re.match(r"import\s*(type\s*)?\{", stripped):
            in_named_import = True
        if not in_named_import:
            # Single-line: import { foo as bar, adjustHumidity } from '…'
            if re.search(r"\bimport\b", line) and "{" in line and name_re.search(line):
                if already_new_re.search(line):
                    # Already imports alias; drop old binding only.
                    repaired = re.sub(
                        rf",\s*{re.escape(old_name)}\b|\b{re.escape(old_name)}\s*,\s*|\b{re.escape(old_name)}\b",
                        lambda m: (
                            "" if m.group(0).strip() == old_name else (", " if m.group(0).startswith(",") else "")
                        ),
                        line,
                        count=1,
                    )
                else:
                    repaired = name_re.sub(new_name, line, count=1)
                if repaired != line:
                    results.append((index, repaired))
            continue
        if name_re.search(line) and not re.search(r"\bas\b", line):
            if already_new_re.search(line):
                repaired = name_re.sub("", line)
                # tidy double commas / leading commas left on the line
                repaired = re.sub(r",\s*,", ",", repaired)
                repaired = re.sub(r"\{\s*,", "{", repaired)
                repaired = re.sub(r",\s*\}", "}", repaired)
            else:
                repaired = name_re.sub(new_name, line, count=1)
            if repaired != line:
                results.append((index, repaired))
        if "}" in line:
            in_named_import = False
    return results

def _package_json_enable_node_test_script_operation(
    *,
    package_json_text: str,
    smoke_paths: Sequence[str],
) -> RepairOperation | None:
    """Point scripts.test at node --test smoke when it only re-ran build/tsc (R169)."""

    payload = _json_object(package_json_text)
    scripts = payload.get("scripts")
    if not isinstance(scripts, Mapping):
        return None
    test_script = str(scripts.get("test") or "").strip()
    if not test_script:
        return None
    if re.search(r"\b(?:vitest|jest|mocha|node\s+--test)\b", test_script):
        return None
    if not re.search(r"\b(?:npm\s+run\s+build|tsc\b|npm\s+run\s+verify)\b", test_script) and test_script not in {
        "npm run build",
        "tsc",
        "tsc -p tsconfig.json",
    }:
        # Still rewrite pure build-aliases.
        if "build" not in test_script.lower():
            return None
    smoke = next((path for path in smoke_paths if path.endswith(".ts")), "tests/verify.test.ts")
    new_scripts = dict(scripts)
    new_scripts["test"] = f"node --test {smoke}"
    if "verify" in new_scripts and re.search(
        r"\b(?:npm\s+run\s+build|tsc\b)\b",
        str(new_scripts.get("verify") or ""),
    ):
        new_scripts["verify"] = f"node --test {smoke}"
    new_payload = dict(payload)
    new_payload["scripts"] = new_scripts
    # Ensure @types/node for node:test imports when missing.
    dev = new_payload.get("devDependencies")
    if not isinstance(dev, Mapping):
        dev = {}
    else:
        dev = dict(dev)
    if "@types/node" not in {str(key) for key in dev}:
        dev["@types/node"] = "^20.11.0"
        new_payload["devDependencies"] = dev
    repaired = json.dumps(new_payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if repaired == package_json_text:
        return None
    return RepairOperation(
        kind="write_file",
        path="package.json",
        content=repaired,
        before_hash=sha256_text(package_json_text),
        metadata={
            "repair_kind": "typescript_package_test_script_smoke",
            "write_file_reason": "build_only_test_script_points_at_smoke",
            "smoke_path": smoke,
        },
    )

def _typescript_syntax_error_paths(diagnostics: Sequence[RepairDiagnostic]) -> tuple[str, ...]:
    paths: list[str] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        if diagnostic.code.lower() not in {"typescript_ts1003", "typescript_ts1005"}:
            continue
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if path and path.endswith((".ts", ".tsx")) and path not in seen:
            seen.add(path)
            paths.append(path)
    return tuple(paths)

def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return "\n"

def _normalized_base_files(base_files: Mapping[str, str]) -> dict[str, str]:
    return normalize_base_files_strict(base_files)

def _repair_plan_or_none(
    *,
    rule_id: str,
    source_tool: str,
    operations: Sequence[RepairOperation],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
    risk_level: str = "low",
    metadata: Mapping[str, object] | None = None,
) -> RepairPlan | None:
    if not operations:
        return None
    return RepairPlan(
        rule_id=rule_id,
        source_tool=source_tool,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level=risk_level,
        priority=1,
        metadata=dict(metadata or {}),
    )

def _diagnostic_text(diagnostics: Sequence[RepairDiagnostic]) -> str:
    return "\n".join(f"{diagnostic.message}\n{diagnostic.raw}" for diagnostic in diagnostics)

def _json_object(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(str(text or "{}"))
    except json.JSONDecodeError:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}

def _typescript_glob_points_outside_root(entry: str, *, root_dir: str) -> bool:
    normalized_entry = str(entry or "").strip().replace("\\", "/")
    normalized_root = _normalize_repair_path(root_dir).rstrip("/")
    if not normalized_entry or not normalized_root:
        return False
    if normalized_entry.startswith(f"{normalized_root}/") or normalized_entry == normalized_root:
        return False
    return normalized_entry.startswith(("tests/", "test/", "*."))

def _parse_html_truncated_entrypoint_paths(diagnostics: Sequence[RepairDiagnostic]) -> list[str]:
    """Return unique HTML paths with truncated/incomplete HTML diagnostics."""

    paths: list[str] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        text = f"{diagnostic.path or ''}\n{diagnostic.message or ''}\n{diagnostic.raw or ''}"
        candidates: list[str] = []
        for match in _HTML_TRUNCATED_ERROR_RE.finditer(text):
            candidates.append(str(match.group("path") or "").strip())
        lowered = text.lower()
        if "truncated/incomplete html" in lowered:
            path_hint = _normalize_repair_path(str(diagnostic.path or ""))
            if path_hint.endswith((".html", ".htm")):
                candidates.append(path_hint)
        for candidate in candidates:
            normalized = _normalize_repair_path(candidate)
            if not normalized or normalized in seen:
                continue
            if not normalized.endswith((".html", ".htm")):
                continue
            seen.add(normalized)
            paths.append(normalized)
    return paths

def _repair_html_entrypoint_quality_text_with_metadata(
    content: str,
    *,
    base_files: Mapping[str, str] | None = None,
) -> tuple[str, dict[str, object]]:
    """Close truncated HTML structure and rewrite TS module scripts to compiled JS.

    Conservative: only balances unclosed ``<script>`` tags and missing ``</html>``,
    and rewrites ``src`` ending in ``.ts``/``.tsx``. Does not invent DOM content.
    """

    original = str(content or "")
    if not original:
        return original, {}
    scripts_rewritten: list[dict[str, str]] = []
    files = dict(base_files or {})

    def _replace_src(match: re.Match[str]) -> str:
        quote = str(match.group("quote") or '"')
        source_ref = str(match.group("src") or "").strip()
        if not source_ref.endswith((".ts", ".tsx")):
            return match.group(0)
        replacement = _html_javascript_entrypoint_for_typescript_source(source_ref, base_files=files)
        if not replacement or replacement == source_ref:
            return match.group(0)
        scripts_rewritten.append({"source": source_ref, "replacement": replacement})
        return f"src={quote}{replacement}{quote}"

    repaired = _HTML_MODULE_SCRIPT_SRC_RE.sub(_replace_src, original)
    lowered = repaired.lower()
    open_scripts = len(re.findall(r"<script\b", lowered))
    close_scripts = lowered.count("</script>")
    closed_scripts = 0
    if open_scripts > close_scripts:
        closed_scripts = open_scripts - close_scripts
        repaired = repaired.rstrip() + ("\n</script>" * closed_scripts)
        lowered = repaired.lower()
    added_html_close = False
    if "<html" in lowered and "</html>" not in lowered:
        repaired = repaired.rstrip() + "\n</html>\n"
        added_html_close = True
    if repaired == original:
        return original, {}
    return repaired, {
        "closed_script_tags": closed_scripts,
        "added_html_close": added_html_close,
        "scripts": tuple(scripts_rewritten),
        "unsafe_cases_fail_closed": True,
    }

def _common_prefix_len(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index

def _html_javascript_entrypoint_for_typescript_source(
    source_ref: str,
    *,
    base_files: Mapping[str, str] | None = None,
) -> str:
    """Map a TypeScript module script src to the compiled JavaScript entrypoint.

    ``./src/web.ts`` (and ``/src/...``) map to ``./dist/web.js`` / ``dist/...`` so
    static HTML loads the tsc emit path, not a non-existent ``./src/web.js``.
    When tsconfig is present in ``base_files``, prefer the compiler outDir/rootDir.
    """

    source = str(source_ref or "").strip().replace("\\", "/")
    if not source.endswith((".ts", ".tsx")):
        return ""
    leading_dot_slash = source.startswith("./")
    normalized = source[2:] if leading_dot_slash else source.lstrip("/")
    files = dict(base_files or {})
    if files and ("tsconfig.json" in files or any(path.endswith("tsconfig.json") for path in files)):
        compiled = _html_compiled_typescript_output_path(files, normalized)
        if compiled:
            return f"./{compiled}" if leading_dot_slash else compiled
    if normalized.startswith("src/"):
        normalized = "dist/" + normalized[len("src/") :]
    js_path = re.sub(r"\.tsx?$", ".js", normalized)
    return f"./{js_path}" if leading_dot_slash else js_path

def _html_compiled_javascript_entrypoint_for_script(source_ref: str, *, base_files: Mapping[str, str]) -> str:
    source = str(source_ref or "").strip().replace("\\", "/")
    normalized = source[2:] if source.startswith("./") else source.lstrip("/")
    if not normalized.startswith("dist/") or not normalized.endswith(".js"):
        return ""
    source_stem = PurePosixPath(normalized).stem
    for candidate in (f"src/{source_stem}.ts", f"src/{source_stem}.tsx", f"{source_stem}.ts", f"{source_stem}.tsx"):
        if candidate not in base_files:
            continue
        compiled = _html_compiled_typescript_output_path(base_files, candidate)
        return f"./{compiled}" if source.startswith("./") else compiled
    return ""

def _html_compiled_typescript_output_path(base_files: Mapping[str, str], source_entry: str) -> str:
    source_path = _normalize_repair_path(source_entry)
    out_dir = _html_typescript_compiler_option(base_files, "outDir") or "dist"
    root_dir = _html_typescript_compiler_option(base_files, "rootDir")
    normalized_out = _normalize_repair_path(out_dir) or "dist"
    normalized_root = _normalize_repair_path(root_dir or "")
    relative_source = source_path
    if normalized_root and normalized_root not in {".", "./"}:
        prefix = f"{normalized_root.rstrip('/')}/"
        if source_path.startswith(prefix):
            relative_source = source_path.removeprefix(prefix)
    elif not normalized_root and source_path.startswith("src/"):
        relative_source = source_path.removeprefix("src/")
    return f"{normalized_out.rstrip('/')}/{PurePosixPath(relative_source).with_suffix('.js').as_posix()}"

def _html_typescript_compiler_option(base_files: Mapping[str, str], key: str) -> str:
    tsconfig = _json_object(str(base_files.get("tsconfig.json") or ""))
    compiler_options = tsconfig.get("compilerOptions")
    if not isinstance(compiler_options, Mapping):
        return ""
    return str(compiler_options.get(key) or "").strip().replace("\\", "/")

def _javascript_annotation_candidate_paths(
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> list[str]:
    candidates: list[str] = []
    for diagnostic in diagnostics:
        for match in _JS_RUNTIME_FILE_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            path = _normalize_repair_path(str(match.group("path") or ""))
            if path.endswith(".js") and path in base_files:
                candidates.append(path)
    if not candidates:
        candidates.extend(path for path in base_files if path.endswith(".js"))
    return _dedupe_preserve_order(candidates)

def _strip_typescript_annotations_from_javascript(text: str) -> str:
    repaired = _JS_FUNCTION_DECL_RE.sub(_strip_javascript_callable_type_match, str(text or ""))
    repaired = _JS_METHOD_DECL_RE.sub(_strip_javascript_callable_type_match, repaired)
    return _JS_VARIABLE_TYPE_RE.sub(r"\g<kind> \g<name>\g<assign>", repaired)

def _strip_javascript_callable_type_match(match: re.Match[str]) -> str:
    params = []
    for raw_param in str(match.group("params") or "").split(","):
        param = raw_param.strip()
        if not param:
            continue
        default = ""
        head = param
        if "=" in param:
            head, default_value = param.split("=", 1)
            default = " = " + default_value.strip()
        head = re.sub(r"^(?P<name>\.\.\.[A-Za-z_$][\w$]*|[A-Za-z_$][\w$]*)\s*:\s*[^=,]+$", r"\g<name>", head.strip())
        params.append(f"{head}{default}")
    return f"{match.group('prefix')}({', '.join(params)}){match.group('brace')}"

def _parse_undeclared_runtime_import_paths(
    diagnostics: Sequence[RepairDiagnostic],
    *,
    package_name: str,
) -> list[str]:
    paths: list[str] = []
    expected = str(package_name or "").split("/", 1)[0].lower()
    for diagnostic in diagnostics:
        for match in _UNDECLARED_RUNTIME_IMPORT_ERROR_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            package = str(match.group("package") or "").split("/", 1)[0].lower()
            path = _normalize_repair_path(str(match.group("path") or ""))
            if package == expected and path:
                paths.append(path)
    return _dedupe_preserve_order(paths)

def _normalize_ts_class_field_initialization(line: str) -> str:
    match = _TS_CLASS_FIELD_DECL_RE.match(line)
    if not match:
        return line
    indent = str(match.group("indent") or "")
    name = str(match.group("name") or "")
    optional = str(match.group("optional") or "")
    type_text = _normalize_typeorm_detached_field_type(str(match.group("type") or "unknown").strip())
    if optional:
        return f"{indent}{name}?: {type_text};"
    return f"{indent}{name}: {type_text} = {_typescript_default_value_for_type(type_text)};"

def _normalize_typeorm_detached_field_type(type_text: str) -> str:
    stripped = str(type_text or "unknown").strip() or "unknown"
    if re.fullmatch(r"[A-Z][A-Za-z0-9_]*\[\]", stripped):
        return "unknown[]"
    if re.fullmatch(r"[A-Z][A-Za-z0-9_]*", stripped):
        return "unknown"
    return stripped

def _parse_typescript_missing_member_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for diagnostic in diagnostics:
        for match in _TS_MISSING_PROPERTY_ERROR_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            parsed.append(
                {
                    "file": _normalize_repair_path(str(match.group("file") or "")),
                    "line": str(match.group("line") or ""),
                    "member": str(match.group("member") or ""),
                    "type": str(match.group("type") or ""),
                }
            )
    return [item for item in parsed if item["file"] and item["member"]]

def _parse_typescript_object_missing_member_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for diagnostic in diagnostics:
        raw = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_OBJECT_MISSING_PROPERTIES_ERROR_RE.finditer(raw):
            members = [
                member.strip() for member in re.split(r",|\band\b", str(match.group("members") or "")) if member.strip()
            ]
            for member in members:
                if _TS_IDENTIFIER_RE.fullmatch(member):
                    parsed.append(
                        {
                            "file": _normalize_repair_path(str(match.group("file") or "")),
                            "line": str(match.group("line") or ""),
                            "member": member,
                            "type": str(match.group("type") or ""),
                        }
                    )
        for match in _TS_OBJECT_MISSING_PROPERTY_ERROR_RE.finditer(raw):
            parsed.append(
                {
                    "file": _normalize_repair_path(str(match.group("file") or "")),
                    "line": str(match.group("line") or ""),
                    "member": str(match.group("member") or ""),
                    "type": str(match.group("type") or ""),
                }
            )
    return [item for item in parsed if item["file"] and item["member"] and item["type"]]

def _parse_typescript_unused_declaration_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for diagnostic in diagnostics:
        raw = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_UNUSED_DECLARATION_ERROR_RE.finditer(raw):
            item = {
                "file": _normalize_repair_path(str(match.group("file") or "")),
                "line": str(match.group("line") or ""),
                "column": str(match.group("col") or ""),
                "name": str(match.group("name") or ""),
            }
            key = (item["file"], item["line"], item["column"], item["name"])
            if item["file"] and item["line"] and item["name"] and key not in seen:
                seen.add(key)
                parsed.append(item)
    return [item for item in parsed if item["file"] and item["line"] and item["name"]]

def _typescript_unused_parameter_operations(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[tuple[RepairOperation, ...], list[dict[str, str]]]:
    operations: list[RepairOperation] = []
    repairs: list[dict[str, str]] = []
    items = _parse_typescript_unused_declaration_errors(diagnostics)
    named_import_operations, named_import_repairs, consumed_item_keys = (
        _typescript_unused_named_import_binding_group_operations(base_files=base_files, items=items)
    )
    operations.extend(named_import_operations)
    repairs.extend(named_import_repairs)
    for item in items:
        if _typescript_unused_declaration_item_key(item) in consumed_item_keys:
            continue
        path = item["file"]
        name = item["name"]
        content = str(base_files.get(path) or "")
        line_number = _to_positive_int(item.get("line"))
        column = _to_positive_int(item.get("column"))
        operation = _typescript_unused_import_declaration_operation(
            path=path,
            content=content,
            name=name,
            line_number=line_number,
        )
        if operation is None:
            operation = _typescript_unused_parameter_operation(
                path=path,
                content=content,
                name=name,
                line_number=line_number,
                column=column,
            )
        if operation is None:
            operation = _typescript_unused_function_declaration_operation(
                path=path,
                content=content,
                name=name,
                line_number=line_number,
            )
        if operation is None:
            operation = _typescript_unused_local_declaration_operation(
                path=path,
                content=content,
                name=name,
                line_number=line_number,
            )
        if operation is None:
            continue
        operations.append(operation)
        repairs.append({"file": path, "parameter": name, "replacement": str(operation.replacement or "")})
    return tuple(operations), repairs

def _typescript_unused_declaration_item_key(item: Mapping[str, str]) -> tuple[str, str, str, str]:
    return (
        str(item.get("file") or ""),
        str(item.get("line") or ""),
        str(item.get("column") or ""),
        str(item.get("name") or ""),
    )

def _typescript_unused_named_import_binding_group_operations(
    *,
    base_files: Mapping[str, str],
    items: Sequence[Mapping[str, str]],
) -> tuple[tuple[RepairOperation, ...], list[dict[str, str]], set[tuple[str, str, str, str]]]:
    grouped: dict[tuple[str, int, int], dict[str, object]] = {}
    consumed_item_keys: set[tuple[str, str, str, str]] = set()
    for item in items:
        path = str(item.get("file") or "")
        name = str(item.get("name") or "")
        line_number = _to_positive_int(item.get("line"))
        content = str(base_files.get(path) or "")
        if not path or not content or line_number <= 0 or not _TS_IDENTIFIER_RE.fullmatch(name):
            continue
        for match in _TS_REEXPORTABLE_NAMED_IMPORT_RE.finditer(content):
            start_line = content.count("\n", 0, match.start()) + 1
            end_line = content.count("\n", 0, match.end()) + 1
            if line_number < start_line or line_number > end_line:
                continue
            pairs = _typescript_import_pairs_from_clause("{" + str(match.group("names") or "") + "}")
            if len(pairs) <= 1 or not any(local == name for _, local in pairs):
                continue
            group_key = (path, match.start(), match.end())
            group = grouped.setdefault(
                group_key,
                {
                    "content": content,
                    "import_text": content[match.start() : match.end()],
                    "module_specifier": str(match.group("module") or ""),
                    "names": set(),
                    "lines": [],
                },
            )
            names = group["names"]
            lines = group["lines"]
            if isinstance(names, set):
                names.add(name)
            if isinstance(lines, list):
                lines.append(line_number)
            consumed_item_keys.add(_typescript_unused_declaration_item_key(item))
            break

    operations: list[RepairOperation] = []
    repairs: list[dict[str, str]] = []
    for (path, start, end), group in sorted(grouped.items()):
        content = str(group.get("content") or "")
        import_text = str(group.get("import_text") or "")
        raw_names = group.get("names", set())
        raw_lines = group.get("lines", [])
        names = {str(name) for name in raw_names if str(name)} if isinstance(raw_names, set) else set()
        diagnostic_lines = (
            [int(line) for line in raw_lines if isinstance(line, int) and int(line) > 0]
            if isinstance(raw_lines, list)
            else []
        )
        if not content or not import_text or not names:
            continue
        replacement = _remove_typescript_named_import_bindings(import_text=import_text, names=names)
        if replacement == import_text:
            continue
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=start,
                span_end=end,
                expected=import_text,
                replacement=replacement,
                before_hash=sha256_text(content),
                metadata={
                    "repair_kind": "typescript_unused_import_specifier",
                    "compiler_reported_unused_binding": True,
                    "bindings": tuple(sorted(names)),
                    "module_specifier": str(group.get("module_specifier") or ""),
                    "diagnostic_lines": tuple(diagnostic_lines),
                },
            )
        )
        repairs.extend({"file": path, "parameter": name, "replacement": replacement} for name in sorted(names))
    return tuple(operations), repairs, consumed_item_keys

def _typescript_unused_import_declaration_operation(
    *,
    path: str,
    content: str,
    name: str,
    line_number: int,
) -> RepairOperation | None:
    if not path or not content or line_number <= 0 or not _TS_IDENTIFIER_RE.fullmatch(name):
        return None
    operation = _typescript_unused_named_import_binding_operation(
        path=path,
        content=content,
        name=name,
        line_number=line_number,
    )
    if operation is not None:
        return operation
    for match in _TS_IMPORT_FROM_ANY_RE.finditer(content):
        start_line = content.count("\n", 0, match.start()) + 1
        end_line = content.count("\n", 0, match.end()) + 1
        if line_number < start_line or line_number > end_line:
            continue
        pairs = _typescript_import_pairs_from_clause(str(match.group("clause") or ""))
        if len(pairs) != 1 or pairs[0][1] != name:
            continue
        start, end = match.span()
        if end < len(content) and content[end : end + 1] == "\n":
            end += 1
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=start,
            span_end=end,
            expected=content[start:end],
            replacement="",
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_unused_import",
                "compiler_reported_unused_binding": True,
                "binding": name,
                "module_specifier": str(match.group("specifier") or ""),
                "diagnostic_line": line_number,
            },
        )
    return None

def _typescript_unused_named_import_binding_operation(
    *,
    path: str,
    content: str,
    name: str,
    line_number: int,
) -> RepairOperation | None:
    for match in _TS_REEXPORTABLE_NAMED_IMPORT_RE.finditer(content):
        start_line = content.count("\n", 0, match.start()) + 1
        end_line = content.count("\n", 0, match.end()) + 1
        if line_number < start_line or line_number > end_line:
            continue
        import_text = content[match.start() : match.end()]
        pairs = _typescript_import_pairs_from_clause("{" + str(match.group("names") or "") + "}")
        if len(pairs) <= 1 or not any(local == name for _, local in pairs):
            continue
        replacement = _remove_typescript_named_import_binding(import_text=import_text, name=name)
        if not replacement or replacement == import_text:
            continue
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=match.start(),
            span_end=match.end(),
            expected=import_text,
            replacement=replacement,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_unused_import_specifier",
                "compiler_reported_unused_binding": True,
                "binding": name,
                "module_specifier": str(match.group("module") or ""),
                "diagnostic_line": line_number,
            },
        )
    return None

def _typescript_line_is_import_binding_context(*, content: str, line: int, name: str) -> bool:
    """Return True when ``name`` on ``line`` is an import/type import binding."""

    if line <= 0 or not name:
        return False
    for match in _TS_REEXPORTABLE_NAMED_IMPORT_RE.finditer(content):
        start_line = content.count("\n", 0, match.start()) + 1
        end_line = content.count("\n", 0, match.end()) + 1
        if line < start_line or line > end_line:
            continue
        pairs = _typescript_import_pairs_from_clause("{" + str(match.group("names") or "") + "}")
        if any(local == name for _, local in pairs):
            return True
    for match in _TS_IMPORT_FROM_ANY_RE.finditer(content):
        start_line = content.count("\n", 0, match.start()) + 1
        end_line = content.count("\n", 0, match.end()) + 1
        if line < start_line or line > end_line:
            continue
        pairs = _typescript_import_pairs_from_clause(str(match.group("clause") or ""))
        if any(local == name for _, local in pairs):
            return True
    lines = content.splitlines()
    if 0 < line <= len(lines):
        candidate = lines[line - 1]
        if re.search(r"\bimport\b", candidate) or re.search(
            rf"^\s*(?:type\s+)?{re.escape(name)}\b",
            candidate,
        ):
            # Heuristic: multi-line import body lines often lack the word import.
            window = "\n".join(lines[max(0, line - 8) : min(len(lines), line + 3)])
            if re.search(r"\bimport\b[\s\S]{0,400}\bfrom\b", window):
                return True
    return False

def _remove_typescript_named_import_binding(*, import_text: str, name: str) -> str:
    return _remove_typescript_named_import_bindings(import_text=import_text, names={name})

def _remove_typescript_named_import_bindings(*, import_text: str, names: set[str]) -> str:
    normalized_names = {name for name in names if _TS_IDENTIFIER_RE.fullmatch(name)}
    if not normalized_names:
        return import_text
    if "\n" in import_text:
        replacement = _remove_typescript_multiline_named_import_bindings(
            import_text=import_text,
            names=normalized_names,
        )
        if replacement != import_text:
            return replacement
    match = _TS_REEXPORTABLE_NAMED_IMPORT_RE.fullmatch(import_text)
    if match is None:
        return import_text
    names_clause = str(match.group("names") or "")
    kept_parts: list[str] = []
    removed = False
    for raw_part in names_clause.split(","):
        part = raw_part.strip()
        if not part:
            continue
        local = _typescript_named_import_local_name(part)
        if local in normalized_names:
            removed = True
            continue
        kept_parts.append(part)
    if not removed:
        return import_text
    if not kept_parts:
        return ""
    return (
        f"{match.group('indent') or ''}import "
        f"{match.group('type_only') or ''}{{ {', '.join(kept_parts)} }} "
        f"from {match.group('quote')}{match.group('module')}{match.group('quote')};"
    )

def _remove_typescript_multiline_named_import_bindings(*, import_text: str, names: set[str]) -> str:
    lines = import_text.splitlines(keepends=True)
    kept_lines: list[str] = []
    removed = False
    for line in lines:
        line_body = line.rstrip("\r\n")
        part = line_body.strip().rstrip(",").strip()
        if not part or _typescript_named_import_local_name(part) not in names:
            kept_lines.append(line)
            continue
        removed = True
    if not removed:
        return import_text
    remaining_names = [
        _typescript_named_import_local_name(line.strip().rstrip(",").strip())
        for line in kept_lines
        if _typescript_named_import_local_name(line.strip().rstrip(",").strip())
    ]
    if not remaining_names:
        return ""
    return "".join(kept_lines)

def _typescript_named_import_local_name(part: str) -> str:
    normalized = str(part or "").strip().rstrip(",").strip()
    if normalized.startswith("type "):
        normalized = normalized[5:].strip()
    alias_parts = re.split(r"\s+as\s+", normalized, maxsplit=1, flags=re.IGNORECASE)
    local = alias_parts[-1].strip()
    return local if _TS_IDENTIFIER_RE.fullmatch(local) else ""

def _typescript_unused_local_declaration_operation(
    *,
    path: str,
    content: str,
    name: str,
    line_number: int,
) -> RepairOperation | None:
    if not path or not content or line_number <= 0 or not _TS_IDENTIFIER_RE.fullmatch(name):
        return None
    lines = content.splitlines(keepends=True)
    line_index = line_number - 1
    if line_index < 0 or line_index >= len(lines):
        return None
    original_line = lines[line_index]
    line_body = original_line.rstrip("\r\n")
    newline = original_line[len(line_body) :]
    match = _TS_UNUSED_LOCAL_DECLARATION_LINE_RE.match(line_body)
    if match is None or str(match.group("name") or "") != name:
        return None
    expression = str(match.group("expr") or "").strip()
    if not expression or _typescript_unused_local_expression_requires_binding(expression):
        return None
    replacement = f"{match.group('indent') or ''!s}{expression};{newline}"
    if replacement == original_line:
        return None
    return _line_text_replace_operation(
        path=path,
        content=content,
        line_index=line_index,
        replacement=replacement,
        metadata={
            "repair_kind": "typescript_unused_local_declaration",
            "binding": name,
            "diagnostic_line": line_number,
            "replacement_strategy": "initializer_expression_statement",
        },
    )

def _typescript_unused_function_declaration_operation(
    *,
    path: str,
    content: str,
    name: str,
    line_number: int,
) -> RepairOperation | None:
    if not path or not content or line_number <= 0 or not _TS_IDENTIFIER_RE.fullmatch(name):
        return None
    lines = content.splitlines(keepends=True)
    line_index = line_number - 1
    if line_index < 0 or line_index >= len(lines):
        return None
    first_line = lines[line_index].rstrip("\r\n")
    if first_line.lstrip().startswith("export "):
        return None
    match = _TS_UNUSED_FUNCTION_DECLARATION_LINE_RE.match(first_line)
    if match is None or str(match.group("name") or "") != name:
        return None
    offsets = _line_start_offsets(lines)
    brace_depth = 0
    saw_open_brace = False
    end_line_index = -1
    for current_index in range(line_index, len(lines)):
        line = lines[current_index]
        if "{" in line:
            saw_open_brace = True
        brace_depth += line.count("{") - line.count("}")
        if saw_open_brace and brace_depth <= 0:
            end_line_index = current_index
            break
    if not saw_open_brace or end_line_index < line_index:
        return None
    span_start = offsets[line_index]
    span_end = offsets[end_line_index + 1]
    expected = content[span_start:span_end]
    if not expected.strip():
        return None
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=span_start,
        span_end=span_end,
        expected=expected,
        replacement="",
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_unused_function_declaration",
            "binding": name,
            "diagnostic_line": line_number,
            "replacement_strategy": "delete_non_exported_function_declaration",
        },
    )

def _typescript_unused_local_expression_requires_binding(expression: str) -> bool:
    stripped = str(expression or "").lstrip()
    if not stripped:
        return True
    if stripped.startswith(("{", "function ", "class ", "interface ", "type ")):
        return True
    return "=>" in stripped

def _typescript_unused_parameter_operation(
    *,
    path: str,
    content: str,
    name: str,
    line_number: int,
    column: int,
) -> RepairOperation | None:
    if not path or not content or line_number <= 0 or not _TS_IDENTIFIER_RE.fullmatch(name):
        return None
    lines = content.splitlines(keepends=True)
    line_index = line_number - 1
    candidate_indexes: list[int] = []
    if 0 <= line_index < len(lines):
        candidate_indexes.append(line_index)
    candidate_indexes.extend(index for index in range(len(lines)) if index not in candidate_indexes)
    for candidate_index in candidate_indexes:
        original_line = lines[candidate_index]
        repaired_line = _typescript_unused_parameter_line_replacement(
            line=original_line,
            name=name,
            column=column if candidate_index == line_index else 0,
        )
        if not repaired_line:
            repaired_line = _typescript_unused_multiline_parameter_line_replacement(
                lines=lines,
                line_index=candidate_index,
                name=name,
                column=column if candidate_index == line_index else 0,
            )
        if not repaired_line or repaired_line == original_line:
            continue
        return _line_text_replace_operation(
            path=path,
            content=content,
            line_index=candidate_index,
            replacement=repaired_line,
            metadata={
                "repair_kind": "typescript_unused_parameter",
                "parameter": name,
                "replacement": f"_{name}",
                "diagnostic_line": line_number,
                "matched_line": candidate_index + 1,
            },
        )
    return None

def _typescript_unused_parameter_line_replacement(*, line: str, name: str, column: int) -> str:
    if name.startswith("_") or f"_{name}" in line:
        return ""
    occurrences = list(re.finditer(rf"\b{re.escape(name)}\b", line))
    if not occurrences:
        return ""
    column_index = max(0, column - 1)
    occurrences.sort(key=lambda match: abs(match.start() - column_index))
    for match in occurrences:
        if not _typescript_identifier_occurrence_is_parameter(line, match.start(), match.end()):
            continue
        return f"{line[: match.start()]}_{name}{line[match.end() :]}"
    return ""

def _typescript_unused_multiline_parameter_line_replacement(
    *,
    lines: Sequence[str],
    line_index: int,
    name: str,
    column: int,
) -> str:
    if name.startswith("_") or line_index < 0 or line_index >= len(lines):
        return ""
    line = lines[line_index]
    if f"_{name}" in line:
        return ""
    occurrences = list(re.finditer(rf"\b{re.escape(name)}\b", line))
    if not occurrences:
        return ""
    column_index = max(0, column - 1)
    occurrences.sort(key=lambda match: abs(match.start() - column_index))
    for match in occurrences:
        if not _typescript_identifier_occurrence_has_parameter_shape(line, match.start(), match.end()):
            continue
        if not _typescript_identifier_occurrence_is_in_multiline_parameter_list(
            lines=lines,
            line_index=line_index,
            start=match.start(),
            end=match.end(),
        ):
            continue
        return f"{line[: match.start()]}_{name}{line[match.end() :]}"
    return ""

def _typescript_identifier_occurrence_is_parameter(line: str, start: int, end: int) -> bool:
    open_index = line.rfind("(", 0, start)
    close_index = line.find(")", end)
    if open_index < 0 or close_index < 0:
        return False
    segment_before = line[open_index + 1 : start]
    segment_after = line[end:close_index]
    if "{" in segment_before or "}" in segment_before:
        return False
    before_token = segment_before.rsplit(",", 1)[-1].strip()
    if before_token:
        return False
    tail = segment_after.lstrip()
    return not tail or tail.startswith((":", "?", "=", ","))

def _typescript_identifier_occurrence_has_parameter_shape(line: str, start: int, end: int) -> bool:
    before = line[:start].strip()
    if before:
        modifier_tokens = before.split()
        allowed_modifiers = {"public", "private", "protected", "readonly", "override"}
        if any(token not in allowed_modifiers for token in modifier_tokens):
            return False
    tail = line[end:].lstrip()
    return not tail or tail.startswith((":", "?", "=", ","))

def _typescript_identifier_occurrence_is_in_multiline_parameter_list(
    *,
    lines: Sequence[str],
    line_index: int,
    start: int,
    end: int,
) -> bool:
    window_start = max(0, line_index - 20)
    window_end = min(len(lines), line_index + 21)
    before = "".join(lines[window_start:line_index]) + lines[line_index][:start]
    after = lines[line_index][end:] + "".join(lines[line_index + 1 : window_end])
    open_index = before.rfind("(")
    if open_index < 0:
        return False
    segment_since_open = before[open_index + 1 :]
    if ")" in segment_since_open or ";" in segment_since_open:
        return False
    close_index = after.find(")")
    return close_index >= 0

def _typescript_object_literal_missing_member_operations(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[RepairOperation, ...]:
    grouped: dict[tuple[str, str], dict[str, int]] = {}
    for item in _parse_typescript_object_missing_member_errors(diagnostics):
        type_name = _typescript_declaration_type_name(item["type"])
        if not type_name:
            continue
        key = (item["file"], type_name)
        grouped.setdefault(key, {})[item["member"]] = _to_positive_int(item.get("line"))

    operations: list[RepairOperation] = []
    for (path, type_name), member_lines in sorted(grouped.items()):
        content = str(base_files.get(path) or "")
        if not content:
            continue
        method_specs = _typescript_method_delegate_specs(
            content=content,
            type_name=type_name,
            members=tuple(member_lines),
        )
        if method_specs:
            operations.extend(
                _typescript_interface_method_return_operations(
                    path=path,
                    content=content,
                    type_name=type_name,
                    method_specs=method_specs,
                )
            )
            object_operation = _typescript_object_literal_method_implementation_operation(
                path=path,
                content=content,
                type_name=type_name,
                member_lines=member_lines,
                method_specs=method_specs,
            )
            if object_operation is not None:
                operations.append(object_operation)
        property_operation = _typescript_object_literal_required_properties_operation(
            base_files=base_files,
            path=path,
            content=content,
            type_name=type_name,
            member_lines=member_lines,
        )
        if property_operation is not None:
            operations.append(property_operation)
    return tuple(operations)

def _typescript_declaration_type_name(raw: str) -> str:
    match = re.search(r"[A-Za-z_$][A-Za-z0-9_$]*", str(raw or ""))
    return str(match.group(0) if match else "")

def _typescript_inline_object_missing_member_operations(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[tuple[RepairOperation, ...], list[dict[str, str]]]:
    operations: list[RepairOperation] = []
    members: list[dict[str, str]] = []
    for item in _parse_typescript_missing_member_errors(diagnostics):
        member = item["member"]
        shape_members = _typescript_inline_object_shape_members(item["type"])
        if not shape_members or member in shape_members or not _TS_IDENTIFIER_RE.fullmatch(member):
            continue
        usage_path = item["file"]
        usage_text = str(base_files.get(usage_path) or "")
        line_number = _to_positive_int(item.get("line"))
        declared_type = _typescript_missing_member_declared_type(
            usage_text,
            line_number,
            member,
            member_is_call=False,
        )
        if not _typescript_safe_structural_member_type(declared_type):
            continue
        type_operation = _typescript_inline_object_type_member_operation(
            base_files=base_files,
            shape_members=shape_members,
            member=member,
            declared_type=declared_type,
        )
        if type_operation is None:
            continue
        operations.append(type_operation)
        members.append({"file": type_operation.path, "type": "inline_object", "member": member})
        literal_operation = _typescript_inline_object_literal_member_operation(
            base_files=base_files,
            shape_members=shape_members,
            member=member,
            declared_type=declared_type,
        )
        if literal_operation is not None:
            operations.append(literal_operation)
    return tuple(operations), members

def _typescript_inline_object_shape_members(raw_type: str) -> dict[str, str]:
    text = str(raw_type or "").strip()
    if not text.startswith("{") or not text.endswith("}"):
        return {}
    members: dict[str, str] = {}
    body = text[1:-1]
    for segment in re.split(r";|,", body):
        cleaned = re.sub(r"^\s*readonly\s+", "", segment.strip())
        match = re.match(r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\??\s*:\s*(?P<type>.+)$", cleaned)
        if not match:
            continue
        name = str(match.group("name") or "")
        ts_type = str(match.group("type") or "").strip()
        if name and ts_type:
            members[name] = ts_type
    return members

def _typescript_inline_object_type_member_operation(
    *,
    base_files: Mapping[str, str],
    shape_members: Mapping[str, str],
    member: str,
    declared_type: str,
) -> RepairOperation | None:
    for path, content in base_files.items():
        for open_brace in _typescript_inline_array_object_type_braces(str(content or "")):
            close_brace = _typescript_matching_brace_index(str(content or ""), open_brace)
            if close_brace < 0:
                continue
            body = str(content or "")[open_brace + 1 : close_brace]
            existing_members = _typescript_inline_object_shape_members(f"{{{body}}}")
            if member in existing_members or not set(shape_members).issubset(existing_members):
                continue
            operation = _typescript_insert_object_member_operation(
                path=path,
                content=str(content or ""),
                close_brace=close_brace,
                member=member,
                value=f"{declared_type};",
                readonly=True,
                repair_kind="typescript_inline_object_type_missing_member",
            )
            if operation is not None:
                return operation
    return None

def _typescript_inline_array_object_type_braces(content: str) -> tuple[int, ...]:
    starts: list[int] = []
    for match in re.finditer(r"\b(?:ReadonlyArray|Array)\s*<\s*\{", str(content or "")):
        open_brace = str(content or "").rfind("{", 0, match.end())
        if open_brace >= 0:
            close_brace = _typescript_matching_brace_index(str(content or ""), open_brace)
            if close_brace >= 0 and str(content or "")[close_brace + 1 :].lstrip().startswith(">"):
                starts.append(open_brace)
    return tuple(starts)

def _typescript_inline_object_literal_member_operation(
    *,
    base_files: Mapping[str, str],
    shape_members: Mapping[str, str],
    member: str,
    declared_type: str,
) -> RepairOperation | None:
    default_value = _typescript_default_value_for_required_property_type(declared_type)
    if not default_value:
        return None
    for path, content in base_files.items():
        text = str(content or "")
        for open_brace in (match.start() for match in re.finditer(r"\{", text)):
            close_brace = _typescript_matching_brace_index(text, open_brace)
            if close_brace < 0:
                continue
            body = text[open_brace + 1 : close_brace]
            if len(body) > 600:
                continue
            object_members = _typescript_object_literal_member_names(body)
            if member in object_members or not set(shape_members).issubset(object_members):
                continue
            operation = _typescript_insert_object_member_operation(
                path=path,
                content=text,
                close_brace=close_brace,
                member=member,
                value=f"{default_value},",
                readonly=False,
                repair_kind="typescript_inline_object_literal_missing_member",
            )
            if operation is not None:
                return operation
    return None

def _typescript_object_literal_member_names(body: str) -> set[str]:
    names: set[str] = set()
    depth = 0
    for line in str(body or "").splitlines():
        if depth == 0 and (match := re.match(r"\s*(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*(?::|,|$)", line)):
            names.add(str(match.group("name") or ""))
        depth += line.count("{") - line.count("}")
        if depth < 0:
            depth = 0
    return names

def _typescript_insert_object_member_operation(
    *,
    path: str,
    content: str,
    close_brace: int,
    member: str,
    value: str,
    readonly: bool,
    repair_kind: str,
) -> RepairOperation | None:
    close_line_start = content.rfind("\n", 0, close_brace) + 1
    if close_line_start <= 0:
        return None
    close_indent_match = re.match(r"\s*", content[close_line_start:close_brace])
    close_indent = close_indent_match.group(0) if close_indent_match else ""
    body = content[content.rfind("{", 0, close_brace) + 1 : close_brace]
    member_indent = _typescript_member_insert_indent(body, fallback=f"{close_indent}  ")
    prefix = "readonly " if readonly else ""
    declaration = f"{member_indent}{prefix}{member}: {value}\n"
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=close_line_start,
        span_end=close_line_start,
        expected="",
        replacement=declaration,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": repair_kind,
            "member": member,
            "declared_type_or_value": value,
            "expected_context_before": content[max(0, close_line_start - 240) : close_line_start],
            "expected_context_after": content[close_line_start : close_line_start + 80],
        },
    )

def _typescript_member_insert_indent(body: str, *, fallback: str) -> str:
    for line in reversed(str(body or "").splitlines()):
        if not line.strip():
            continue
        indent_match = re.match(r"\s*", line)
        return indent_match.group(0) if indent_match else fallback
    return fallback

def _typescript_receiver_for_member_access(line: str, member: str) -> str:
    match = re.search(rf"\b(?P<receiver>[A-Za-z_$][\w$]*!?)\s*\.\s*{re.escape(member)}\b", str(line or ""))
    return str(match.group("receiver") if match else "")

def _typescript_type_declaration_body(content: str, type_name: str) -> str:
    """Return the brace-balanced body of a class/interface declaration.

    R160: naive non-greedy ``}`` matching stopped at the first method body close,
    so getters like ``get position()`` and methods like ``currentGlow()`` never
    entered the existing-member set and member-alias rewrites failed closed.
    """

    if not _TS_IDENTIFIER_RE.fullmatch(type_name):
        return ""
    escaped = re.escape(type_name)
    header = re.search(
        rf"(?:export\s+)?(?:interface|class)\s+{escaped}\b[^{{]*{{",
        str(content or ""),
    )
    if header is None:
        return ""
    depth = 0
    start = header.end()
    text = str(content or "")
    for index in range(header.end() - 1, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index]
    return ""

def _typescript_existing_member_names_for_type(
    *,
    base_files: Mapping[str, str],
    type_name: str,
) -> set[str]:
    members: set[str] = set()
    for text in base_files.values():
        body = _typescript_type_declaration_body(str(text or ""), type_name)
        if not body:
            continue
        # Only direct class/interface members (indent 1-4). Nested object keys inside
        # methods (e.g. ``return { glow: ... }``) must not pollute the member set —
        # R160: Firefly class has currentGlow() but not glow; nested return keys
        # previously made alias rewrites skip incorrectly.
        member_line = re.compile(
            r"^(?P<indent>[ \t]{1,4})(?:(?:public|private|protected|readonly|static|abstract|async|override)\s+)*"
            r"(?:"
            r"(?:get|set)\s+(?P<accessor>[A-Za-z_$][\w$]*)\s*\("  # get position()
            r"|"
            r"(?P<name>[A-Za-z_$][\w$]*)\s*(?:\??\s*[:=(])"  # field / method / optional
            r")",
            re.MULTILINE,
        )
        for member_match in member_line.finditer(body):
            name = str(member_match.group("accessor") or member_match.group("name") or "")
            if name and name not in {
                "if",
                "for",
                "while",
                "switch",
                "try",
                "catch",
                "return",
                "const",
                "let",
                "var",
                "get",
                "set",
                "constructor",
            }:
                members.add(name)
    return members

def _typescript_method_delegate_specs(
    *,
    content: str,
    type_name: str,
    members: Sequence[str],
) -> dict[str, dict[str, str]]:
    specs: dict[str, dict[str, str]] = {}
    escaped_type = re.escape(type_name)
    for member in members:
        if not _TS_IDENTIFIER_RE.fullmatch(member):
            continue
        match = re.search(
            rf"(?m)^export\s+function\s+"
            rf"(?P<delegate>{re.escape(member)}[A-Za-z0-9_$]*)\s*"
            rf"\(\s*(?P<self>[A-Za-z_$][\w$]*)\s*:\s*{escaped_type}\s*,\s*(?P<params>[^)]*)\)\s*:\s*{escaped_type}\s*{{",
            content,
        )
        if not match:
            continue
        params = _typescript_normalized_parameter_list(str(match.group("params") or ""))
        if not params:
            continue
        specs[member] = {
            "delegate": str(match.group("delegate") or ""),
            "params": params,
            "args": _typescript_parameter_argument_list(params),
        }
    return specs

def _typescript_normalized_parameter_list(params: str) -> str:
    normalized: list[str] = []
    for raw in str(params or "").split(","):
        part = raw.strip()
        if not part:
            continue
        if "=" in part:
            part = part.split("=", 1)[0].strip()
        if not re.match(r"^[A-Za-z_$][\w$]*\??\s*:", part):
            return ""
        normalized.append(part)
    return ", ".join(normalized)

def _typescript_parameter_argument_list(params: str) -> str:
    args: list[str] = []
    for raw in str(params or "").split(","):
        name = raw.strip().split(":", 1)[0].strip().rstrip("?")
        if not _TS_IDENTIFIER_RE.fullmatch(name):
            return ""
        args.append(name)
    return ", ".join(args)

def _typescript_interface_method_return_operations(
    *,
    path: str,
    content: str,
    type_name: str,
    method_specs: Mapping[str, Mapping[str, str]],
) -> tuple[RepairOperation, ...]:
    operations: list[RepairOperation] = []
    escaped_type = re.escape(type_name)
    interface_match = re.search(rf"(?m)^export\s+interface\s+{escaped_type}\b[^\n]*{{", content)
    if not interface_match:
        return ()
    interface_end = content.find("\n}", interface_match.end())
    if interface_end < 0:
        return ()
    interface_body = content[interface_match.end() : interface_end]
    body_start = interface_match.end()
    for member, spec in method_specs.items():
        member_match = re.search(
            rf"(?m)^(?P<indent>\s*){re.escape(member)}\s*\([^;\n]*\)\s*:\s*unknown\s*;",
            interface_body,
        )
        if not member_match:
            continue
        indent = str(member_match.group("indent") or "  ")
        replacement = f"{indent}{member}({spec['params']}): {type_name};"
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=body_start + member_match.start(),
                span_end=body_start + member_match.end(),
                expected=str(member_match.group(0) or ""),
                replacement=replacement,
                before_hash=sha256_text(content),
                metadata={
                    "repair_kind": "typescript_interface_method_return",
                    "type": type_name,
                    "member": member,
                    "delegate": str(spec.get("delegate") or ""),
                },
            )
        )
    return tuple(operations)

def _typescript_object_literal_method_implementation_operation(
    *,
    path: str,
    content: str,
    type_name: str,
    member_lines: Mapping[str, int],
    method_specs: Mapping[str, Mapping[str, str]],
) -> RepairOperation | None:
    line_number = max((line for line in member_lines.values() if line > 0), default=0)
    line_offsets = _text_line_start_offsets(content)
    search_end = line_offsets[line_number - 1] if 0 < line_number <= len(line_offsets) else len(content)
    return_match = None
    for match in re.finditer(r"return\s+Object\.freeze\s*\(\s*{", content[:search_end]):
        return_match = match
    if return_match is None:
        for match in re.finditer(r"return\s+Object\.freeze\s*\(\s*{", content):
            if match.start() >= search_end:
                return_match = match
                break
    if return_match is None:
        return None
    close_match = re.search(r"(?m)^(?P<indent>\s*)}\s*\)\s*;", content[return_match.end() :])
    if close_match is None:
        return None
    span_start = return_match.end() + close_match.start()
    object_body = content[return_match.end() : span_start]
    declarations: list[str] = []
    close_indent = str(close_match.group("indent") or "")
    entry_indent = f"{close_indent}  "
    body_indent = f"{entry_indent}  "
    for member, spec in method_specs.items():
        if re.search(rf"(?m)^\s*{re.escape(member)}\s*[:(]", object_body):
            continue
        args = str(spec.get("args") or "")
        delegate = str(spec.get("delegate") or "")
        params = str(spec.get("params") or "")
        if not args or not delegate:
            continue
        declarations.append(
            f"{entry_indent}{member}({params}): {type_name} {{\n"
            f"{body_indent}return {delegate}(this, {args});\n"
            f"{entry_indent}}},\n"
        )
    if not declarations:
        return None
    context_start = max(return_match.start(), span_start - 240)
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=span_start,
        span_end=span_start,
        expected="",
        replacement="".join(declarations),
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_object_literal_missing_member_implementation",
            "type": type_name,
            "members": tuple(method_specs),
            "expected_context_before": content[context_start:span_start],
            "expected_context_after": content[span_start : span_start + 8],
        },
    )

def _typescript_object_literal_required_properties_operation(
    *,
    base_files: Mapping[str, str],
    path: str,
    content: str,
    type_name: str,
    member_lines: Mapping[str, int],
) -> RepairOperation | None:
    member_types = _typescript_declared_member_types_for_type(base_files=base_files, type_name=type_name)
    if not member_types:
        return None
    object_bounds = _typescript_object_literal_bounds_near_line(
        content=content,
        line_number=max((line for line in member_lines.values() if line > 0), default=0),
    )
    if object_bounds is None:
        return None
    body_start, body_end, close_indent = object_bounds
    object_body = content[body_start:body_end]
    entry_indent = f"{close_indent}  "
    declarations: list[str] = []
    repaired_members: list[str] = []
    for member in sorted(member_lines):
        if not _TS_IDENTIFIER_RE.fullmatch(member):
            continue
        if re.search(rf"(?m)^\s*{re.escape(member)}\s*:", object_body):
            continue
        default_value = _typescript_default_value_for_required_property_type(member_types.get(member, ""))
        if not default_value:
            continue
        declarations.append(f"{entry_indent}{member}: {default_value},\n")
        repaired_members.append(member)
    if not declarations:
        return None
    context_start = max(0, body_end - 240)
    span_end = body_end + len(close_indent)
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=body_end,
        span_end=span_end,
        expected=close_indent,
        replacement="".join(declarations) + close_indent,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_object_literal_required_properties",
            "type": type_name,
            "members": tuple(repaired_members),
            "expected_context_before": content[context_start:body_end],
            "expected_context_after": content[span_end : span_end + 8],
        },
    )

def _typescript_declared_member_types_for_type(*, base_files: Mapping[str, str], type_name: str) -> dict[str, str]:
    if not _TS_IDENTIFIER_RE.fullmatch(type_name):
        return {}
    escaped = re.escape(type_name)
    member_types: dict[str, str] = {}
    for content in base_files.values():
        declaration_match = re.search(
            rf"(?m)^(?:export\s+)?(?:interface|class)\s+{escaped}\b[^\n]*{{",
            str(content or ""),
        )
        if not declaration_match:
            continue
        declaration_end = str(content or "").find("\n}", declaration_match.end())
        if declaration_end < 0:
            continue
        body = str(content or "")[declaration_match.end() : declaration_end]
        for member_match in re.finditer(
            r"(?m)^\s*(?:(?:public|private|protected|readonly|static|abstract)\s+)*"
            r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*:\s*(?P<type>[^;=\n]+)\s*(?:;|=)",
            body,
        ):
            name = str(member_match.group("name") or "")
            ts_type = str(member_match.group("type") or "").strip()
            if _TS_IDENTIFIER_RE.fullmatch(name) and ts_type:
                member_types[name] = ts_type
    return member_types

def _typescript_object_literal_bounds_near_line(
    *,
    content: str,
    line_number: int,
) -> tuple[int, int, str] | None:
    text = str(content or "")
    line_offsets = _text_line_start_offsets(text)
    search_start = line_offsets[line_number - 1] if 0 < line_number <= len(line_offsets) else 0
    candidates: list[re.Match[str]] = []
    for match in re.finditer(r"\breturn\s+(?:Object\.freeze\s*\(\s*)?{", text):
        if match.start() <= search_start + 240:
            candidates.append(match)
    for match in re.finditer(r"=\s*{", text):
        if abs(match.start() - search_start) <= 240:
            candidates.append(match)
    for match in sorted(candidates, key=lambda item: abs(item.start() - search_start)):
        open_brace = text.find("{", match.start(), match.end())
        if open_brace < 0:
            continue
        close_brace = _typescript_matching_brace_index(text, open_brace)
        if close_brace <= open_brace:
            continue
        close_line_start = text.rfind("\n", 0, close_brace) + 1
        close_indent = text[close_line_start:close_brace]
        if close_indent.strip():
            close_indent = ""
        return (open_brace + 1, close_line_start, close_indent)
    return None

def _typescript_matching_brace_index(text: str, open_brace: int) -> int:
    if open_brace < 0 or open_brace >= len(text) or text[open_brace] != "{":
        return -1
    depth = 0
    quote = ""
    escaped = False
    for index in range(open_brace, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"', "`"}:
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1

def _typescript_line_invokes_constructor(line: str, class_name: str) -> bool:
    if not _TS_IDENTIFIER_RE.fullmatch(class_name):
        return False
    return bool(re.search(rf"\bnew\s+{re.escape(class_name)}\s*\(", str(line or "")))

def _typescript_exported_private_constructor_modifier_span(text: str, class_name: str) -> tuple[int, int, int] | None:
    if not _TS_IDENTIFIER_RE.fullmatch(class_name):
        return None
    class_pattern = re.compile(
        rf"\bexport\s+(?:default\s+)?class\s+{re.escape(class_name)}\b[^\{{]*\{{",
        re.MULTILINE,
    )
    line_offsets = _text_line_start_offsets(text)
    for class_match in class_pattern.finditer(text):
        open_brace = str(text or "").find("{", class_match.start(), class_match.end())
        if open_brace < 0:
            continue
        close_brace = _typescript_matching_brace_index(text, open_brace)
        if close_brace <= open_brace:
            continue
        body = text[open_brace + 1 : close_brace]
        constructor_match = re.search(r"(?m)^(?P<indent>\s*)private\s+constructor\s*\(", body)
        if constructor_match is None:
            continue
        start = open_brace + 1 + constructor_match.start() + len(str(constructor_match.group("indent") or ""))
        end = start + len("private ")
        line_index = _line_index_for_offset(line_offsets, start)
        if line_index < 0:
            continue
        return line_index, start, end
    return None

def _typescript_safe_structural_member_type(ts_type: str) -> bool:
    normalized = " ".join(str(ts_type or "").strip().split())
    if not normalized:
        return False
    lowered = normalized.lower()
    if lowered in {"unknown", "any", "string", "number", "boolean", "object"}:
        return True
    if re.fullmatch(r"readonlyarray\s*<\s*unknown\s*>", lowered):
        return True
    if re.fullmatch(r"record\s*<\s*(?:string|number)\s*,\s*unknown\s*>", lowered):
        return True
    if re.fullmatch(r"\{[\w\s:;,<>|\"'[\].-]*}", normalized):
        return True
    if re.fullmatch(r"(?:readonly\s+)?[A-Za-z_$][A-Za-z0-9_$]*(?:\[\])+", normalized):
        return True
    return bool(re.fullmatch(r"(?:\"[^\"]+\"|'[^']+')(?:\s*\|\s*(?:\"[^\"]+\"|'[^']+'))*", normalized))

def _typescript_default_value_for_required_property_type(ts_type: str) -> str:
    normalized = " ".join(str(ts_type or "").strip().split())
    lowered = normalized.lower()
    if not normalized:
        return ""
    if "null" in {part.strip() for part in lowered.split("|")}:
        return "null"
    if lowered in {"unknown", "any", "object"}:
        return "{}"
    if "[]" in lowered or lowered.startswith("readonlyarray"):
        return "[]"
    if lowered.startswith("record<") or (normalized.startswith("{") and normalized.endswith("}")):
        return "{}"
    literal_match = re.match(r"^(?:'([^']+)'|\"([^\"]+)\")", normalized)
    if literal_match:
        value = str(literal_match.group(1) or literal_match.group(2) or "")
        return json.dumps(value)
    default_value = _typescript_default_value_for_type(normalized)
    return default_value if default_value != "undefined" else ""

def _text_line_start_offsets(text: str) -> list[int]:
    offsets = [0]
    for match in re.finditer(r"\n", str(text or "")):
        offsets.append(match.end())
    return offsets

def _line_index_for_offset(offsets: Sequence[int], offset: int) -> int:
    if offset < 0:
        return -1
    for index, start in enumerate(offsets):
        next_start = offsets[index + 1] if index + 1 < len(offsets) else None
        if offset >= start and (next_start is None or offset < next_start):
            return index
    return -1

def _line_text_replace_operation(
    *,
    path: str,
    content: str,
    line_index: int,
    replacement: str,
    metadata: Mapping[str, object],
) -> RepairOperation:
    lines = str(content or "").splitlines(keepends=True)
    offsets = _line_start_offsets(lines)
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=offsets[line_index],
        span_end=offsets[line_index + 1],
        expected=lines[line_index],
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata=dict(metadata),
    )

def _typescript_symbol_is_constructed(text: str, symbol: str) -> bool:
    return bool(re.search(rf"\bnew\s+{re.escape(symbol)}\s*\(", str(text or "")))

def _typescript_symbol_is_called(text: str, symbol: str) -> bool:
    token = str(text or "")
    call_re = re.compile(rf"(?<!new\s)\b{re.escape(symbol)}\s*\(")
    return bool(call_re.search(token))

def _typescript_symbol_has_named_constructor_binding(text: str, symbol: str) -> bool:
    token = str(text or "")
    escaped = re.escape(symbol)
    return bool(
        re.search(
            rf"\b(?:const|let|var)\s+[A-Za-z_$][A-Za-z0-9_$]*\s*=\s*new\s+{escaped}\s*\(",
            token,
        )
        or re.search(
            rf"\b(?:public|private|protected)?\s*(?:readonly\s+)?[A-Za-z_$][A-Za-z0-9_$]*\s*=\s*new\s+{escaped}\s*\(",
            token,
        )
    )

def _typescript_symbol_has_field_constructor_binding(text: str, symbol: str) -> bool:
    """True when ``this.field = new Symbol(...)`` or typed field is assigned."""

    token = str(text or "")
    escaped = re.escape(symbol)
    return bool(
        re.search(rf"\bthis\.[A-Za-z_$][A-Za-z0-9_$]*\s*=\s*new\s+{escaped}\s*\(", token)
        or re.search(
            rf":\s*{escaped}\b[\s\S]{{0,200}}?\bthis\.[A-Za-z_$][A-Za-z0-9_$]*\s*=\s*new\s+{escaped}\s*\(",
            token,
        )
    )

def _typescript_methods_used_on_constructed_symbol(text: str, symbol: str) -> list[str]:
    token = str(text or "")
    variables: list[str] = []
    constructed_var_re = re.compile(
        rf"\b(?:const|let|var)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*new\s+{re.escape(symbol)}\s*\("
    )
    for match in constructed_var_re.finditer(token):
        variables.append(str(match.group("name") or ""))
    # Field assignment: this.scene = new GardenScene(...)
    field_re = re.compile(
        rf"\bthis\.(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*new\s+{re.escape(symbol)}\s*\("
    )
    for match in field_re.finditer(token):
        variables.append(str(match.group("name") or ""))
    # Typed field receivers: private readonly scene: GardenScene;
    typed_field_re = re.compile(
        rf"(?:public|private|protected)?\s*(?:readonly\s+)?"
        rf"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*:\s*{re.escape(symbol)}\b"
    )
    for match in typed_field_re.finditer(token):
        variables.append(str(match.group("name") or ""))

    methods: list[str] = []
    for variable in _dedupe_preserve_order([name for name in variables if name]):
        # scene.snapshot() / this.scene.snapshot() / scene.publishForRegistry?.()
        for match in re.finditer(
            rf"\b(?:this\.)?{re.escape(variable)}(?:\?\.)?\.(?P<method>[A-Za-z_$][A-Za-z0-9_$]*)\s*(?:\?\.)?\s*\(",
            token,
        ):
            methods.append(str(match.group("method") or ""))
    direct_re = re.compile(
        rf"\bnew\s+{re.escape(symbol)}\s*\([^)]*\)\s*\.\s*(?P<method>[A-Za-z_$][A-Za-z0-9_$]*)\s*\("
    )
    for match in direct_re.finditer(token):
        methods.append(str(match.group("method") or ""))
    return _dedupe_preserve_order([method for method in methods if method and method != "constructor"])

def _typescript_module_declares_symbol(module_text: str, symbol: str) -> bool:
    return bool(_typescript_module_declared_symbol_kind(module_text, symbol))

def _typescript_module_declared_symbol_kind(module_text: str, symbol: str) -> str:
    if not _TS_IDENTIFIER_RE.fullmatch(symbol):
        return ""
    escaped = re.escape(symbol)
    declaration_re = re.compile(
        rf"^(?:export\s+)?(?:abstract\s+)?(?:async\s+)?"
        rf"(?P<kind>enum|class|interface|type|const|let|var|function)\s+{escaped}\b",
        re.MULTILINE,
    )
    match = declaration_re.search(module_text)
    return str(match.group("kind") or "").strip() if match else ""

def _find_typescript_similar_runtime_declaration(module_text: str, symbol: str) -> str:
    wanted = _normalize_typescript_identifier_for_similarity(symbol)
    if not wanted:
        return ""
    declaration_re = re.compile(
        r"^(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var|enum)\s+"
        r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\b",
        re.MULTILINE,
    )
    best = ""
    best_score = 0
    for match in declaration_re.finditer(module_text):
        name = str(match.group("name") or "").strip()
        if name == symbol:
            continue
        candidate = _normalize_typescript_identifier_for_similarity(name)
        if not candidate:
            continue
        score = 0
        if wanted.startswith(candidate):
            score = len(candidate)
        elif candidate.startswith(wanted):
            score = len(wanted)
        if score > best_score and score >= min(4, len(wanted)):
            best = name
            best_score = score
    return best

def _normalize_typescript_identifier_for_similarity(symbol: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "", str(symbol or "")).lower()
    for suffix in ("checks", "check", "results", "result", "items", "item"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix) + 2:
            normalized = normalized[: -len(suffix)]
            break
    return normalized

def _typescript_declared_type_kind(*, base_files: Mapping[str, str], type_name: str) -> str:
    if not _TS_IDENTIFIER_RE.fullmatch(type_name):
        return ""
    escaped = re.escape(type_name)
    for content in base_files.values():
        match = re.search(rf"(?P<kind>interface|class)\s+{escaped}\b[^{{]*{{", str(content or ""))
        if match:
            return str(match.group("kind") or "")
    return ""

def _normalize_typescript_module_ref(raw: object) -> str:
    value = str(raw or "").strip().rstrip(".")
    previous = None
    while value != previous:
        previous = value
        value = value.strip().strip("'\"`").strip()
    return value.rstrip(".")

def _apply_single_text_operation(content: str, operation: RepairOperation) -> str:
    if operation.span_start is None or operation.span_end is None:
        return content
    return content[: operation.span_start] + str(operation.replacement or "") + content[operation.span_end :]

def _typescript_line_at(text: str, line_number: int) -> str:
    lines = str(text or "").splitlines()
    if line_number <= 0 or line_number > len(lines):
        return ""
    return lines[line_number - 1]

def _typescript_member_usage_is_call(text: str, line_number: int, member: str) -> bool:
    line = _typescript_line_at(text, line_number)
    # Optional call forms: obj.m(, obj.m?.(, obj?.m(, obj?.m?.(
    return bool(
        re.search(
            rf"(?:\?\.|\.)\s*{re.escape(member)}\s*(?:\?\.)?\s*\(",
            line,
        )
    )

def _typescript_missing_member_declared_type(text: str, line_number: int, member: str, *, member_is_call: bool) -> str:
    line = _typescript_line_at(text, line_number)
    if member_is_call:
        # Chained property access after call needs a structural/any return (R180 snapshot().x).
        if re.search(rf"\.\s*{re.escape(member)}\s*\??\s*\([^)]*\)\s*\.", line):
            return "any"
        return "number"
    return _typescript_usage_compatible_member_type(line, member) or "unknown"

def _typescript_usage_compatible_member_type(usage_line: str, member: str) -> str:
    if not _TS_IDENTIFIER_RE.fullmatch(member):
        return ""
    escaped = re.escape(member)
    line = str(usage_line or "")
    if _typescript_member_name_suggests_string(member) and (
        re.search(rf"\.\s*{escaped}\s*(?:={2, 3}|!==?)", line)
        or re.search(rf"(?:={2, 3}|!==?)\s*[^;\n]*\.\s*{escaped}\b", line)
        or re.search(rf"\.\s*{escaped}\s*\.\s*(?:length|trim|toLowerCase|toUpperCase|includes)\b", line)
    ):
        return "string"
    if _typescript_member_name_strongly_suggests_string(member) and re.search(rf"=\s*[^;\n]*\.\s*{escaped}\b", line):
        return "string"
    if _typescript_member_name_suggests_number(member) and re.search(
        rf"(?:\.\s*{escaped}\b\s*(?:[*/%+\-]|[<>]=?)|(?:[*/%+\-]|[<>]=?)\s*[^;\n]*\.\s*{escaped}\b)",
        line,
    ):
        return "number"
    if re.search(rf"\.\s*{escaped}\s*\[", line):
        return "Record<string, unknown>"
    if re.search(rf"\.\s*{escaped}\s*\.\s*(?:length|map|filter|reduce|forEach|some|every|find)\b", line):
        return "ReadonlyArray<unknown>"
    if re.search(rf"\.\s*{escaped}\s*\.\s*(?:toFixed|toExponential|toPrecision)\s*\(", line):
        return "number"
    if re.search(rf"\.\s*{escaped}\s*\.\s*(?:trim|toLowerCase|toUpperCase|includes|startsWith|endsWith)\s*\(", line):
        return "string"
    return ""

def _typescript_member_name_suggests_string(member: str) -> bool:
    lowered = str(member or "").lower()
    return lowered in {
        "id",
        "key",
        "name",
        "title",
        "label",
        "slug",
        "type",
        "status",
        "color",
        "colour",
    } or lowered.endswith(("id", "key", "name", "title", "label", "slug", "type", "status", "color", "colour"))

def _typescript_member_name_strongly_suggests_string(member: str) -> bool:
    lowered = str(member or "").lower()
    return lowered in {"color", "colour"} or lowered.endswith(("color", "colour"))

def _typescript_member_name_suggests_number(member: str) -> bool:
    lowered = str(member or "").lower()
    return lowered in {
        "x",
        "y",
        "z",
        "r",
        "g",
        "b",
        "width",
        "height",
        "size",
        "radius",
        "count",
        "total",
        "amount",
        "quantity",
        "price",
        "score",
        "rating",
        "brightness",
        "intensity",
        "opacity",
        "alpha",
    } or lowered.endswith(
        (
            "x",
            "y",
            "z",
            "width",
            "height",
            "size",
            "radius",
            "count",
            "total",
            "amount",
            "quantity",
            "price",
            "score",
            "rating",
            "brightness",
            "intensity",
            "opacity",
            "alpha",
        )
    )

def _add_typescript_member_operation(
    *,
    base_files: Mapping[str, str],
    type_name: str,
    member: str,
    member_is_call: bool,
) -> RepairOperation | None:
    escaped = re.escape(type_name)
    for path, content in base_files.items():
        match = re.search(rf"(interface\s+{escaped}\b[^{{]*{{|class\s+{escaped}\b[^{{]*{{)", content)
        if not match:
            continue
        insert_at = content.find("\n}", match.end())
        if insert_at < 0:
            continue
        declaration = f"\n  {member}(..._args: unknown[]): unknown;" if member_is_call else f"\n  {member}: unknown;"
        context_start = max(0, match.start())
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=insert_at,
            span_end=insert_at,
            expected="",
            replacement=declaration,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_missing_member",
                "type": type_name,
                "member": member,
                "expected_context_before": content[context_start:insert_at],
                "expected_context_after": content[insert_at : insert_at + 2],
            },
        )
    return None

def _add_typescript_members_operation(
    *,
    base_files: Mapping[str, str],
    type_name: str,
    members: Sequence[tuple[str, bool, str] | tuple[str, bool, str, bool]],
    preferred_usage_paths: Sequence[str] = (),
) -> RepairOperation | None:
    escaped = re.escape(type_name)
    candidate_paths = list(base_files.keys())
    # Prefer declarations imported by the usage file over unrelated same-name types.
    ordered_paths = _order_type_declaration_paths_for_usage(
        base_files=base_files,
        type_name=type_name,
        preferred_usage_paths=preferred_usage_paths,
        candidate_paths=candidate_paths,
    )
    for path in ordered_paths:
        content = str(base_files.get(path) or "")
        match = re.search(rf"(?P<kind>interface|class)\s+{escaped}\b[^{{]*{{", content)
        if not match:
            continue
        insert_at = content.find("\n}", match.end())
        if insert_at < 0:
            insert_at = _typescript_matching_brace_index(content, match.end() - 1)
        if insert_at < 0:
            continue
        existing = _typescript_existing_member_names_for_type(base_files={path: content}, type_name=type_name)
        declarations: list[str] = []
        is_class = str(match.group("kind") or "") == "class"
        class_text = content[match.start() : insert_at]
        for member_spec in members:
            member, member_is_call, declared_type = member_spec[:3]
            static_context = len(member_spec) > 3 and bool(member_spec[3])
            if member in existing or not _TS_IDENTIFIER_RE.fullmatch(member):
                continue
            value_type = declared_type if _typescript_safe_structural_member_type(declared_type) else "unknown"
            if is_class and static_context and member_is_call:
                constructor_args = _typescript_constructor_default_arguments(class_text)
                declarations.append(
                    f"\n  public static {member}(..._args: unknown[]): {type_name} {{"
                    f"\n    return new {type_name}({constructor_args});\n  }}"
                )
            elif is_class and static_context:
                constructor_args = _typescript_constructor_default_arguments(class_text)
                declarations.append(
                    f"\n  public static readonly {member}: {type_name} = new {type_name}({constructor_args});"
                )
            elif is_class and member_is_call:
                return_type = value_type if value_type not in {"unknown"} else "number"
                if return_type == "any":
                    default_value = "undefined as any"
                else:
                    default_value = _typescript_default_value_for_required_property_type(return_type)
                declarations.append(
                    f"\n  public {member}(..._args: unknown[]): {return_type} {{"
                    f"\n    return {default_value};\n  }}"
                )
            elif is_class:
                declarations.append(
                    f"\n  public {member}: {value_type} = {_typescript_default_value_for_required_property_type(value_type)};"
                )
            elif member_is_call:
                return_type = value_type if value_type not in {"unknown", "any"} else "number"
                declarations.append(f"\n  {member}(..._args: unknown[]): {return_type};")
            else:
                declarations.append(f"\n  {member}: {value_type};")
        if not declarations:
            # Members already exist on this declaration; try next candidate path.
            continue
        context_start = max(0, match.start())
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=insert_at,
            span_end=insert_at,
            expected="",
            replacement="".join(declarations),
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_missing_member",
                "type": type_name,
                "members": tuple(member_spec[0] for member_spec in members),
                "batched_same_type_members": True,
                "expected_context_before": content[context_start:insert_at],
                "expected_context_after": content[insert_at : insert_at + 2],
            },
        )
    return None

def _order_type_declaration_paths_for_usage(
    *,
    base_files: Mapping[str, str],
    type_name: str,
    preferred_usage_paths: Sequence[str],
    candidate_paths: Sequence[str],
) -> list[str]:
    """Order class/interface declaration files: imported by usage first, then others."""

    escaped = re.escape(type_name)
    declaring = [
        path
        for path in candidate_paths
        if re.search(rf"(?:export\s+)?(?:interface|class)\s+{escaped}\b", str(base_files.get(path) or ""))
    ]
    if not declaring:
        return list(candidate_paths)
    preferred: list[str] = []
    for usage_path in preferred_usage_paths:
        usage_text = str(base_files.get(usage_path) or "")
        for match in re.finditer(
            r"""from\s+['"](?P<mod>[^'"]+)['"]""",
            usage_text,
        ):
            resolved = _resolve_relative_ts_module_path(usage_path, str(match.group("mod") or ""), base_files)
            if resolved and resolved in declaring and resolved not in preferred:
                preferred.append(resolved)
    rest = [path for path in declaring if path not in preferred]
    # Prefer incomplete stubs (few members) over full implementations when usage imports them.
    def _member_count(path: str) -> int:
        return len(_typescript_existing_member_names_for_type(base_files={path: str(base_files.get(path) or "")}, type_name=type_name))

    preferred.sort(key=_member_count)
    rest.sort(key=_member_count)
    ordered = preferred + rest
    # Fall back to any path for non-declaring scan compatibility.
    for path in candidate_paths:
        if path not in ordered:
            ordered.append(path)
    return ordered

def _extend_typescript_declare_const_type_literal_operation(
    *,
    path: str,
    content: str,
    receiver: str,
    member: str,
    member_is_call: bool,
) -> RepairOperation | None:
    """Insert missing member into ``declare const receiver: { ... }`` type literal (R180)."""

    if not _TS_IDENTIFIER_RE.fullmatch(receiver) or not _TS_IDENTIFIER_RE.fullmatch(member):
        return None
    pattern = re.compile(
        rf"declare\s+const\s+{re.escape(receiver)}\s*:\s*\{{",
        re.MULTILINE,
    )
    match = pattern.search(content)
    if not match:
        return None
    brace_open = match.end() - 1
    brace_close = _typescript_matching_brace_index(content, brace_open)
    if brace_close < 0:
        return None
    body = content[brace_open + 1 : brace_close]
    if re.search(rf"\b{re.escape(member)}\s*[?]?\s*[:(]", body):
        return None
    if member_is_call:
        declaration = f"\n  {member}?(..._args: unknown[]): void;"
    else:
        declaration = f"\n  {member}?: unknown;"
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=brace_close,
        span_end=brace_close,
        expected="",
        replacement=declaration,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_declare_const_literal_member",
            "receiver": receiver,
            "member": member,
            "expected_context_before": content[max(0, brace_close - 120) : brace_close],
            "expected_context_after": content[brace_close : brace_close + 40],
        },
    )

def _typescript_constructor_default_arguments(class_text: str) -> str:
    match = re.search(r"\bconstructor\s*\((?P<params>[^)]*)\)", str(class_text or ""), re.DOTALL)
    if not match:
        return ""
    defaults: list[str] = []
    for raw_param in str(match.group("params") or "").split(","):
        param = raw_param.strip()
        if not param:
            continue
        type_match = re.search(r":\s*(?P<type>[^=,]+)", param)
        param_type = str(type_match.group("type") or "unknown").strip() if type_match else "unknown"
        default_value = _typescript_default_value_for_required_property_type(param_type)
        defaults.append(default_value if default_value else "undefined")
    return ", ".join(defaults)

def _resolve_relative_ts_module_path(importer_path: str, module_ref: str, base_files: Mapping[str, str]) -> str:
    if not module_ref.startswith("."):
        return ""
    base_dir = posixpath.dirname(importer_path)
    raw = posixpath.normpath(posixpath.join(base_dir, module_ref))
    raw_root, raw_ext = posixpath.splitext(raw)
    candidates = [raw, f"{raw}.ts", f"{raw}.tsx", posixpath.join(raw, "index.ts"), posixpath.join(raw, "index.tsx")]
    if raw_ext.lower() in {".js", ".jsx", ".mjs", ".cjs"}:
        candidates.extend(
            (
                f"{raw_root}.ts",
                f"{raw_root}.tsx",
                posixpath.join(raw_root, "index.ts"),
                posixpath.join(raw_root, "index.tsx"),
            )
        )
    for candidate in candidates:
        normalized = _normalize_repair_path(candidate)
        if normalized in base_files:
            return normalized
    return ""

def _repair_typescript_unresolved_identifier_import(
    *,
    path: str,
    original: str,
    base_files: Mapping[str, str],
    missing_symbol: str,
) -> tuple[str, str]:
    if not _TS_IDENTIFIER_RE.fullmatch(missing_symbol):
        return original, ""
    for match in _TS_NAMED_IMPORT_RE.finditer(original):
        module_ref = str(match.group("module") or "")
        module_path = _resolve_relative_ts_module_path(path, module_ref, base_files)
        if not module_path:
            continue
        # Follow barrel star/named reexports (R170 MoonPhase via export * from './types').
        if not _typescript_module_exports_symbol_resolved(
            module_path=module_path,
            base_files=base_files,
            symbol=missing_symbol,
        ):
            continue
        symbols = str(match.group("symbols") or "")
        existing = _parse_named_import_symbols(symbols)
        if missing_symbol in existing:
            continue
        replacement_symbols = _typescript_named_import_symbols_with_added_symbol(symbols, missing_symbol)
        if replacement_symbols == symbols:
            continue
        return (
            f"{original[: match.start('symbols')]}{replacement_symbols}{original[match.end('symbols') :]}",
            f"import:{module_ref}:{missing_symbol}",
        )
    return original, ""

def _parse_named_import_symbols(symbols: str) -> list[str]:
    parsed: list[str] = []
    for raw in str(symbols or "").split(","):
        token = raw.strip().split(" as ", 1)[-1].strip()
        if _TS_IDENTIFIER_RE.fullmatch(token):
            parsed.append(token)
    return _dedupe_preserve_order(parsed)

def _typescript_named_import_symbols_with_added_symbol(symbols: str, missing_symbol: str) -> str:
    if not _TS_IDENTIFIER_RE.fullmatch(missing_symbol):
        return str(symbols or "")
    raw_symbols = str(symbols or "")
    parts = [part.strip() for part in raw_symbols.split(",") if part.strip()]
    if not parts:
        return raw_symbols
    if "\n" not in raw_symbols and "\r" not in raw_symbols:
        leading = " " if raw_symbols[:1].isspace() else ""
        trailing = " " if raw_symbols[-1:].isspace() else ""
        return f"{leading}{', '.join([*parts, missing_symbol])}{trailing}"
    newline = "\r\n" if "\r\n" in raw_symbols else "\n"
    indent = _typescript_named_specifier_indent(raw_symbols)
    return newline + "".join(f"{indent}{part},{newline}" for part in [*parts, missing_symbol])

def _typescript_imported_const_class_alias_available(
    *,
    base_files: Mapping[str, str],
    importer_path: str,
    importer_text: str,
    symbol: str,
) -> bool:
    for match in _TS_NAMED_IMPORT_RE.finditer(importer_text):
        imported_symbols = _parse_named_import_symbols(str(match.group("symbols") or ""))
        if symbol not in imported_symbols:
            continue
        module_path = _resolve_relative_ts_module_path(importer_path, str(match.group("module") or ""), base_files)
        if not module_path:
            continue
        if _typescript_module_exports_const_class_alias(str(base_files.get(module_path) or ""), symbol):
            return True
    return False

def _typescript_module_exports_const_class_alias(module_text: str, symbol: str) -> bool:
    if not _TS_IDENTIFIER_RE.fullmatch(symbol):
        return False
    alias_re = re.compile(
        rf"\bexport\s+const\s+{re.escape(symbol)}\s*=\s*(?P<class_name>[A-Za-z_$][A-Za-z0-9_$]*)\s*;",
        re.MULTILINE,
    )
    alias = alias_re.search(module_text)
    if not alias:
        return False
    class_name = str(alias.group("class_name") or "")
    if not _TS_IDENTIFIER_RE.fullmatch(class_name):
        return False
    class_re = re.compile(rf"\b(?:export\s+)?class\s+{re.escape(class_name)}\b", re.MULTILINE)
    return bool(class_re.search(module_text))

def _typescript_duplicate_identifier_targets(diagnostics: Sequence[RepairDiagnostic]) -> dict[str, set[str]]:
    targets: dict[str, set[str]] = {}
    for diagnostic in diagnostics:
        if diagnostic.code != "typescript_ts2300":
            continue
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if not path:
            continue
        match = _TS_DUPLICATE_IDENTIFIER_MESSAGE_RE.search(str(diagnostic.message or diagnostic.raw or ""))
        name = str(match.group("name") or "").strip() if match else ""
        if _TS_IDENTIFIER_RE.fullmatch(name):
            targets.setdefault(path, set()).add(name)
    return targets

def _typescript_string_brand_type_sources(base_files: Mapping[str, str]) -> dict[str, str]:
    sources: dict[str, str] = {}
    for path, content in base_files.items():
        normalized = _normalize_repair_path(path)
        if not normalized.endswith((".ts", ".tsx")):
            continue
        for match in _TS_STRING_BRAND_TYPE_ALIAS_RE.finditer(str(content or "")):
            name = str(match.group("name") or "").strip()
            if name:
                sources.setdefault(name, normalized)
    return sources

def _typescript_type_only_value_usage_symbol(diagnostic: RepairDiagnostic) -> str:
    text = f"{diagnostic.message}\n{diagnostic.raw}"
    match = _TS_TYPE_ONLY_VALUE_USAGE_MESSAGE_RE.search(text)
    if not match:
        return ""
    candidate = str(match.group("name") or "").strip()
    return candidate if _TS_IDENTIFIER_RE.fullmatch(candidate) else ""

def _typescript_type_value_dot_member(
    *,
    base_files: Mapping[str, str],
    diagnostic: RepairDiagnostic,
    symbol: str,
) -> str:
    path = _normalize_repair_path(str(diagnostic.path or ""))
    content = str(base_files.get(path) or "")
    line_number = int(diagnostic.line or 0)
    if not path or not content:
        return ""
    fallback_match = re.search(rf"\b{re.escape(symbol)}\.(?P<member>[A-Za-z_$][\w$]*)\b", content)
    if line_number <= 0:
        return str(fallback_match.group("member") or "") if fallback_match else ""
    lines = content.splitlines()
    if line_number > len(lines):
        return ""
    line = lines[line_number - 1]
    match = re.search(rf"\b{re.escape(symbol)}\.(?P<member>[A-Za-z_$][\w$]*)\b", line)
    if not match and fallback_match is not None:
        match = fallback_match
    if match is None:
        return ""
    return str(match.group("member") or "")

def _typescript_file_has_type_name_import(content: str, type_name: str) -> bool:
    escaped = re.escape(type_name)
    return bool(
        re.search(rf"\bimport\s+type\s*\{{[^}}]*\b{escaped}\b[^}}]*\}}\s+from\b", content, re.DOTALL)
        or re.search(rf"\bimport\s*\{{[^}}]*\btype\s+{escaped}\b[^}}]*\}}\s+from\b", content, re.DOTALL)
    )

def _typescript_insert_type_import_operation(
    *,
    path: str,
    content: str,
    type_name: str,
    source_path: str,
) -> RepairOperation | None:
    module_specifier = _relative_import_specifier_for_actual_path(
        importer_rel=path,
        original_specifier="",
        actual_target_rel=source_path,
    )
    import_line = f'import type {{ {type_name} }} from "{module_specifier}";\n'
    insert_at = _typescript_import_insert_offset(content)
    before_hash = sha256_text(content)
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=insert_at,
        span_end=insert_at,
        expected="",
        replacement=import_line,
        before_hash=before_hash,
        metadata={
            "repair_kind": "typescript_branded_literal_type_import",
            "target_type": type_name,
            "module_specifier": module_specifier,
            "expected_context_before": content[max(0, insert_at - 240) : insert_at],
            "expected_context_after": content[insert_at : insert_at + 120],
        },
    )

def _typescript_import_insert_offset(content: str) -> int:
    matches = list(re.finditer(r"^import\b[^\n]*(?:\n|$)", content, re.MULTILINE))
    if matches:
        return matches[-1].end()
    header_match = re.match(r"^(?:/\*.*?\*/\s*)", content, re.DOTALL)
    return header_match.end() if header_match else 0

def _typescript_named_type_specifier_names(symbols: str) -> set[str]:
    names: set[str] = set()
    for raw in str(symbols or "").split(","):
        token = str(raw or "").strip()
        if not token.lower().startswith("type "):
            continue
        name = _typescript_named_export_specifier_name(token[5:].strip())
        if name:
            names.add(name)
    return names

def _typescript_named_value_specifier_names(symbols: str) -> set[str]:
    names: set[str] = set()
    for raw in str(symbols or "").split(","):
        name = _typescript_named_value_specifier_name(raw)
        if name:
            names.add(name)
    return names

def _typescript_named_value_specifier_name(raw: str) -> str:
    token = str(raw or "").strip()
    if not token or token.startswith("type "):
        return ""
    return _typescript_named_export_specifier_name(token)

def _typescript_named_export_specifier_name(raw: str) -> str:
    token = str(raw or "").strip()
    if not token:
        return ""
    candidate = re.split(r"\s+as\s+", token, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    return candidate if _TS_IDENTIFIER_RE.fullmatch(candidate) else ""

def _typescript_named_specifier_indent(symbols: str) -> str:
    for line in str(symbols or "").splitlines():
        if line.strip():
            match = re.match(r"^\s*", line)
            indent = match.group(0) if match else ""
            return indent or "  "
    return "  "

def _typescript_module_exports_symbol(module_text: str, symbol: str) -> bool:
    escaped = re.escape(symbol)
    if re.search(rf"\bexport\s+(?:type|interface|enum|class|const|let|var|function)\s+{escaped}\b", module_text):
        return True
    for match in re.finditer(r"\bexport\s+(?:type\s+)?\{(?P<symbols>[^}]+)\}", module_text):
        for token in str(match.group("symbols") or "").split(","):
            parts = re.split(r"\s+as\s+", token.strip(), maxsplit=1)
            exported = parts[-1].strip()
            if exported == symbol:
                return True
    return False

def _typescript_module_exports_symbol_resolved(
    *,
    module_path: str,
    base_files: Mapping[str, str],
    symbol: str,
    _depth: int = 0,
    _seen: set[str] | None = None,
) -> bool:
    """True when module or its star/named reexport chain exports ``symbol``.

    R170: barrel ``export * from './types'`` re-exports ``MoonPhase``; local-text
    only checks miss star reexports and leave TS2304 on re-export sites.
    """

    if not _TS_IDENTIFIER_RE.fullmatch(symbol):
        return False
    normalized_path = _normalize_repair_path(module_path)
    if not normalized_path:
        return False
    seen = _seen if _seen is not None else set()
    if normalized_path in seen or _depth > 5:
        return False
    seen.add(normalized_path)
    module_text = str(base_files.get(normalized_path) or "")
    if not module_text:
        return False
    if _typescript_module_exports_symbol(module_text, symbol):
        return True
    for match in _TS_STAR_REEXPORT_RE.finditer(module_text):
        child = _resolve_relative_ts_module_path(normalized_path, str(match.group("mod") or ""), base_files)
        if child and _typescript_module_exports_symbol_resolved(
            module_path=child,
            base_files=base_files,
            symbol=symbol,
            _depth=_depth + 1,
            _seen=seen,
        ):
            return True
    return False

def _parse_typescript_cannot_find_name_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for diagnostic in diagnostics:
        for match in _TS_CANNOT_FIND_NAME_RAW_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            parsed.append(
                {
                    "file": _normalize_repair_path(str(match.group("file") or "")),
                    "line": str(match.group("line") or ""),
                    "col": str(match.group("col") or ""),
                    "symbol": str(match.group("symbol") or ""),
                }
            )
    return [item for item in parsed if item["file"] and item["symbol"]]

def _typescript_missing_identifier_usage_is_type_position(text: str, item: Mapping[str, str]) -> bool:
    line_number = _to_positive_int(item.get("line"))
    lines = str(text or "").splitlines()
    if line_number <= 0 or line_number > len(lines):
        return False
    symbol = re.escape(str(item.get("symbol") or ""))
    return bool(re.search(rf"[:<,|&([]\s*{symbol}\b|\bas\s+{symbol}\b", lines[line_number - 1]))

def _resolve_case_variant_base_file(*, base_files: Mapping[str, str], relative_path: str) -> str:
    normalized = _normalize_repair_path(relative_path)
    if not normalized:
        return ""
    lowered = normalized.lower()
    matches = [path for path in base_files if path.lower() == lowered]
    return matches[0] if len(matches) == 1 else ""

def _relative_import_specifier_for_actual_path(
    *,
    importer_rel: str,
    original_specifier: str,
    actual_target_rel: str,
) -> str:
    relative = posixpath.relpath(actual_target_rel, posixpath.dirname(importer_rel) or ".")
    if not relative.startswith("."):
        relative = f"./{relative}"
    if not posixpath.splitext(original_specifier)[1]:
        for suffix in _relative_import_suffix_order(importer_rel):
            if relative.endswith(suffix):
                relative = relative[: -len(suffix)]
                break
    return relative

def _relative_import_suffix_order(importer_rel: str) -> tuple[str, ...]:
    if importer_rel.endswith(".tsx"):
        return (".tsx", ".ts", ".jsx", ".js")
    if importer_rel.endswith(".jsx"):
        return (".jsx", ".js", ".tsx", ".ts")
    return (".ts", ".tsx", ".js", ".jsx")

def _typescript_import_pairs_from_clause(clause: str) -> list[tuple[str, str]]:
    clause = str(clause or "").strip()
    if clause.startswith("type "):
        clause = clause[5:].strip()
    pairs: list[tuple[str, str]] = []
    namespace_match = re.fullmatch(r"\*\s+as\s+([A-Za-z_$][\w$]*)", clause)
    if namespace_match:
        return []
    compact_clause = clause.replace(" ", "")
    if clause.startswith("{") and clause.endswith("}"):
        default_clause = ""
        named_clause = clause[1:-1]
    elif ",{" in compact_clause:
        default_clause, named_clause = clause.split(",", 1)
        named_clause = named_clause.strip()
        named_clause = named_clause[1:-1] if named_clause.startswith("{") and named_clause.endswith("}") else ""
    else:
        default_clause = clause
        named_clause = ""
    default_name = default_clause.strip()
    if _TS_IDENTIFIER_RE.fullmatch(default_name):
        pairs.append(("default", default_name))
    for raw_part in named_clause.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if part.startswith("type "):
            part = part[5:].strip()
        alias_parts = re.split(r"\s+as\s+", part, maxsplit=1, flags=re.IGNORECASE)
        imported = alias_parts[0].strip()
        local = alias_parts[-1].strip()
        if _TS_IDENTIFIER_RE.fullmatch(imported) and _TS_IDENTIFIER_RE.fullmatch(local):
            pairs.append((imported, local))
    return pairs

def _typescript_identifier_used_outside_span(content: str, name: str, span: tuple[int, int]) -> bool:
    if not _TS_IDENTIFIER_RE.fullmatch(name):
        return False
    outside = content[: span[0]] + content[span[1] :]
    return re.search(rf"\b{re.escape(name)}\b", outside) is not None

def _too_many_arguments_declaration_operation(
    *,
    base_files: Mapping[str, str],
    method_name: str,
    expected_count: int,
) -> RepairOperation | None:
    if expected_count != 0:
        return None
    declaration = _find_unique_typescript_function_declaration(
        base_files=base_files,
        function_name=method_name,
        expected_count=expected_count,
    )
    if declaration is None:
        return None
    declaration_path, declaration_line_index, declaration_line = declaration
    repaired_line = _add_rest_param_to_typescript_callable(declaration_line.rstrip("\r\n"))
    if repaired_line == declaration_line.rstrip("\r\n"):
        return None
    newline = declaration_line[len(declaration_line.rstrip("\r\n")) :]
    return _line_text_replace_operation(
        path=declaration_path,
        content=str(base_files[declaration_path]),
        line_index=declaration_line_index,
        replacement=f"{repaired_line}{newline}",
        metadata={
            "repair_kind": "typescript_too_many_arguments",
            "method": method_name,
            "repair": "declaration_rest_parameter",
        },
    )

def _too_many_arguments_callsite_trim_operation(
    *,
    path: str,
    content: str,
    line_index: int,
    method_name: str,
    expected_count: int,
    got_count: int,
    column: int,
    base_files: Mapping[str, str],
) -> RepairOperation | None:
    """Drop surplus call arguments by aligning identifiers to declaration params (R169).

    Live: ``paintFlowers(ctx, surface, garden, t)`` vs
    ``function paintFlowers(ctx, garden, t)`` — drop the unmatched middle ``surface``.
    """

    if got_count <= expected_count or expected_count <= 0:
        return None
    declaration = _find_unique_typescript_function_declaration(
        base_files=base_files,
        function_name=method_name,
        expected_count=expected_count,
    )
    if declaration is None:
        declaration = _find_unique_typescript_function_declaration_multiline(
            base_files=base_files,
            function_name=method_name,
            expected_count=expected_count,
        )
    if declaration is None:
        return None
    _, _, decl_header = declaration
    params = _typescript_function_param_names_from_header(decl_header)
    if len(params) != expected_count:
        return None
    lines = content.splitlines(keepends=True)
    line = lines[line_index]
    line_body = line.rstrip("\r\n")
    newline = line[len(line_body) :]
    column_index = max(0, int(column) - 1)
    call_re = re.compile(rf"\b{re.escape(method_name)}\s*\(")
    for match in call_re.finditer(line_body):
        open_index = line_body.find("(", match.start())
        close_index = _find_matching_paren(line_body, open_index)
        if close_index < 0 or not (match.start() <= column_index <= close_index):
            continue
        spans = _split_typescript_argument_spans(line_body, open_index + 1, close_index)
        if len(spans) != got_count:
            continue
        arg_texts = [line_body[start:end].strip() for start, end in spans]
        selected = _typescript_select_args_for_params(arg_texts, params)
        if selected is None or len(selected) != expected_count:
            continue
        repaired_args = ", ".join(selected)
        repaired_line = f"{line_body[: open_index + 1]}{repaired_args}{line_body[close_index:]}{newline}"
        if repaired_line == line:
            return None
        return _line_text_replace_operation(
            path=path,
            content=content,
            line_index=line_index,
            replacement=repaired_line,
            metadata={
                "repair_kind": "typescript_too_many_arguments",
                "method": method_name,
                "repair": "callsite_drop_unmatched_args",
            },
        )
    return None

def _too_many_arguments_declaration_expand_operation(
    *,
    base_files: Mapping[str, str],
    method_name: str,
    expected_count: int,
    got_count: int,
) -> RepairOperation | None:
    """Insert ``_extraN: unknown`` parameters so declaration accepts the call (R169)."""

    if got_count <= expected_count:
        return None
    declaration = _find_unique_typescript_function_declaration(
        base_files=base_files,
        function_name=method_name,
        expected_count=expected_count,
    )
    multiline = False
    if declaration is None:
        declaration = _find_unique_typescript_function_declaration_multiline(
            base_files=base_files,
            function_name=method_name,
            expected_count=expected_count,
        )
        multiline = declaration is not None
    if declaration is None:
        return None
    declaration_path, declaration_line_index, declaration_header = declaration
    content = str(base_files.get(declaration_path) or "")
    if not content:
        return None
    if multiline:
        return _expand_multiline_function_params(
            path=declaration_path,
            content=content,
            function_name=method_name,
            expected_count=expected_count,
            got_count=got_count,
        )
    repaired = _insert_unknown_params_into_callable_header(
        declaration_header.rstrip("\r\n"),
        add_count=got_count - expected_count,
    )
    if repaired == declaration_header.rstrip("\r\n"):
        return None
    newline = declaration_header[len(declaration_header.rstrip("\r\n")) :]
    return _line_text_replace_operation(
        path=declaration_path,
        content=content,
        line_index=declaration_line_index,
        replacement=f"{repaired}{newline}",
        metadata={
            "repair_kind": "typescript_too_many_arguments",
            "method": method_name,
            "repair": "declaration_insert_unknown_params",
        },
    )

def _typescript_function_param_names_from_header(header: str) -> list[str]:
    open_index = header.find("(")
    close_index = _find_matching_paren(header, open_index)
    if open_index < 0 or close_index < 0:
        return []
    params = _split_typescript_params(header[open_index + 1 : close_index])
    names: list[str] = []
    for param in params:
        name = param.strip().split(":", 1)[0].strip().rstrip("?").lstrip("...")
        if _TS_IDENTIFIER_RE.fullmatch(name):
            names.append(name)
    return names

def _typescript_select_args_for_params(
    arg_texts: Sequence[str],
    param_names: Sequence[str],
) -> list[str] | None:
    """Select a subsequence of call args that best matches declaration param names."""

    if not param_names or len(arg_texts) < len(param_names):
        return None
    # Prefer exact identifier matches for each param name.
    selected: list[str] = []
    used: set[int] = set()
    for param in param_names:
        found = None
        for index, arg in enumerate(arg_texts):
            if index in used:
                continue
            if _TS_IDENTIFIER_RE.fullmatch(arg.strip()) and arg.strip() == param:
                found = index
                break
        if found is None:
            # Fall back to first unused arg (positional fill for non-matching).
            for index in range(len(arg_texts)):
                if index not in used:
                    found = index
                    break
        if found is None:
            return None
        used.add(found)
        selected.append(arg_texts[found])
    # Require that at least one non-positional (name) match happened when surplus exists.
    if len(arg_texts) > len(param_names):
        name_hits = sum(
            1
            for param, arg in zip(param_names, selected, strict=False)
            if _TS_IDENTIFIER_RE.fullmatch(arg.strip()) and arg.strip() == param
        )
        if name_hits < max(1, len(param_names) - 1):
            return None
    return selected

def _find_unique_typescript_function_declaration_multiline(
    *,
    base_files: Mapping[str, str],
    function_name: str,
    expected_count: int,
) -> tuple[str, int, str] | None:
    """Find multi-line ``function name(\\n params \\n)`` with exact param count."""

    if not _TS_IDENTIFIER_RE.fullmatch(function_name):
        return None
    header_re = re.compile(
        rf"(?ms)^\s*(?:export\s+)?(?:async\s+)?function\s+{re.escape(function_name)}\s*\(",
    )
    matches: list[tuple[str, int, str]] = []
    for path, text in base_files.items():
        if not path.endswith((".ts", ".tsx")) or path.endswith(".d.ts"):
            continue
        content = str(text or "")
        for match in header_re.finditer(content):
            open_index = content.find("(", match.start())
            close_index = _find_matching_paren(content, open_index)
            if open_index < 0 or close_index < 0:
                continue
            params = _split_typescript_params(content[open_index + 1 : close_index])
            if len(params) != expected_count:
                continue
            # header from line start through closing paren (and optional return type start)
            line_start = content.rfind("\n", 0, match.start()) + 1
            header = content[line_start : close_index + 1]
            line_index = content.count("\n", 0, line_start)
            matches.append((path, line_index, header))
    return matches[0] if len(matches) == 1 else None

def _insert_unknown_params_into_callable_header(header: str, *, add_count: int) -> str:
    if add_count <= 0:
        return header
    open_index = header.find("(")
    close_index = _find_matching_paren(header, open_index)
    if open_index < 0 or close_index < 0:
        return header
    params = _split_typescript_params(header[open_index + 1 : close_index])
    extras = [f"_extra{index + 1}: unknown" for index in range(add_count)]
    # Insert extras before the last param when possible (common surface padding).
    if len(params) >= 2:
        new_params = [*params[:-1], *extras, params[-1]]
    else:
        new_params = [*params, *extras]
    return header[: open_index + 1] + ", ".join(new_params) + header[close_index:]

def _expand_multiline_function_params(
    *,
    path: str,
    content: str,
    function_name: str,
    expected_count: int,
    got_count: int,
) -> RepairOperation | None:
    header_re = re.compile(
        rf"(?ms)^\s*(?:export\s+)?(?:async\s+)?function\s+{re.escape(function_name)}\s*\(",
    )
    match = header_re.search(content)
    if match is None:
        return None
    open_index = content.find("(", match.start())
    close_index = _find_matching_paren(content, open_index)
    if open_index < 0 or close_index < 0:
        return None
    params_block = content[open_index + 1 : close_index]
    params = _split_typescript_params(params_block)
    if len(params) != expected_count:
        return None
    add_count = got_count - expected_count
    extras = [f"_extra{index + 1}: unknown" for index in range(add_count)]
    # Prefer multi-line style with trailing commas.
    indent_match = re.search(r"\n(?P<indent>[ \t]+)\S", params_block)
    indent = indent_match.group("indent") if indent_match else "  "
    if "\n" in params_block:
        body_params = [*params[:-1], *extras, params[-1]] if len(params) >= 2 else [*params, *extras]
        new_block = "\n" + ",\n".join(f"{indent}{item}" for item in body_params) + ",\n"
        # keep indent of closing paren line
        close_line_start = content.rfind("\n", 0, close_index) + 1
        close_indent = re.match(r"[ \t]*", content[close_line_start:close_index])
        close_pad = close_indent.group(0) if close_indent else ""
        new_block = "\n" + ",\n".join(f"{indent}{item}" for item in body_params) + f",\n{close_pad}"
    else:
        new_block = ", ".join([*params[:-1], *extras, params[-1]] if len(params) >= 2 else [*params, *extras])
    replacement = content[: open_index + 1] + new_block + content[close_index:]
    if replacement == content:
        return None
    return RepairOperation(
        kind="write_file",
        path=path,
        content=replacement,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_too_many_arguments",
            "method": function_name,
            "repair": "declaration_insert_unknown_params_multiline",
            "write_file_reason": "too_many_arguments_expand_signature",
        },
    )

def _typescript_call_name_from_usage_line(usage_line: str, column: int) -> str:
    prefix = usage_line[: max(0, min(len(usage_line), int(column)))]
    matches = list(re.finditer(r"(?:\.|\b)(?P<name>[A-Za-z_$][\w$]*)\s*\(", prefix))
    if not matches:
        matches = list(re.finditer(r"\b(?P<name>[A-Za-z_$][\w$]*)\s*\(", usage_line))
    return str(matches[-1].group("name") if matches else "").strip()

def _find_unique_typescript_method_declaration(
    *,
    base_files: Mapping[str, str],
    method_name: str,
    expected_count: int,
) -> tuple[str, int, str] | None:
    if not _TS_IDENTIFIER_RE.fullmatch(method_name):
        return None
    method_re = re.compile(
        rf"^\s*(?:public\s+|private\s+|protected\s+)?(?:async\s+)?{re.escape(method_name)}\s*\((?P<params>[^)]*)\)",
    )
    matches: list[tuple[str, int, str]] = []
    for path, text in base_files.items():
        if not path.endswith((".ts", ".tsx")) or path.endswith(".d.ts"):
            continue
        for line_index, line in enumerate(str(text or "").splitlines(keepends=True)):
            match = method_re.search(line.rstrip("\r\n"))
            if not match:
                continue
            params = _split_typescript_params(str(match.group("params") or ""))
            if len(params) >= expected_count:
                matches.append((path, line_index, line))
    return matches[0] if len(matches) == 1 else None

def _find_unique_typescript_function_declaration(
    *,
    base_files: Mapping[str, str],
    function_name: str,
    expected_count: int,
) -> tuple[str, int, str] | None:
    if not _TS_IDENTIFIER_RE.fullmatch(function_name):
        return None
    function_re = re.compile(
        rf"^\s*(?:export\s+)?(?:async\s+)?function\s+{re.escape(function_name)}\s*"
        r"\((?P<params>[^)]*)\)",
    )
    matches: list[tuple[str, int, str]] = []
    for path, text in base_files.items():
        if not path.endswith((".ts", ".tsx")) or path.endswith(".d.ts"):
            continue
        for line_index, line in enumerate(str(text or "").splitlines(keepends=True)):
            match = function_re.search(line.rstrip("\r\n"))
            if not match:
                continue
            params = _split_typescript_params(str(match.group("params") or ""))
            if len(params) == expected_count:
                matches.append((path, line_index, line))
    return matches[0] if len(matches) == 1 else None

def _add_rest_param_to_typescript_callable(line: str) -> str:
    open_index = line.find("(")
    close_index = _find_matching_paren(line, open_index)
    if open_index < 0 or close_index < 0:
        return line
    params_text = line[open_index + 1 : close_index].strip()
    if "..._args" in params_text:
        return line
    separator = ", " if params_text else ""
    repaired_params = f"{params_text}{separator}..._args: unknown[]"
    return line[: open_index + 1] + repaired_params + line[close_index:]

def _add_defaults_to_typescript_method_params(line: str, *, got_count: int, expected_count: int) -> str:
    open_index = line.find("(")
    close_index = _find_matching_paren(line, open_index)
    if open_index < 0 or close_index < 0:
        return line
    params_text = line[open_index + 1 : close_index]
    params = _split_typescript_params(params_text)
    if len(params) < expected_count or got_count >= expected_count:
        return line
    changed = False
    for index in range(got_count, min(expected_count, len(params))):
        repaired = _typescript_param_with_default(params[index])
        if repaired != params[index]:
            params[index] = repaired
            changed = True
    if not changed:
        return line
    return line[: open_index + 1] + ", ".join(params) + line[close_index:]

def _split_typescript_params(params_text: str) -> list[str]:
    spans = _split_typescript_argument_spans(params_text, 0, len(params_text))
    return [params_text[start:end].strip() for start, end in spans if params_text[start:end].strip()]

def _typescript_param_with_default(param: str) -> str:
    if "=" in param:
        return param
    if ":" not in param:
        return f"{param} = undefined"
    name, annotation = param.split(":", 1)
    ts_type = annotation.strip()
    return f"{name.strip()}: {ts_type} = {_typescript_default_value_for_type(ts_type)}"

def _typescript_errors_require_dom_lib(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    text = _diagnostic_text(diagnostics).lower()
    dom_global_names = ("console", "window", "document", "navigator", "location")
    dom_type_names = ("htmlelement", "htmlelementtagnamemap")
    return ("include 'dom'" in text and any(f"cannot find name '{name}'" in text for name in dom_global_names)) or any(
        f"cannot find name '{name}'" in text for name in dom_type_names
    )

def _typescript_errors_require_import_meta_module(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    text = _diagnostic_text(diagnostics).lower()
    return "ts1343" in text and "import.meta" in text and "module" in text

def _typescript_errors_require_es2021_lib(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    text = _diagnostic_text(diagnostics).lower()
    return (
        ("ts2550" in text or "property 'replaceall' does not exist" in text)
        and "replaceall" in text
        and ("es2021" in text or "target library" in text or "lib" in text)
    )

def _typescript_libs_allow_es2021(libs: Sequence[str]) -> bool:
    allowed = {"es2021", "es2022", "es2023", "es2024", "esnext"}
    return any(str(item or "").strip().lower() in allowed for item in libs)

def _typescript_promote_libs_to_es2021(libs: Sequence[str], target: object) -> list[str]:
    promoted: list[str] = []
    replaced = False
    for item in libs:
        raw = str(item or "").strip()
        lowered = raw.lower()
        if lowered in {"es5", "es6", "es2015", "es2016", "es2017", "es2018", "es2019", "es2020"}:
            if not replaced:
                promoted.append("ES2021")
                replaced = True
            continue
        if raw:
            promoted.append(raw)
    if not replaced and not _typescript_libs_allow_es2021(promoted):
        target_text = str(target or "").strip()
        if target_text and target_text.lower() not in {"es2021", "es2022", "es2023", "es2024", "esnext"}:
            promoted.insert(0, "ES2021")
        elif target_text:
            promoted.insert(0, target_text)
        else:
            promoted.insert(0, "ES2021")
    return list(dict.fromkeys(promoted))

def _typescript_module_allows_import_meta(raw_module: object) -> bool:
    return str(raw_module or "").strip().lower() in {
        "es2020",
        "es2022",
        "esnext",
        "system",
        "node16",
        "node18",
        "node20",
        "nodenext",
    }

def _typescript_property_line_with_default(line: str, member: str) -> str:
    match = re.match(
        rf"^(?P<prefix>\s*(?:(?:public|private|protected)\s+)?(?:readonly\s+)?{re.escape(member)}\s*:\s*)(?P<type>[^;=]+)(?P<suffix>;?\s*)$",
        line,
    )
    if not match or "=" in line or "!" in line:
        return line
    ts_type = str(match.group("type") or "unknown").strip()
    return f"{match.group('prefix')}{ts_type} = {_typescript_default_value_for_type(ts_type)}{match.group('suffix')}"

def _typescript_default_value_for_type(ts_type: str) -> str:
    lowered = str(ts_type or "").strip().lower()
    if lowered == "string":
        return '""'
    if lowered == "number":
        return "0"
    if lowered == "boolean":
        return "false"
    if "[]" in lowered:
        return "[]"
    if lowered == "date":
        return "new Date(0)"
    return "undefined"

def _typescript_unwrap_phantom_call(line: str, missing_symbol: str) -> str:
    """Replace ``missing(expr)`` with ``expr`` when ``missing`` is undefined."""

    if not _TS_IDENTIFIER_RE.fullmatch(missing_symbol):
        return line
    pattern = re.compile(rf"\b{re.escape(missing_symbol)}\s*\(")
    match = pattern.search(line)
    if match is None:
        return line
    start = match.start()
    open_paren = match.end() - 1
    depth = 0
    end = -1
    for index in range(open_paren, len(line)):
        char = line[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end < 0:
        return line
    inner = line[open_paren + 1 : end].strip()
    if not inner:
        return line
    return line[:start] + inner + line[end + 1 :]

def _typescript_local_function_names(lines: Sequence[str]) -> list[str]:
    names: list[str] = []
    for line in lines:
        body = line.rstrip("\r\n")
        match = re.match(
            r"^\s*(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\(",
            body,
        )
        if match:
            names.append(str(match.group("name") or ""))
            continue
        match = re.match(
            r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
            r"(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>",
            body,
        )
        if match:
            names.append(str(match.group("name") or ""))
    return [name for name in names if _TS_IDENTIFIER_RE.fullmatch(name)]

def _typescript_best_local_function_alias(missing_symbol: str, candidates: Sequence[str]) -> str:
    """Pick a same-file helper whose name is a close alias of the missing symbol."""

    missing = str(missing_symbol or "").strip()
    if not missing or not candidates:
        return ""
    missing_core = missing.lstrip("_").lower()
    best = ""
    best_score = 0.0
    for candidate in candidates:
        cand = str(candidate or "").strip()
        if not cand or cand == missing:
            continue
        cand_core = cand.lstrip("_").lower()
        if not cand_core:
            continue
        score = 0.0
        if cand_core == missing_core:
            score = 1.0
        elif missing_core in cand_core or cand_core in missing_core:
            score = 0.85
        else:
            # Token-ish similarity: shared prefix length / max length.
            shared = 0
            for left, right in zip(missing_core, cand_core, strict=False):
                if left != right:
                    break
                shared += 1
            score = shared / max(len(missing_core), len(cand_core), 1)
            # Reward single-character drift in the middle (deltaMult ~ decayMult).
            if abs(len(missing_core) - len(cand_core)) <= 1 and shared >= 3:
                score = max(score, 0.7)
        if score > best_score:
            best_score = score
            best = cand
    return best if best_score >= 0.7 else ""

def _typescript_function_param_names_for_line(lines: Sequence[str], target_index: int) -> list[str]:
    for start_index in range(target_index, -1, -1):
        line_body = lines[start_index].rstrip("\r\n")
        match = _TS_FUNCTION_DECLARATION_LINE_RE.match(line_body) or _TS_ARROW_FUNCTION_DECLARATION_LINE_RE.match(
            line_body
        )
        if not match:
            continue
        if not _typescript_line_is_inside_scope(lines, start_index, target_index):
            continue
        return _parse_typescript_param_names(str(match.group("params") or ""))
    return []

def _typescript_line_is_inside_scope(lines: Sequence[str], start_index: int, target_index: int) -> bool:
    depth = 0
    for index in range(start_index, target_index + 1):
        line_body = lines[index].rstrip("\r\n")
        depth += line_body.count("{")
        depth -= line_body.count("}")
        if index < target_index and depth <= 0:
            return False
    return depth > 0

def _parse_typescript_param_names(params_text: str) -> list[str]:
    names: list[str] = []
    for raw_param in _split_typescript_params(params_text):
        param = raw_param.split("=", 1)[0].split(":", 1)[0].strip().removeprefix("...").strip()
        if _TS_IDENTIFIER_RE.fullmatch(param):
            names.append(param)
    return names

def _typescript_identifier_alias_matches(missing_symbol: str, candidate: str) -> bool:
    missing_lower = missing_symbol.lower()
    candidate_lower = candidate.lower()
    if not candidate_lower or missing_lower == candidate_lower:
        return False
    prefixes = ("new", "next", "updated", "current", "previous", "prev")
    return any(missing_lower == f"{prefix}{candidate_lower}" for prefix in prefixes)

def _parse_typescript_missing_test_global_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for diagnostic in diagnostics:
        for match in _TS_CANNOT_FIND_TEST_GLOBAL_RAW_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            path = _normalize_repair_path(str(match.group("file") or ""))
            symbol = str(match.group("symbol") or "")
            if path.endswith((".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx")) and symbol in _TS_TEST_GLOBAL_NAMES:
                parsed.append({"file": path, "symbol": symbol})
    return parsed

def _add_vitest_import_to_typescript_test(text: str, symbols: set[str]) -> str:
    requested = sorted(symbol for symbol in symbols if symbol in _TS_TEST_GLOBAL_NAMES)
    if not requested:
        return text
    match = _TS_VITEST_IMPORT_RE.search(text)
    if match:
        existing = {token.strip() for token in str(match.group("symbols") or "").split(",") if token.strip()}
        replacement = f"import {{ {', '.join(sorted(existing | set(requested)))} }} from 'vitest';"
        return text[: match.start()] + replacement + text[match.end() :]
    return f"import {{ {', '.join(requested)} }} from 'vitest';\n{text}"

def _prepend_typescript_vitest_import_operation(
    *,
    path: str,
    original: str,
    symbols: set[str],
) -> RepairOperation | None:
    requested = sorted(symbol for symbol in symbols if symbol in _TS_TEST_GLOBAL_NAMES)
    if not requested or not original:
        return None
    first_line_end = original.find("\n")
    if first_line_end < 0:
        span_start = 0
        span_end = len(original)
        expected = original
        replacement = f"import {{ {', '.join(requested)} }} from 'vitest';\n{original}"
    else:
        span_start = 0
        span_end = first_line_end + 1
        expected = original[:span_end]
        replacement = f"import {{ {', '.join(requested)} }} from 'vitest';\n{expected}"
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=span_start,
        span_end=span_end,
        expected=expected,
        replacement=replacement,
        before_hash=sha256_text(original),
        metadata={
            "repair_kind": "typescript_vitest_global_import",
            "symbols": tuple(requested),
            "prepend_import": True,
        },
    )

def _repair_typescript_multiline_dom_handle_declarations(
    text: str,
    symbols: set[str],
) -> tuple[str, list[str]]:
    guarded: list[str] = []
    declaration_re = re.compile(
        r"(?ms)^(?P<indent>\s*)(?P<kind>const|let|var)\s+"
        r"(?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
        r"(?P<source>(?:document\.(?:getElementById|querySelector)|"
        r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\.querySelector)"
        r"\s*\(.*?\)\s+as\s+(?P<type>[^;\n]*\bnull\b[^;\n]*)\s*;)"
    )

    def _replace(match: re.Match[str]) -> str:
        symbol = str(match.group("symbol") or "").strip()
        if symbols and symbol not in symbols:
            return match.group(0)
        source = str(match.group("source") or "")
        narrowed_source = re.sub(r"\s*\|\s*null\b", "", source)
        narrowed_source = re.sub(r"\bnull\s*\|\s*", "", narrowed_source)
        if narrowed_source == source:
            return match.group(0)
        guarded.append(symbol)
        declaration = f"{match.group('indent')}{match.group('kind')} {symbol} = {narrowed_source}"
        following = text[match.end() : match.end() + 240]
        if _typescript_nullable_guard_in_text_window(following, symbol):
            return declaration
        indent = str(match.group("indent") or "")
        return (
            f"{declaration}\n"
            f"{indent}if (!{symbol}) {{\n"
            f'{indent}  throw new Error("DOM element unavailable: {symbol}");\n'
            f"{indent}}}"
        )

    repaired = declaration_re.sub(_replace, text)
    return repaired, _dedupe_preserve_order(guarded)

def _typescript_nullable_guard_in_text_window(window: str, symbol: str) -> bool:
    compact = re.sub(r"\s+", "", window)
    return (
        f"if(!{symbol})" in compact
        or f"if({symbol}===null)" in compact
        or f"if({symbol}==null)" in compact
        or f"if(null==={symbol})" in compact
        or f"if(null=={symbol})" in compact
    )

def _function_body_end_offset(content: str, from_index: int) -> int | None:
    """Return exclusive end offset of a function body starting near ``from_index``."""

    brace = content.find("{", from_index)
    if brace < 0:
        # signature-only / overload ending at semicolon
        semi = content.find(";", from_index)
        return semi + 1 if semi >= 0 else None
    depth = 0
    in_squote = False
    in_dquote = False
    in_template = False
    escape = False
    i = brace
    while i < len(content):
        ch = content[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\" and (in_squote or in_dquote or in_template):
            escape = True
            i += 1
            continue
        if in_squote:
            if ch == "'":
                in_squote = False
            i += 1
            continue
        if in_dquote:
            if ch == '"':
                in_dquote = False
            i += 1
            continue
        if in_template:
            if ch == "`":
                in_template = False
            i += 1
            continue
        if ch == "'":
            in_squote = True
        elif ch == '"':
            in_dquote = True
        elif ch == "`":
            in_template = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None

def _strip_typescript_literal_type(value: str) -> str:
    normalized = str(value or "").strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {"'", '"'}:
        return normalized[1:-1]
    return normalized

def _is_number_to_function_argument(diagnostic: RepairDiagnostic) -> bool:
    message = str(diagnostic.message or diagnostic.raw or "").lower()
    if diagnostic.code.lower() == "typescript_ts2345" and "number" in message and "(n: number) => number" in message:
        return True
    return bool(_TS_NUMBER_TO_FUNCTION_ARGUMENT_RAW_RE.search(str(diagnostic.raw or diagnostic.message or "")))

def _has_number_to_function_argument_diagnostic(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    return any(_is_number_to_function_argument(diagnostic) for diagnostic in diagnostics)

def _diagnostic_targets_path(diagnostic: RepairDiagnostic, path: str) -> bool:
    normalized_path = _normalize_repair_path(str(diagnostic.path or ""))
    if normalized_path == path:
        return True
    return path in {
        _normalize_repair_path(str(match.group("file") or ""))
        for pattern in (
            _TS_MISSING_CLOSING_BRACE_RAW_RE,
            _TS_NUMBER_PROPERTY_CALL_RAW_RE,
            _TS_NUMBER_TO_STRING_ARGUMENT_RAW_RE,
            _TS_NUMBER_TO_FUNCTION_ARGUMENT_RAW_RE,
            _TS_READONLY_ASSIGNMENT_RAW_RE,
            _TS_SHORTHAND_PROPERTY_SCOPE_RAW_RE,
            _TS_STRING_LITERAL_SUGGESTION_RAW_RE,
            _TS_UNKNOWN_MEMBER_ACCESS_RAW_RE,
        )
        for match in pattern.finditer(str(diagnostic.raw or diagnostic.message or ""))
    }

def _property_call_is_near_columns(start: int, end: int, columns: set[int]) -> bool:
    if not columns:
        return True
    for column in columns:
        zero_based = max(0, column - 1)
        if start <= zero_based <= end:
            return True
        if zero_based < start and start - zero_based <= 120:
            return True
    return False

def _remove_shorthand_properties(line: str, properties: set[str]) -> tuple[str, tuple[str, ...]]:
    if not properties or "{" not in line or "}" not in line:
        return line, ()
    open_index = line.find("{")
    close_index = line.rfind("}")
    if close_index <= open_index:
        return line, ()
    inner = line[open_index + 1 : close_index]
    if "{" in inner or "}" in inner:
        return line, ()
    parts = [part.strip() for part in inner.split(",")]
    kept: list[str] = []
    removed: list[str] = []
    for part in parts:
        if not part:
            continue
        if part in properties and _TS_IDENTIFIER_RE.fullmatch(part):
            removed.append(part)
            continue
        kept.append(part)
    if not removed:
        return line, ()
    replacement_inner = f" {', '.join(kept)} " if kept else ""
    return f"{line[: open_index + 1]}{replacement_inner}{line[close_index:]}", tuple(sorted(removed))

def _column_is_near_span(column: int, span_start: int, span_end: int) -> bool:
    if column <= 0:
        return True
    zero_based = column - 1
    if span_start <= zero_based <= span_end:
        return True
    return zero_based < span_start and span_start - zero_based <= 120

def _line_mentions_assignment_property(lines: Sequence[str], line_number: int, prop: str) -> bool:
    line_index = line_number - 1
    if line_index < 0 or line_index >= len(lines):
        return False
    line = str(lines[line_index] or "")
    escaped = re.escape(prop)
    return bool(re.search(rf"(?:\.{escaped}\b|\[['\"]{escaped}['\"]\])\s*(?:[+\-*/%]?=|\+\+|--)", line))

def _wrap_typescript_argument_at_column_as_string(line: str, column: int) -> str:
    span = _find_typescript_argument_span_at_column(line, column)
    if span is None:
        return line
    start, end = span
    argument = line[start:end]
    stripped = argument.strip()
    if not stripped or stripped.startswith(("String(", '"', "'", "`")):
        return line
    leading = argument[: len(argument) - len(argument.lstrip())]
    trailing = argument[len(argument.rstrip()) :]
    replacement = f"{leading}String({stripped}){trailing}"
    return line[:start] + replacement + line[end:]

def _find_typescript_argument_span_at_column(line: str, column: int) -> tuple[int, int] | None:
    index = max(0, min(len(line), int(column) - 1))
    open_index = line.rfind("(", 0, index + 1)
    close_index = line.find(")", index)
    if open_index < 0 or close_index < 0 or close_index <= open_index:
        return None
    spans = _split_typescript_argument_spans(line, open_index + 1, close_index)
    for start, end in spans:
        if start <= index <= end:
            if "=>" in line[start:end]:
                return None
            return start, end
    return None

def _find_matching_paren(text: str, open_paren: int) -> int:
    if open_paren < 0 or open_paren >= len(text) or text[open_paren] != "(":
        return -1
    depth = 0
    quote = ""
    escaped = False
    for index in range(open_paren, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"', "`"}:
            quote = char
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1

def _split_typescript_argument_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    depth = 0
    arg_start = start
    quote = ""
    index = start
    while index < end:
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in {"'", '"', "`"}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            spans.append((arg_start, index))
            arg_start = index + 1
        index += 1
    if arg_start <= end:
        spans.append((arg_start, end))
    return spans

def _typescript_brace_balance_delta(source: str) -> int:
    depth = 0
    quote: str | None = None
    escaped = False
    line_comment = False
    block_comment = False
    index = 0
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
                index += 2
                continue
            index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char == "/" and next_char == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and next_char == "*":
            block_comment = True
            index += 2
            continue
        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    return depth

def _text_replace_operations_from_repair(
    *,
    path: str,
    original: str,
    repaired: str,
    metadata: Mapping[str, object],
) -> tuple[RepairOperation, ...]:
    before_hash = sha256_text(original)
    operations: list[RepairOperation] = []
    original_lines = original.splitlines(keepends=True)
    repaired_lines = repaired.splitlines(keepends=True)
    original_offsets = _line_start_offsets(original_lines)
    matcher = SequenceMatcher(a=original_lines, b=repaired_lines, autojunk=False)
    for tag, start_line, end_line, replacement_start, replacement_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        start = original_offsets[start_line]
        end = original_offsets[end_line]
        expected = "".join(original_lines[start_line:end_line])
        operation_metadata = dict(metadata)
        if not expected:
            operation_metadata["expected_context_before"] = "".join(original_lines[max(0, start_line - 2) : start_line])
            operation_metadata["expected_context_after"] = "".join(
                original_lines[start_line : min(len(original_lines), start_line + 2)]
            )
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=start,
                span_end=end,
                expected=expected,
                replacement="".join(repaired_lines[replacement_start:replacement_end]),
                before_hash=before_hash,
                metadata=operation_metadata,
            )
        )
    return tuple(operations)

def _typescript_base_files_have_dom_lib(base_files: Mapping[str, str]) -> bool:
    for path, content in base_files.items():
        basename = _normalize_repair_path(str(path or "")).rsplit("/", maxsplit=1)[-1].lower()
        if not basename.startswith("tsconfig") or not basename.endswith(".json"):
            continue
        payload = _json_object(str(content or ""))
        compiler_options = payload.get("compilerOptions")
        if not isinstance(compiler_options, dict):
            continue
        libs = compiler_options.get("lib")
        if isinstance(libs, list) and any(str(lib).strip().lower() == "dom" for lib in libs):
            return True
    return False

def _remove_typescript_local_dom_shims(text: str) -> tuple[str, tuple[str, ...]]:
    lines = str(text or "").splitlines(keepends=True)
    kept: list[str] = []
    removed: list[str] = []
    index = 0
    while index < len(lines):
        symbol = _typescript_local_dom_shim_start_symbol(lines[index])
        if symbol:
            end_index = _typescript_block_end(lines, index)
            removed.append(symbol)
            index = end_index
            continue
        kept.append(lines[index])
        index += 1
    if not removed:
        return str(text or ""), ()
    repaired = "".join(kept)
    repaired = re.sub(r"\n{3,}", "\n\n", repaired)
    return repaired, tuple(dict.fromkeys(removed))

def _typescript_local_dom_shim_start_symbol(line: str) -> str:
    declare_match = _TS_LOCAL_DOM_DECLARE_CONST_START_RE.match(line)
    if declare_match:
        return str(declare_match.group("name") or "").strip()
    interface_match = _TS_LOCAL_DOM_INTERFACE_START_RE.match(line)
    if interface_match:
        return str(interface_match.group("name") or "").strip()
    return ""

def _typescript_block_end(lines: Sequence[str], start_index: int) -> int:
    depth = 0
    saw_open = False
    for index in range(start_index, len(lines)):
        line = str(lines[index] or "")
        open_count = line.count("{")
        close_count = line.count("}")
        saw_open = saw_open or open_count > 0
        depth += open_count - close_count
        if saw_open and depth <= 0:
            return index + 1
    return start_index + 1

def _is_typescript_config_file(path: str) -> bool:
    normalized = _normalize_repair_path(path).lower()
    if not normalized:
        return False
    basename = normalized.rsplit("/", maxsplit=1)[-1]
    return basename.endswith((".config.ts", ".config.tsx"))

def _is_typescript_test_file(path: str) -> bool:
    normalized = _normalize_repair_path(path).lower()
    if not normalized:
        return False
    basename = normalized.rsplit("/", maxsplit=1)[-1]
    return "/__tests__/" in f"/{normalized}" or basename.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))

def _line_start_offsets(lines: Sequence[str]) -> list[int]:
    offsets: list[int] = [0]
    current = 0
    for line in lines:
        current += len(line)
        offsets.append(current)
    return offsets

def _to_positive_int(value: object) -> int:
    try:
        parsed = int(str(value or "0"))
    except ValueError:
        return 0
    return parsed if parsed > 0 else 0

def _dedupe_preserve_order(items: Sequence[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped

def _normalize_repair_path(path: str) -> str:
    return normalize_repair_path_strict(path)

def _is_typescript_comma_expected_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    if diagnostic.code == "typescript_return_object_property_semicolon":
        return True
    if diagnostic.code.lower() != "typescript_ts1005":
        return False
    message = diagnostic.message.lower()
    raw = diagnostic.raw.lower()
    return "expected" in message and "," in (message + raw)

def _typescript_file_looks_truncated(content: str) -> bool:
    text = str(content or "")
    if not text.strip():
        return False
    stripped = text.rstrip()
    if stripped.endswith(("}", ";", "*/", "*/\n")):
        # still check balance
        pass
    else:
        last = stripped.splitlines()[-1].strip() if stripped.splitlines() else ""
        if last and not last.endswith(("}", ";", ",", "{", ")", "]")):
            return True
        if last.endswith(("(", ",", "=", ":", "{")):
            return True
    depth_brace = text.count("{") - text.count("}")
    depth_paren = text.count("(") - text.count(")")
    return depth_brace > 0 or depth_paren > 0

def _typescript_object_freeze_assert_operation(
    *,
    path: str,
    content: str,
    line: int,
    type_name: str,
) -> RepairOperation | None:
    if not _TS_IDENTIFIER_RE.fullmatch(type_name):
        return None
    # Find Object.freeze({...}) near the diagnostic line and assert as type_name.
    pattern = re.compile(
        r"(Object\.freeze\s*\()(\{)([\s\S]*?)(\n\})\s*(\))",
        re.MULTILINE,
    )
    line_offsets = _text_line_start_offsets(content)
    line_start = line_offsets[line - 1] if 0 < line <= len(line_offsets) else 0
    best = None
    for match in pattern.finditer(content):
        if abs(match.start() - line_start) > 400 and not (match.start() <= line_start <= match.end()):
            continue
        if f"as {type_name}" in match.group(0):
            continue
        best = match
        break
    if best is None:
        # Fall back to first freeze assignment of this type on the const line.
        assign = re.search(
            rf"(export\s+const\s+\w+\s*:\s*{re.escape(type_name)}\s*=\s*Object\.freeze\s*\()(\{{)([\s\S]*?)(\n\}})\s*(\))",
            content,
        )
        if assign is None or f"as {type_name}" in assign.group(0):
            return None
        best = assign
    replacement = f"{best.group(1)}{best.group(2)}{best.group(3)}{best.group(4)} as {type_name}{best.group(5)}"
    # groups for first pattern: 1=Object.freeze(, 2={, 3=body, 4=\n}, 5=)
    if best.lastindex and best.lastindex >= 5:
        pass
    else:
        return None
    # Recompute groups for assign pattern which has 5 groups similarly
    if best.re.pattern.startswith("(export"):
        replacement = f"{best.group(1)}{best.group(2)}{best.group(3)}{best.group(4)} as {type_name}{best.group(5)}"
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=best.start(),
        span_end=best.end(),
        expected=best.group(0),
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_object_assign_assertion",
            "type_name": type_name,
            "diagnostic_line": line,
        },
    )

def _typescript_identifier_in_scope(content: str, name: str, *, line: int) -> bool:
    if not _TS_IDENTIFIER_RE.fullmatch(name):
        return False
    # Local const/let/var/param declaration of the suggested name before use.
    decl = re.compile(
        rf"\b(?:const|let|var)\s+{re.escape(name)}\b|"
        rf"\bfunction\s+\w+\s*\([^)]*\b{re.escape(name)}\b|"
        rf"\([^)]*\b{re.escape(name)}\s*:"
    )
    prefix = "\n".join(content.splitlines()[: max(0, line)])
    return bool(decl.search(prefix)) or bool(re.search(rf"\b{re.escape(name)}\b", prefix))

def _typescript_rename_identifier_at_diagnostic(
    *,
    path: str,
    content: str,
    line: int,
    column: int,
    actual: str,
    suggestion: str,
) -> RepairOperation | None:
    lines = content.splitlines(keepends=True)
    if line < 1 or line > len(lines):
        return None
    original_line = lines[line - 1]
    col_index = max(0, column - 1) if column > 0 else 0
    # Prefer exact column match of the bad identifier.
    symbol_re = re.compile(rf"(?<![\w$]){re.escape(actual)}(?![\w$])")
    matches = list(symbol_re.finditer(original_line))
    if not matches:
        return None
    selected = min(
        matches,
        key=lambda match: (
            0 if match.start() <= col_index <= match.end() else 1,
            min(abs(match.start() - col_index), abs(match.end() - col_index)),
        ),
    )
    repaired_line = original_line[: selected.start()] + suggestion + original_line[selected.end() :]
    if repaired_line == original_line:
        return None
    lines[line - 1] = repaired_line
    repaired = "".join(lines)
    return RepairOperation(
        kind="write_file",
        path=path,
        content=repaired,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_identifier_suggestion",
            "write_file_reason": "ts2552_did_you_mean",
            "actual": actual,
            "suggestion": suggestion,
            "line": line,
        },
    )

def _parse_typescript_argument_missing_props_diagnostic(
    diagnostic: RepairDiagnostic,
) -> tuple[str, int, int, str, str, list[str]] | None:
    raw = str(diagnostic.raw or diagnostic.message or "")
    path = _normalize_repair_path(str(diagnostic.path or ""))
    line = int(diagnostic.line or 0)
    col = int(diagnostic.column or 0)
    match = _TS2345_ARG_MISSING_PROPS_RE.search(raw)
    if match:
        path = path or _normalize_repair_path(str(match.group("file") or ""))
        line = line or int(match.group("line") or 0)
        col = col or int(match.group("col") or 0)
        source_type = str(match.group("source") or "").strip()
        target_shape = str(match.group("target") or "").strip()
        props_raw = str(match.group("props") or "")
    else:
        code = str(diagnostic.code or "").lower()
        if code not in {"typescript_ts2345", "ts2345"} and "argument of type" not in raw.lower():
            return None
        loose = _TS2345_ARG_MISSING_PROPS_LOOSE_RE.search(raw)
        if loose is None or not path or line <= 0:
            return None
        source_type = str(loose.group("source") or "").strip()
        target_shape = str(loose.group("target") or "").strip()
        props_raw = ""
        clause = _TS2345_MISSING_PROPS_CLAUSE_RE.search(raw)
        if clause:
            props_raw = str(clause.group("props") or "")
    if not props_raw:
        # Infer property names from anonymous target shape.
        props_raw = ", ".join(m.group("name") for m in _TS_ANON_OBJECT_PROP_RE.finditer(target_shape))
    props = [token.strip() for token in props_raw.split(",") if _TS_IDENTIFIER_RE.fullmatch(token.strip() or "")]
    if not path or line <= 0 or not props or not target_shape.startswith("{"):
        return None
    return path, line, col, source_type, target_shape, props

def _typescript_extract_argument_expression(line: str, col_index: int) -> str:
    text = str(line or "")
    if not text:
        return ""
    # Walk outward from column to capture identifier / member / call chain.
    start = min(max(0, col_index), len(text) - 1)
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] in "._$"):
        start -= 1
    end = start
    depth_paren = 0
    depth_brace = 0
    depth_bracket = 0
    while end < len(text):
        ch = text[end]
        if ch == "(":
            depth_paren += 1
        elif ch == ")":
            if depth_paren == 0:
                break
            depth_paren -= 1
        elif ch == "{":
            depth_brace += 1
        elif ch == "}":
            if depth_brace == 0:
                break
            depth_brace -= 1
        elif ch == "[":
            depth_bracket += 1
        elif ch == "]":
            if depth_bracket == 0:
                break
            depth_bracket -= 1
        elif ch in {",", ";"} and depth_paren == 0 and depth_brace == 0 and depth_bracket == 0:
            break
        end += 1
    expr = text[start:end].strip()
    # Reject empty or keyword-only tokens.
    if not expr or expr in {"if", "return", "const", "let", "var"}:
        return ""
    return expr

def _typescript_type_field_names(type_name: str, base_files: Mapping[str, str]) -> set[str]:
    name = str(type_name or "").strip()
    if not _TS_IDENTIFIER_RE.fullmatch(name):
        return set()
    fields: set[str] = set()
    header = re.compile(
        rf"(?:export\s+)?(?:interface|type)\s+{re.escape(name)}\b[^{{=]*=?\s*\{{",
    )
    for content in base_files.values():
        text = str(content or "")
        for match in header.finditer(text):
            open_brace = text.find("{", match.start())
            if open_brace < 0:
                continue
            close = _typescript_matching_brace_index(text, open_brace)
            if close < 0:
                continue
            body = text[open_brace + 1 : close]
            for prop in re.finditer(
                r"(?m)^\s*(?:readonly\s+)?(?P<prop>[A-Za-z_$][\w$]*)\s*[?:]",
                body,
            ):
                fields.add(str(prop.group("prop") or ""))
    return fields

def _parse_typescript_missing_props_diagnostic(
    diagnostic: RepairDiagnostic,
) -> tuple[str, int, int, str, list[str]] | None:
    raw = str(diagnostic.raw or diagnostic.message or "")
    code = str(diagnostic.code or "").lower()
    path = _normalize_repair_path(str(diagnostic.path or ""))
    line = int(diagnostic.line or 0)
    col = int(diagnostic.column or 0)
    primary = _TS_MISSING_PROPS_PRIMARY_RE.search(raw)
    if primary:
        path = path or _normalize_repair_path(str(primary.group("file") or ""))
        line = line or int(primary.group("line") or 0)
        col = col or int(primary.group("col") or 0)
    props_match = _TS_MISSING_PROPS_FROM_TYPE_RE.search(raw)
    if props_match is None and code not in {"typescript_ts2345", "typescript_ts2739"}:
        return None
    if props_match is None:
        return None
    type_name = str(props_match.group("type") or "")
    props = [
        token.strip()
        for token in str(props_match.group("props") or "").split(",")
        if _TS_IDENTIFIER_RE.fullmatch(token.strip() or "")
    ]
    if not path or line <= 0 or not props:
        return None
    return path, line, col, type_name, props

def _typescript_types_with_named_properties(
    base_files: Mapping[str, str],
) -> dict[str, tuple[str, ...]]:
    """Map property name → types/interfaces that declare it."""

    owners: dict[str, list[str]] = {}
    type_header = re.compile(
        r"(?:export\s+)?(?:interface|type)\s+(?P<name>[A-Za-z_$][\w$]*)\b[^{=]*=?\s*\{",
    )
    for content in base_files.values():
        text = str(content or "")
        for header in type_header.finditer(text):
            type_name = str(header.group("name") or "")
            open_brace = text.find("{", header.start())
            if open_brace < 0:
                continue
            close = _typescript_matching_brace_index(text, open_brace)
            if close < 0:
                continue
            body = text[open_brace + 1 : close]
            for prop_match in re.finditer(
                r"(?m)^\s*(?:readonly\s+)?(?P<prop>[A-Za-z_$][\w$]*)\s*[?:]",
                body,
            ):
                prop = str(prop_match.group("prop") or "")
                if prop and type_name:
                    owners.setdefault(prop, []).append(type_name)
    return {key: tuple(dict.fromkeys(values)) for key, values in owners.items()}

def _typescript_param_type_from_property_operation(
    *,
    path: str,
    content: str,
    line: int,
    prop: str,
    candidate_types: Sequence[str],
) -> RepairOperation | None:
    lines = content.splitlines(keepends=True)
    # Find receiver of .prop on diagnostic line
    if line < 1 or line > len(lines):
        return None
    use_line = lines[line - 1]
    receiver_match = re.search(rf"\b([A-Za-z_$][\w$]*)\s*\.\s*{re.escape(prop)}\b", use_line)
    if receiver_match is None:
        return None
    receiver = str(receiver_match.group(1) or "")
    # Search enclosing function signature upward
    for idx in range(line - 1, max(-1, line - 80), -1):
        if idx < 0:
            continue
        text = lines[idx]
        if "function" not in text and "=>" not in text and "(" not in text:
            continue
        # multi-line signatures: join a small window
        window = "".join(lines[idx : min(len(lines), idx + 6)])
        param_re = re.compile(rf"([,(]\s*)({re.escape(receiver)})\s*:\s*(number|string|boolean)\b")
        param_match = param_re.search(window)
        if param_match is None:
            continue
        # Prefer candidate whose name relates to receiver (Humidity ~ humidityPercent)
        chosen = ""
        receiver_l = receiver.lower()
        for cand in candidate_types:
            if cand.lower() in receiver_l or receiver_l.replace("percent", "").replace("value", "") in cand.lower():
                chosen = cand
                break
        if not chosen and len(candidate_types) == 1:
            chosen = candidate_types[0]
        if not chosen:
            # Prefer types imported or declared in this file
            for cand in candidate_types:
                if re.search(rf"\b{re.escape(cand)}\b", content):
                    chosen = cand
                    break
        if not chosen:
            return None
        new_param = f"{param_match.group(1)}{param_match.group(2)}: {chosen}"
        window_start = sum(len(item) for item in lines[:idx])
        span_start = window_start + param_match.start()
        span_end = window_start + param_match.end()
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=span_start,
            span_end=span_end,
            expected=content[span_start:span_end],
            replacement=new_param,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_param_object_property",
                "parameter": receiver,
                "property": prop,
                "type_name": chosen,
                "diagnostic_line": line,
            },
        )
    return None

def _typescript_ensure_named_type_import_operation(
    *,
    path: str,
    content: str,
    type_name: str,
    base_files: Mapping[str, str],
) -> RepairOperation | None:
    """Insert ``type_name`` into an existing relative import when missing."""

    if not _TS_IDENTIFIER_RE.fullmatch(type_name):
        return None
    if re.search(rf"\bimport\s*{{[^}}]*\b{re.escape(type_name)}\b", content):
        return None
    # Prefer import from ./models when present (common L1 shape).
    for module in ("./models", "../models"):
        match = re.search(
            rf"(import\s*{{)([^}}]+)(}}\s*from\s*['\"]{re.escape(module)}['\"]\s*;)",
            content,
        )
        if match is None:
            continue
        clause = str(match.group(2) or "")
        if type_name in {part.strip().split(" as ")[0].strip() for part in clause.split(",")}:
            return None
        new_clause = clause.rstrip()
        if new_clause and not new_clause.endswith(","):
            new_clause = f"{new_clause}, "
        else:
            new_clause = f"{new_clause} "
        new_clause = f"{new_clause}{type_name}"
        replacement = f"{match.group(1)}{new_clause}{match.group(3)}"
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=match.start(),
            span_end=match.end(),
            expected=match.group(0),
            replacement=replacement,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_param_object_property_import",
                "type_name": type_name,
                "module": module,
            },
        )
    # Locate declaration file for type_name and insert a new import.
    source_path = ""
    for rel, text in base_files.items():
        if re.search(rf"(?:export\s+)?(?:interface|type|class)\s+{re.escape(type_name)}\b", str(text or "")):
            source_path = rel
            break
    if not source_path or source_path == path:
        return None
    # relative import from path → source_path
    from_dir = PurePosixPath(path).parent
    target = PurePosixPath(source_path)
    rel = posixpath.relpath(target.as_posix(), from_dir.as_posix() if str(from_dir) != "." else ".")
    if not rel.startswith("."):
        rel = f"./{rel}"
    if rel.endswith(".ts"):
        rel = rel[:-3]
    insert = f'import {{ {type_name} }} from "{rel}";\n'
    # after last import
    last_import = None
    for m in re.finditer(r"(?m)^import\s.+;\s*$", content):
        last_import = m
    if last_import is not None:
        span_start = last_import.end()
        if not content[span_start : span_start + 1].startswith("\n"):
            insert = "\n" + insert
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=span_start,
            span_end=span_start,
            expected="",
            replacement=insert if content[span_start:].startswith("\n") else "\n" + insert,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_param_object_property_import",
                "type_name": type_name,
                "module": rel,
            },
        )
    return None

__all__ = (
    "_typescript_diagnostic_line",
    "_typescript_global_guard_precedes",
    "_typescript_enum_has_runtime_member_access",
    "_functions_accepting_type",
    "_pick_function_alias",
    "_rewrite_named_import_binding_lines",
    "_package_json_enable_node_test_script_operation",
    "_typescript_syntax_error_paths",
    "_line_ending",
    "_normalized_base_files",
    "_repair_plan_or_none",
    "_diagnostic_text",
    "_json_object",
    "_typescript_glob_points_outside_root",
    "_parse_html_truncated_entrypoint_paths",
    "_repair_html_entrypoint_quality_text_with_metadata",
    "_common_prefix_len",
    "_html_javascript_entrypoint_for_typescript_source",
    "_html_compiled_javascript_entrypoint_for_script",
    "_html_compiled_typescript_output_path",
    "_html_typescript_compiler_option",
    "_javascript_annotation_candidate_paths",
    "_strip_typescript_annotations_from_javascript",
    "_strip_javascript_callable_type_match",
    "_parse_undeclared_runtime_import_paths",
    "_normalize_ts_class_field_initialization",
    "_normalize_typeorm_detached_field_type",
    "_parse_typescript_missing_member_errors",
    "_parse_typescript_object_missing_member_errors",
    "_parse_typescript_unused_declaration_errors",
    "_typescript_unused_parameter_operations",
    "_typescript_unused_declaration_item_key",
    "_typescript_unused_named_import_binding_group_operations",
    "_typescript_unused_import_declaration_operation",
    "_typescript_unused_named_import_binding_operation",
    "_typescript_line_is_import_binding_context",
    "_remove_typescript_named_import_binding",
    "_remove_typescript_named_import_bindings",
    "_remove_typescript_multiline_named_import_bindings",
    "_typescript_named_import_local_name",
    "_typescript_unused_local_declaration_operation",
    "_typescript_unused_function_declaration_operation",
    "_typescript_unused_local_expression_requires_binding",
    "_typescript_unused_parameter_operation",
    "_typescript_unused_parameter_line_replacement",
    "_typescript_unused_multiline_parameter_line_replacement",
    "_typescript_identifier_occurrence_is_parameter",
    "_typescript_identifier_occurrence_has_parameter_shape",
    "_typescript_identifier_occurrence_is_in_multiline_parameter_list",
    "_typescript_object_literal_missing_member_operations",
    "_typescript_declaration_type_name",
    "_typescript_inline_object_missing_member_operations",
    "_typescript_inline_object_shape_members",
    "_typescript_inline_object_type_member_operation",
    "_typescript_inline_array_object_type_braces",
    "_typescript_inline_object_literal_member_operation",
    "_typescript_object_literal_member_names",
    "_typescript_insert_object_member_operation",
    "_typescript_member_insert_indent",
    "_typescript_receiver_for_member_access",
    "_typescript_type_declaration_body",
    "_typescript_existing_member_names_for_type",
    "_typescript_method_delegate_specs",
    "_typescript_normalized_parameter_list",
    "_typescript_parameter_argument_list",
    "_typescript_interface_method_return_operations",
    "_typescript_object_literal_method_implementation_operation",
    "_typescript_object_literal_required_properties_operation",
    "_typescript_declared_member_types_for_type",
    "_typescript_object_literal_bounds_near_line",
    "_typescript_matching_brace_index",
    "_typescript_line_invokes_constructor",
    "_typescript_exported_private_constructor_modifier_span",
    "_typescript_safe_structural_member_type",
    "_typescript_default_value_for_required_property_type",
    "_text_line_start_offsets",
    "_line_index_for_offset",
    "_line_text_replace_operation",
    "_typescript_symbol_is_constructed",
    "_typescript_symbol_is_called",
    "_typescript_symbol_has_named_constructor_binding",
    "_typescript_symbol_has_field_constructor_binding",
    "_typescript_methods_used_on_constructed_symbol",
    "_typescript_module_declares_symbol",
    "_typescript_module_declared_symbol_kind",
    "_find_typescript_similar_runtime_declaration",
    "_normalize_typescript_identifier_for_similarity",
    "_typescript_declared_type_kind",
    "_normalize_typescript_module_ref",
    "_apply_single_text_operation",
    "_typescript_line_at",
    "_typescript_member_usage_is_call",
    "_typescript_missing_member_declared_type",
    "_typescript_usage_compatible_member_type",
    "_typescript_member_name_suggests_string",
    "_typescript_member_name_strongly_suggests_string",
    "_typescript_member_name_suggests_number",
    "_add_typescript_member_operation",
    "_add_typescript_members_operation",
    "_order_type_declaration_paths_for_usage",
    "_extend_typescript_declare_const_type_literal_operation",
    "_typescript_constructor_default_arguments",
    "_resolve_relative_ts_module_path",
    "_repair_typescript_unresolved_identifier_import",
    "_parse_named_import_symbols",
    "_typescript_named_import_symbols_with_added_symbol",
    "_typescript_imported_const_class_alias_available",
    "_typescript_module_exports_const_class_alias",
    "_typescript_duplicate_identifier_targets",
    "_typescript_string_brand_type_sources",
    "_typescript_type_only_value_usage_symbol",
    "_typescript_type_value_dot_member",
    "_typescript_file_has_type_name_import",
    "_typescript_insert_type_import_operation",
    "_typescript_import_insert_offset",
    "_typescript_named_type_specifier_names",
    "_typescript_named_value_specifier_names",
    "_typescript_named_value_specifier_name",
    "_typescript_named_export_specifier_name",
    "_typescript_named_specifier_indent",
    "_typescript_module_exports_symbol",
    "_typescript_module_exports_symbol_resolved",
    "_parse_typescript_cannot_find_name_errors",
    "_typescript_missing_identifier_usage_is_type_position",
    "_resolve_case_variant_base_file",
    "_relative_import_specifier_for_actual_path",
    "_relative_import_suffix_order",
    "_typescript_import_pairs_from_clause",
    "_typescript_identifier_used_outside_span",
    "_too_many_arguments_declaration_operation",
    "_too_many_arguments_callsite_trim_operation",
    "_too_many_arguments_declaration_expand_operation",
    "_typescript_function_param_names_from_header",
    "_typescript_select_args_for_params",
    "_find_unique_typescript_function_declaration_multiline",
    "_insert_unknown_params_into_callable_header",
    "_expand_multiline_function_params",
    "_typescript_call_name_from_usage_line",
    "_find_unique_typescript_method_declaration",
    "_find_unique_typescript_function_declaration",
    "_add_rest_param_to_typescript_callable",
    "_add_defaults_to_typescript_method_params",
    "_split_typescript_params",
    "_typescript_param_with_default",
    "_typescript_errors_require_dom_lib",
    "_typescript_errors_require_import_meta_module",
    "_typescript_errors_require_es2021_lib",
    "_typescript_libs_allow_es2021",
    "_typescript_promote_libs_to_es2021",
    "_typescript_module_allows_import_meta",
    "_typescript_property_line_with_default",
    "_typescript_default_value_for_type",
    "_typescript_unwrap_phantom_call",
    "_typescript_local_function_names",
    "_typescript_best_local_function_alias",
    "_typescript_function_param_names_for_line",
    "_typescript_line_is_inside_scope",
    "_parse_typescript_param_names",
    "_typescript_identifier_alias_matches",
    "_parse_typescript_missing_test_global_errors",
    "_add_vitest_import_to_typescript_test",
    "_prepend_typescript_vitest_import_operation",
    "_repair_typescript_multiline_dom_handle_declarations",
    "_typescript_nullable_guard_in_text_window",
    "_function_body_end_offset",
    "_strip_typescript_literal_type",
    "_is_number_to_function_argument",
    "_has_number_to_function_argument_diagnostic",
    "_diagnostic_targets_path",
    "_property_call_is_near_columns",
    "_remove_shorthand_properties",
    "_column_is_near_span",
    "_line_mentions_assignment_property",
    "_wrap_typescript_argument_at_column_as_string",
    "_find_typescript_argument_span_at_column",
    "_find_matching_paren",
    "_split_typescript_argument_spans",
    "_typescript_brace_balance_delta",
    "_text_replace_operations_from_repair",
    "_typescript_base_files_have_dom_lib",
    "_remove_typescript_local_dom_shims",
    "_typescript_local_dom_shim_start_symbol",
    "_typescript_block_end",
    "_is_typescript_config_file",
    "_is_typescript_test_file",
    "_line_start_offsets",
    "_to_positive_int",
    "_dedupe_preserve_order",
    "_normalize_repair_path",
    "_is_typescript_comma_expected_diagnostic",
    "_typescript_file_looks_truncated",
    "_typescript_object_freeze_assert_operation",
    "_typescript_identifier_in_scope",
    "_typescript_rename_identifier_at_diagnostic",
    "_parse_typescript_argument_missing_props_diagnostic",
    "_typescript_extract_argument_expression",
    "_typescript_type_field_names",
    "_parse_typescript_missing_props_diagnostic",
    "_typescript_types_with_named_properties",
    "_typescript_param_type_from_property_operation",
    "_typescript_ensure_named_type_import_operation",
)
