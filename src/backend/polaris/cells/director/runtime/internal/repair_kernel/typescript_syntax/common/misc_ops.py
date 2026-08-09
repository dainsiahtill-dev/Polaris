from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from typing import Any

from ...contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ...javascript_syntax import repair_javascript_export_contract_placeholders
from ...path_files import normalize_base_files_strict, normalize_repair_path_strict
from ..constants import *  # noqa: F403
from .path_ops import *  # noqa: F403
from .plan_ops import *  # noqa: F403
from .parse_ops import *  # noqa: F403

"""Shared TypeScript repair helpers: misc_ops."""

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

def _json_object(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(str(text or "{}"))
    except json.JSONDecodeError:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}

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

def _text_line_start_offsets(text: str) -> list[int]:
    offsets = [0]
    for match in re.finditer(r"\n", str(text or "")):
        offsets.append(match.end())
    return offsets

def _typescript_symbol_is_called(text: str, symbol: str) -> bool:
    token = str(text or "")
    call_re = re.compile(rf"(?<!new\s)\b{re.escape(symbol)}\s*\(")
    return bool(call_re.search(token))

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

def _split_typescript_params(params_text: str) -> list[str]:
    spans = _split_typescript_argument_spans(params_text, 0, len(params_text))
    return [params_text[start:end].strip() for start, end in spans if params_text[start:end].strip()]

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

def _typescript_identifier_alias_matches(missing_symbol: str, candidate: str) -> bool:
    missing_lower = missing_symbol.lower()
    candidate_lower = candidate.lower()
    if not candidate_lower or missing_lower == candidate_lower:
        return False
    prefixes = ("new", "next", "updated", "current", "previous", "prev")
    return any(missing_lower == f"{prefix}{candidate_lower}" for prefix in prefixes)

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


__all__ = (
    "_functions_accepting_type",
    "_pick_function_alias",
    "_package_json_enable_node_test_script_operation",
    "_json_object",
    "_javascript_annotation_candidate_paths",
    "_strip_typescript_annotations_from_javascript",
    "_normalize_ts_class_field_initialization",
    "_normalize_typeorm_detached_field_type",
    "_typescript_matching_brace_index",
    "_text_line_start_offsets",
    "_typescript_symbol_is_called",
    "_typescript_module_declares_symbol",
    "_typescript_module_declared_symbol_kind",
    "_find_typescript_similar_runtime_declaration",
    "_normalize_typescript_identifier_for_similarity",
    "_typescript_declared_type_kind",
    "_extend_typescript_declare_const_type_literal_operation",
    "_resolve_relative_ts_module_path",
    "_typescript_duplicate_identifier_targets",
    "_typescript_string_brand_type_sources",
    "_typescript_type_only_value_usage_symbol",
    "_typescript_missing_identifier_usage_is_type_position",
    "_resolve_case_variant_base_file",
    "_typescript_import_pairs_from_clause",
    "_typescript_identifier_used_outside_span",
    "_typescript_call_name_from_usage_line",
    "_find_unique_typescript_method_declaration",
    "_split_typescript_params",
    "_typescript_libs_allow_es2021",
    "_typescript_promote_libs_to_es2021",
    "_typescript_property_line_with_default",
    "_typescript_default_value_for_type",
    "_typescript_unwrap_phantom_call",
    "_typescript_local_function_names",
    "_typescript_best_local_function_alias",
    "_typescript_identifier_alias_matches",
    "_function_body_end_offset",
    "_strip_typescript_literal_type",
    "_property_call_is_near_columns",
    "_column_is_near_span",
    "_line_mentions_assignment_property",
    "_split_typescript_argument_spans",
    "_typescript_brace_balance_delta",
    "_line_start_offsets",
    "_to_positive_int",
    "_dedupe_preserve_order",
    "_normalize_repair_path",
    "_typescript_file_looks_truncated",
    "_typescript_object_freeze_assert_operation",
    "_typescript_identifier_in_scope",
    "_typescript_rename_identifier_at_diagnostic",
    "_typescript_type_field_names",
    "_typescript_types_with_named_properties",
)
