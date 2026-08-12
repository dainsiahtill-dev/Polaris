"""missing_methods domain for JavaScript/Node syntax repairs."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from ..contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ._shared import (
    _base_file_from_runtime_path,
    _dedupe_diagnostics,
    _find_matching_brace,
    _normalize_base_files,
    _resolve_js_module,
)
from .constants import (
    _JS_CLASS_RE_TEMPLATE,
    _JS_CONSTRUCTOR_REQUIRES_FIELD_RE,
    _JS_CONSTRUCTOR_STRING_CONTRACT_RE,
    _JS_IDENTIFIER_RE,
    _JS_METHOD_RE,
    _JS_MISSING_METHOD_RUNTIME_RE,
    _JS_MISSING_METHOD_RUNTIME_STACK_RE,
    JAVASCRIPT_MISSING_METHOD_RUNTIME_SOURCE_TOOL,
)


def build_javascript_missing_method_runtime_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Add a conservative class method alias for traceable JS TypeError failures."""

    normalized_base = _normalize_base_files(base_files)
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    seen: set[tuple[str, str]] = set()
    for diagnostic in diagnostics:
        for failure in _missing_method_failures(diagnostic, normalized_base):
            entry_path = failure["file"]
            entry_text = normalized_base.get(entry_path, "")
            class_name = _infer_constructed_class(entry_text, failure["object"])
            if not class_name:
                class_name = _infer_iterated_imported_class(entry_text, failure["object"])
            if not class_name:
                continue
            class_path = _resolve_imported_class_path(normalized_base, entry_path, entry_text, class_name)
            if not class_path:
                class_path = entry_path if _class_declared_in_text(entry_text, class_name) else ""
            if not class_path:
                continue
            key = (class_path, failure["member"])
            if key in seen:
                continue
            seen.add(key)
            class_text = normalized_base.get(class_path)
            if class_text is None:
                continue
            operation = _missing_method_alias_operation(
                base_files=normalized_base,
                path=class_path,
                text=class_text,
                entry_text=entry_text,
                class_name=class_name,
                object_name=failure["object"],
                missing_member=failure["member"],
                call_arguments=failure.get("arguments") or "",
                diagnostic=diagnostic,
            )
            if operation is None:
                continue
            operations.append(operation)
            matched.append(diagnostic)
        for failure in _constructor_contract_failures(diagnostic, normalized_base):
            class_path = failure["file"]
            class_text = normalized_base.get(class_path)
            if class_text is None:
                continue
            operation = _constructor_contract_operation(
                base_files=normalized_base,
                path=class_path,
                text=class_text,
                class_name=failure["class_name"],
                required_field=failure["field"],
                diagnostic=diagnostic,
            )
            if operation is None:
                continue
            operations.append(operation)
            matched.append(diagnostic)
    if not operations:
        return None
    return RepairPlan(
        rule_id="javascript.missing_method_runtime",
        source_tool=JAVASCRIPT_MISSING_METHOD_RUNTIME_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=_dedupe_diagnostics(matched),
        mode=mode,
        risk_level="medium",
        priority=1,
        metadata={
            "runtime_plan_scope": "single_class_single_existing_method_alias_only",
            "unsafe_cases_fail_closed": True,
        },
    )


def _missing_method_failures(
    diagnostic: RepairDiagnostic,
    base_files: Mapping[str, str],
) -> tuple[dict[str, str], ...]:
    raw = str(diagnostic.raw or diagnostic.message or "")
    failures: list[dict[str, str]] = []
    for pattern in (_JS_MISSING_METHOD_RUNTIME_RE, _JS_MISSING_METHOD_RUNTIME_STACK_RE):
        for match in pattern.finditer(raw):
            rel_file = _base_file_from_runtime_path(str(match.group("file") or ""), base_files)
            obj = str(match.group("object") or "")
            member = str(match.group("member") or "")
            if rel_file and _JS_IDENTIFIER_RE.match(obj) and _JS_IDENTIFIER_RE.match(member):
                failures.append(
                    {
                        "file": rel_file,
                        "object": obj,
                        "member": member,
                        "arguments": _missing_method_call_arguments(raw, obj, member),
                    }
                )
    return tuple({(item["file"], item["object"], item["member"]): item for item in failures}.values())


def _constructor_contract_failures(
    diagnostic: RepairDiagnostic,
    base_files: Mapping[str, str],
) -> tuple[dict[str, str], ...]:
    raw = str(diagnostic.raw or diagnostic.message or "")
    failures: list[dict[str, str]] = []
    for pattern in (_JS_CONSTRUCTOR_STRING_CONTRACT_RE, _JS_CONSTRUCTOR_REQUIRES_FIELD_RE):
        for match in pattern.finditer(raw):
            rel_file = _base_file_from_runtime_path(str(match.group("file") or ""), base_files)
            class_name = str(match.group("class_name") or "")
            field = str(match.group("field") or "")
            if rel_file and _JS_IDENTIFIER_RE.match(class_name) and _JS_IDENTIFIER_RE.match(field):
                failures.append({"file": rel_file, "class_name": class_name, "field": field})
    return tuple({(item["file"], item["class_name"], item["field"]): item for item in failures}.values())


def _infer_constructed_class(entry_text: str, object_name: str) -> str:
    escaped = re.escape(object_name)
    match = re.search(
        rf"(?:const|let|var)\s+{escaped}\s*=\s*new\s+(?P<class>[A-Za-z_$][\w$]*)\s*\(",
        entry_text,
    )
    return str(match.group("class") or "") if match else ""


def _infer_iterated_imported_class(entry_text: str, object_name: str) -> str:
    if not _JS_IDENTIFIER_RE.match(object_name):
        return ""
    match = re.search(
        rf"for\s*\(\s*(?:const|let|var)\s+{re.escape(object_name)}\s+of\s+this\.(?P<collection>[A-Za-z_$][\w$]*)\s*\)",
        entry_text,
    )
    if not match:
        return ""
    imported_classes = _imported_class_names(entry_text)
    if not imported_classes:
        return ""
    candidates = {
        _upper_camel_identifier(object_name),
        _upper_camel_identifier(_singularize_js_identifier(object_name)),
        _upper_camel_identifier(_singularize_js_identifier(str(match.group("collection") or ""))),
    }
    matches = [name for name in imported_classes if name in candidates]
    return matches[0] if len(matches) == 1 else ""


def _imported_class_names(entry_text: str) -> tuple[str, ...]:
    names: list[str] = []
    for match in re.finditer(r"import\s+\{(?P<names>[^}]+)\}\s+from\s+['\"][^'\"]+['\"]", entry_text):
        for item in str(match.group("names") or "").split(","):
            token = item.strip()
            if " as " in token:
                token = token.rsplit(" as ", 1)[-1].strip()
            if _JS_IDENTIFIER_RE.match(token):
                names.append(token)
    for match in re.finditer(r"import\s+(?P<name>[A-Z][A-Za-z0-9_$]*)\s+from\s+['\"][^'\"]+['\"]", entry_text):
        name = str(match.group("name") or "")
        if _JS_IDENTIFIER_RE.match(name):
            names.append(name)
    return tuple(dict.fromkeys(names))


def _singularize_js_identifier(identifier: str) -> str:
    text = str(identifier or "").strip()
    if len(text) > 3 and text.endswith("ies"):
        return text[:-3] + "y"
    if len(text) > 1 and text.endswith("s"):
        return text[:-1]
    return text


def _pluralize_js_identifier(identifier: str) -> str:
    text = str(identifier or "").strip()
    if not text:
        return text
    if text.endswith("y"):
        return f"{text[:-1]}ies"
    if text.endswith("s"):
        return text
    return f"{text}s"


def _upper_camel_identifier(identifier: str) -> str:
    text = str(identifier or "").strip()
    if not text:
        return ""
    return text[:1].upper() + text[1:]


def _resolve_imported_class_path(
    base_files: Mapping[str, str],
    entry_path: str,
    entry_text: str,
    class_name: str,
) -> str:
    escaped = re.escape(class_name)
    patterns = (
        rf"import\s+{escaped}\s+from\s+['\"](?P<module>\.[^'\"]+)['\"]",
        rf"import\s+\{{[^}}]*\b{escaped}\b[^}}]*\}}\s+from\s+['\"](?P<module>\.[^'\"]+)['\"]",
        rf"(?:const|let|var)\s+{escaped}\s*=\s*require\(['\"](?P<module>\.[^'\"]+)['\"]\)",
    )
    for pattern in patterns:
        match = re.search(pattern, entry_text)
        if match:
            return _resolve_js_module(base_files, entry_path, str(match.group("module") or ""))
    return ""


def _class_declared_in_text(text: str, class_name: str) -> bool:
    return re.search(_JS_CLASS_RE_TEMPLATE.format(class_name=re.escape(class_name)), text) is not None


def _missing_method_alias_operation(
    *,
    base_files: Mapping[str, str],
    path: str,
    text: str,
    entry_text: str,
    class_name: str,
    object_name: str,
    missing_member: str,
    call_arguments: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    class_match = re.search(_JS_CLASS_RE_TEMPLATE.format(class_name=re.escape(class_name)), text)
    if not class_match:
        return None
    class_end = _find_matching_brace(text, class_match.end() - 1)
    if class_end is None:
        return None
    class_body = text[class_match.end() : class_end]
    existing_methods = [
        match.group("name") for match in _JS_METHOD_RE.finditer(class_body) if match.group("name") != "constructor"
    ]
    existing_methods = list(dict.fromkeys(existing_methods))
    constructor_object_keys = _constructor_object_keys_for_class(base_files, class_name)
    class_body, constructor_fields = _augment_constructor_from_object_keys(
        class_body,
        constructor_object_keys,
    )
    call_sites = _missing_method_call_sites(entry_text, object_name)
    if missing_member not in {site["member"] for site in call_sites}:
        call_sites = ({"member": missing_member, "arguments": call_arguments}, *call_sites)
    method_replacements: list[str] = []
    aliased_methods: list[str] = []
    selected_existing_methods: list[str] = []
    for call_site in call_sites:
        member = call_site["member"]
        if member in aliased_methods or re.search(rf"(?m)^\s+{re.escape(member)}\s*\(", text):
            continue
        alias_args = _alias_arguments_from_call_arguments(call_site.get("arguments", ""), member)
        expected_fields = _expected_return_fields_for_call(entry_text, object_name, member)
        add_field = _collection_field_for_add_method(class_body, member)
        if add_field and not expected_fields:
            method_replacements.append(_collection_add_method_replacement(member, add_field))
            aliased_methods.append(member)
            continue
        collection_field = _collection_field_for_list_method(class_body, member)
        if collection_field and not alias_args and not expected_fields:
            method_replacements.append(_collection_list_method_replacement(member, collection_field))
            aliased_methods.append(member)
            continue
        existing = _select_existing_method_for_alias(
            class_body=class_body,
            existing_methods=existing_methods,
            expected_fields=expected_fields,
        )
        if not existing:
            continue
        existing_return_fields = _return_object_fields_for_method(class_body, existing)
        method_replacements.append(
            _missing_method_alias_replacement(
                missing_member=member,
                existing_member=existing,
                alias_args=alias_args,
                expected_fields=expected_fields,
                existing_return_fields=existing_return_fields,
            )
        )
        aliased_methods.append(member)
        selected_existing_methods.append(existing)
    if not method_replacements:
        return None
    replacement = class_body + "".join(method_replacements)
    span_start = class_match.end()
    span_end = class_end
    context_before = text[max(0, span_start - 160) : span_start]
    context_after = text[span_end : min(len(text), span_end + 160)]
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=span_start,
        span_end=span_end,
        expected=text[span_start:span_end],
        replacement=replacement,
        before_hash=sha256_text(text),
        metadata={
            "repair_kind": "javascript_missing_method_runtime",
            "class_name": class_name,
            "missing_member": missing_member,
            "aliased_to": selected_existing_methods[0] if len(set(selected_existing_methods)) == 1 else "",
            "selected_existing_methods": selected_existing_methods,
            "aliased_methods": aliased_methods,
            "constructor_object_fields": constructor_fields,
            "diagnostic_id": diagnostic.diagnostic_id,
            "edit_file_preferred": True,
            "unsafe_cases_fail_closed": True,
            "expected_context_before": context_before,
            "expected_context_after": context_after,
        },
    )


def _constructor_contract_operation(
    *,
    base_files: Mapping[str, str],
    path: str,
    text: str,
    class_name: str,
    required_field: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    repaired = _repair_constructor_object_contract_text(
        text,
        base_files=base_files,
        class_name=class_name,
        required_field=required_field,
    )
    if repaired == text:
        return None
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=0,
        span_end=len(text),
        expected=text,
        replacement=repaired,
        before_hash=sha256_text(text),
        metadata={
            "repair_kind": "javascript_constructor_object_contract",
            "class_name": class_name,
            "required_field": required_field,
            "diagnostic_id": diagnostic.diagnostic_id,
            "edit_file_preferred": True,
            "unsafe_cases_fail_closed": True,
        },
    )


def _repair_constructor_object_contract_text(
    text: str,
    *,
    base_files: Mapping[str, str],
    class_name: str,
    required_field: str,
) -> str:
    class_match = re.search(_JS_CLASS_RE_TEMPLATE.format(class_name=re.escape(class_name)), text)
    if not class_match:
        return text
    class_end = _find_matching_brace(text, class_match.end() - 1)
    if class_end is None:
        return text
    class_body = text[class_match.end() : class_end]
    usage_keys = list(_constructor_object_keys_for_class(base_files, class_name))
    required_fields = list(
        dict.fromkeys(
            [
                required_field,
                *_constructor_required_string_fields(text, class_name),
            ]
        )
    )
    class_body, _constructor_fields = _augment_constructor_from_object_keys(
        class_body,
        [*usage_keys, *required_fields],
    )
    class_body = _normalize_constructor_required_string_fields(
        class_body,
        required_fields=required_fields,
        usage_keys=usage_keys,
    )
    repaired = text[: class_match.end()] + class_body + text[class_end:]
    repaired = _extend_to_json_usage_fields(repaired, class_name=class_name, usage_keys=usage_keys)
    return _append_javascript_namespace_helpers(repaired, base_files=base_files, class_name=class_name)


def _constructor_required_string_fields(text: str, class_name: str) -> tuple[str, ...]:
    fields: list[str] = []
    escaped_class = re.escape(str(class_name or ""))
    for pattern in (
        rf"{escaped_class}\.(?P<field>[A-Za-z_$][\w$]*)\s+must be a non-empty string",
        rf"{escaped_class}\s+requires\s+(?:an?\s+)?(?P<field>[A-Za-z_$][\w$]*)",
    ):
        fields.extend(str(match.group("field") or "") for match in re.finditer(pattern, str(text or "")))
    return tuple(dict.fromkeys(field for field in fields if _JS_IDENTIFIER_RE.match(field)))


def _normalize_constructor_required_string_fields(
    class_body: str,
    *,
    required_fields: Sequence[str],
    usage_keys: Sequence[str],
) -> str:
    constructor_match = re.search(
        r"constructor\s*\(\s*\{(?P<fields>[^}]*)\}\s*=\s*\{\}\s*\)\s*\{",
        class_body,
    )
    if constructor_match is None:
        return class_body
    body_open = constructor_match.end() - 1
    body_close = _find_matching_brace(class_body, body_open)
    if body_close is None:
        return class_body
    body = class_body[body_open + 1 : body_close]
    for field in required_fields:
        if not _JS_IDENTIFIER_RE.match(field):
            continue
        normalized = f"normalized{field[:1].upper()}{field[1:]}"
        if normalized not in body:
            body = "\n" + _constructor_string_field_normalizer(field, normalized, usage_keys) + body
        body = _replace_constructor_required_string_field(body, field=field, normalized=normalized)
    return class_body[: body_open + 1] + body + class_body[body_close:]


def _constructor_string_field_normalizer(field: str, normalized: str, usage_keys: Sequence[str]) -> str:
    candidates = list(dict.fromkeys([field, *usage_keys, "title"]))
    rendered_candidates = []
    for key in candidates:
        if key == "fragments":
            rendered_candidates.append('Array.isArray(fragments) ? fragments.map(String).join(" | ") : fragments')
        elif _JS_IDENTIFIER_RE.match(key):
            rendered_candidates.append(key)
    joined = ", ".join(rendered_candidates)
    return (
        f"\n    const {normalized} = [{joined}].find(\n"
        '      (value) => typeof value === "string" && value.length > 0,\n'
        '    ) ?? "";'
    )


def _replace_constructor_required_string_field(body: str, *, field: str, normalized: str) -> str:
    escaped_field = re.escape(field)
    repaired = re.sub(rf"\bif\s*\(\s*!\s*{escaped_field}\s*\)", f"if (!{normalized})", body)
    repaired = re.sub(rf"\btypeof\s+{escaped_field}\s*!==", f"typeof {normalized} !==", repaired)
    repaired = re.sub(rf"\b{escaped_field}\.length\b", f"{normalized}.length", repaired)
    return re.sub(rf"\bthis\.{escaped_field}\s*=\s*{escaped_field}\s*;", f"this.{field} = {normalized};", repaired)


def _extend_to_json_usage_fields(text: str, *, class_name: str, usage_keys: Sequence[str]) -> str:
    class_match = re.search(_JS_CLASS_RE_TEMPLATE.format(class_name=re.escape(class_name)), text)
    if not class_match:
        return text
    class_end = _find_matching_brace(text, class_match.end() - 1)
    if class_end is None:
        return text
    class_body = text[class_match.end() : class_end]
    method_match = next(
        (match for match in _JS_METHOD_RE.finditer(class_body) if match.group("name") == "toJSON"), None
    )
    if method_match is None:
        return text
    method_end = _find_matching_brace(class_body, method_match.end() - 1)
    if method_end is None:
        return text
    method_body = class_body[method_match.end() : method_end]
    return_match = re.search(r"return\s*\{(?P<fields>.*?)\}\s*;?", method_body, flags=re.DOTALL)
    if not return_match:
        return text
    existing_fields = set(_parse_js_object_field_list(str(return_match.group("fields") or "")))
    missing = [field for field in usage_keys if _JS_IDENTIFIER_RE.match(field) and field not in existing_fields]
    if not missing:
        return text
    field_lines = [f"      {field}: {_to_json_field_expression(field)}," for field in missing]
    insert_at = class_match.end() + method_match.end() + return_match.end("fields")
    insertion = "\n" + "\n".join(field_lines)
    return text[:insert_at] + insertion + text[insert_at:]


def _to_json_field_expression(field: str) -> str:
    if field == "createdAt":
        return "this.createdAt instanceof Date ? this.createdAt.toISOString() : this.createdAt"
    return f"this.{field}"


def _append_javascript_namespace_helpers(
    text: str,
    *,
    base_files: Mapping[str, str],
    class_name: str,
) -> str:
    helper_names = _javascript_namespace_calls_for_class(base_files, class_name)
    repaired = text.rstrip()
    for helper_name in helper_names:
        if re.search(rf"\bexport\s+function\s+{re.escape(helper_name)}\s*\(", repaired):
            continue
        if re.search(rf"\bstatic\s+{re.escape(helper_name)}\s*\(", repaired):
            continue
        repaired += "\n\n" + _build_javascript_namespace_helper_function(helper_name)
    return repaired + "\n" if repaired != text.rstrip() else text


def _javascript_namespace_calls_for_class(base_files: Mapping[str, str], class_name: str) -> tuple[str, ...]:
    if not _JS_IDENTIFIER_RE.match(class_name):
        return ()
    pattern = re.compile(rf"\b{re.escape(class_name)}\.(?P<method>[A-Za-z_$][\w$]*)\s*\(")
    names: list[str] = []
    for source in base_files.values():
        for match in pattern.finditer(str(source or "")):
            name = str(match.group("method") or "")
            if name and name != class_name:
                names.append(name)
    return tuple(dict.fromkeys(names))


def _build_javascript_namespace_helper_function(helper_name: str) -> str:
    if helper_name.startswith("compose"):
        return (
            f'export function {helper_name}(seed = "") {{\n'
            '  const text = String(seed ?? "dream");\n'
            "  return `Dream ${text}`;\n"
            "}"
        )
    return f"export function {helper_name}(...args) {{\n  return args[0] ?? null;\n}}"


def _missing_method_call_arguments(raw: str, object_name: str, member: str) -> str:
    pattern = re.compile(
        rf"\b{re.escape(object_name)}\.{re.escape(member)}\s*\((?P<args>[^()\n]*(?:\([^)]*\)[^()\n]*)*)\)"
    )
    match = pattern.search(str(raw or ""))
    return str(match.group("args") or "").strip() if match else ""


def _missing_method_call_sites(entry_text: str, object_name: str) -> tuple[dict[str, str], ...]:
    if not _JS_IDENTIFIER_RE.match(object_name):
        return ()
    pattern = re.compile(
        rf"\b{re.escape(object_name)}\.(?P<member>[A-Za-z_$][\w$]*)\s*"
        r"\((?P<args>[^()\n]*(?:\([^)]*\)[^()\n]*)*)\)"
    )
    sites: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in pattern.finditer(str(entry_text or "")):
        member = str(match.group("member") or "")
        if member in seen:
            continue
        seen.add(member)
        sites.append({"member": member, "arguments": str(match.group("args") or "").strip()})
    return tuple(sites)


def _expected_return_fields_for_call(entry_text: str, object_name: str, member: str) -> tuple[str, ...]:
    if not (_JS_IDENTIFIER_RE.match(object_name) and _JS_IDENTIFIER_RE.match(member)):
        return ()
    escaped_call = rf"{re.escape(object_name)}\.{re.escape(member)}\s*\("
    destructured = re.search(
        rf"(?:const|let|var)\s*\{{(?P<fields>[^}}]+)\}}\s*=\s*{escaped_call}",
        entry_text,
    )
    if destructured:
        return _parse_js_object_field_list(str(destructured.group("fields") or ""))
    assigned = re.search(
        rf"(?:const|let|var)\s+(?P<var>[A-Za-z_$][\w$]*)\s*=\s*{escaped_call}",
        entry_text,
    )
    if not assigned:
        return ()
    result_var = str(assigned.group("var") or "")
    field_pattern = re.compile(rf"\b{re.escape(result_var)}\.(?P<field>[A-Za-z_$][\w$]*)\b")
    fields = [str(match.group("field") or "") for match in field_pattern.finditer(entry_text[assigned.end() :])]
    return tuple(dict.fromkeys(field for field in fields if _JS_IDENTIFIER_RE.match(field)))


def _parse_js_object_field_list(raw_fields: str) -> tuple[str, ...]:
    fields: list[str] = []
    for item in _split_js_call_arguments(raw_fields):
        token = item.strip()
        if not token:
            continue
        if ":" in token:
            token = token.split(":", 1)[0].strip()
        token = token.lstrip(".").strip()
        if _JS_IDENTIFIER_RE.match(token):
            fields.append(token)
    return tuple(dict.fromkeys(fields))


def _return_object_fields_for_method(class_body: str, method_name: str) -> tuple[str, ...]:
    method_match = next(
        (match for match in _JS_METHOD_RE.finditer(class_body) if match.group("name") == method_name),
        None,
    )
    if method_match is None:
        return ()
    method_end = _find_matching_brace(class_body, method_match.end() - 1)
    if method_end is None:
        return ()
    method_body = class_body[method_match.end() : method_end]
    return_match = re.search(r"return\s*\{(?P<fields>.*?)\}\s*;?", method_body, flags=re.DOTALL)
    if not return_match:
        return ()
    return _parse_js_object_field_list(str(return_match.group("fields") or ""))


def _select_existing_method_for_alias(
    *,
    class_body: str,
    existing_methods: Sequence[str],
    expected_fields: Sequence[str],
) -> str:
    candidates = [method for method in dict.fromkeys(existing_methods) if _JS_IDENTIFIER_RE.match(method)]
    if not candidates:
        return ""
    if not expected_fields:
        return candidates[0] if len(candidates) == 1 else ""
    scored: list[tuple[int, str]] = []
    for method in candidates:
        return_fields = _return_object_fields_for_method(class_body, method)
        score = _return_field_match_score(expected_fields, return_fields)
        if score > 0:
            scored.append((score, method))
    if not scored:
        return candidates[0] if len(candidates) == 1 else ""
    scored.sort(key=lambda item: (-item[0], candidates.index(item[1])))
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return ""
    return scored[0][1]


def _return_field_match_score(expected_fields: Sequence[str], return_fields: Sequence[str]) -> int:
    score = 0
    return_set = {field for field in return_fields if _JS_IDENTIFIER_RE.match(field)}
    for expected in expected_fields:
        for alias in _field_alias_candidates(expected, tuple(return_set), set()):
            if alias in return_set:
                score += 1
                break
    return score


def _collection_field_for_list_method(class_body: str, method_name: str) -> str:
    if not _JS_IDENTIFIER_RE.match(method_name):
        return ""
    match = re.match(r"list(?P<tail>[A-Z][A-Za-z0-9_$]*)$", method_name)
    if not match:
        return ""
    requested = _lower_camel_identifier(match.group("tail"))
    fields = _class_collection_fields(class_body)
    if requested in fields:
        return requested
    singular = _singularize_js_identifier(requested)
    matches = [field for field in fields if _singularize_js_identifier(field) == singular]
    return matches[0] if len(matches) == 1 else ""


def _collection_field_for_add_method(class_body: str, method_name: str) -> str:
    if not _JS_IDENTIFIER_RE.match(method_name):
        return ""
    match = re.match(r"add(?P<tail>[A-Z][A-Za-z0-9_$]*)$", method_name)
    if not match:
        return ""
    singular = _lower_camel_identifier(match.group("tail"))
    requested = _pluralize_js_identifier(singular)
    fields = _class_collection_fields(class_body)
    if requested in fields:
        return requested
    matches = [field for field in fields if _singularize_js_identifier(field) == singular]
    return matches[0] if len(matches) == 1 else ""


def _class_collection_fields(class_body: str) -> tuple[str, ...]:
    fields: list[str] = []
    for match in re.finditer(r"\bthis\.(?P<field>[A-Za-z_$][\w$]*)\s*=", class_body):
        field = str(match.group("field") or "")
        if _JS_IDENTIFIER_RE.match(field) and _field_is_array_like(field):
            fields.append(field)
    return tuple(dict.fromkeys(fields))


def _collection_list_method_replacement(method_name: str, collection_field: str) -> str:
    return (
        f"\n  {method_name}() {{\n"
        f"    return Array.isArray(this.{collection_field}) ? [...this.{collection_field}] : [];\n"
        "  }\n"
    )


def _collection_add_method_replacement(method_name: str, collection_field: str) -> str:
    match = re.match(r"add(?P<tail>[A-Z][A-Za-z0-9_$]*)$", method_name)
    param_name = _lower_camel_identifier(match.group("tail")) if match else "item"
    return (
        f"\n  {method_name}({param_name}) {{\n    this.{collection_field}.push({param_name});\n    return this;\n  }}\n"
    )


def _constructor_object_keys_for_class(base_files: Mapping[str, str], class_name: str) -> tuple[str, ...]:
    if not _JS_IDENTIFIER_RE.match(class_name):
        return ()
    pattern = re.compile(rf"\bnew\s+(?:[A-Za-z_$][\w$]*\.)?{re.escape(class_name)}\s*\(")
    keys: list[str] = []
    for text in base_files.values():
        source = str(text or "")
        for match in pattern.finditer(source):
            open_paren = source.find("(", match.start())
            object_start = source.find("{", open_paren)
            if open_paren < 0 or object_start < 0:
                continue
            if source[open_paren + 1 : object_start].strip():
                continue
            object_end = _find_matching_brace(source, object_start)
            if object_end is None:
                continue
            keys.extend(_parse_js_object_field_list(source[object_start + 1 : object_end]))
    return tuple(dict.fromkeys(key for key in keys if _JS_IDENTIFIER_RE.match(key)))


def _augment_constructor_from_object_keys(class_body: str, object_keys: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    missing_keys = [key for key in object_keys if _JS_IDENTIFIER_RE.match(key)]
    if not missing_keys:
        return class_body, ()
    constructor_match = re.search(
        r"constructor\s*\(\s*\{(?P<fields>[^}]*)\}\s*=\s*\{\}\s*\)\s*\{",
        class_body,
    )
    if constructor_match is None:
        return class_body, ()
    existing_fields = set(_parse_constructor_field_names(str(constructor_match.group("fields") or "")))
    fields_to_add = [
        key
        for key in missing_keys
        if key not in existing_fields and not re.search(rf"\bthis\.{re.escape(key)}\s*=", class_body)
    ]
    if not fields_to_add:
        return class_body, ()
    new_field_text = _constructor_field_text(str(constructor_match.group("fields") or ""), fields_to_add)
    updated = (
        class_body[: constructor_match.start("fields")] + new_field_text + class_body[constructor_match.end("fields") :]
    )
    insertion_at = constructor_match.end() + (len(new_field_text) - len(str(constructor_match.group("fields") or "")))
    assignment_lines = "".join(f"\n    {_constructor_assignment_for_field(field)}" for field in fields_to_add)
    updated = updated[:insertion_at] + assignment_lines + updated[insertion_at:]
    return updated, tuple(fields_to_add)


def _parse_constructor_field_names(raw_fields: str) -> tuple[str, ...]:
    names: list[str] = []
    for item in _split_js_call_arguments(raw_fields):
        token = item.strip()
        if not token:
            continue
        if ":" in token:
            token = token.split(":", 1)[0].strip()
        if "=" in token:
            token = token.split("=", 1)[0].strip()
        if _JS_IDENTIFIER_RE.match(token):
            names.append(token)
    return tuple(dict.fromkeys(names))


def _constructor_field_text(existing_fields: str, fields_to_add: Sequence[str]) -> str:
    fields = [item.strip() for item in _split_js_call_arguments(existing_fields) if item.strip()]
    fields.extend(field for field in fields_to_add if _JS_IDENTIFIER_RE.match(field))
    return ", ".join(dict.fromkeys(fields))


def _constructor_assignment_for_field(field: str) -> str:
    if _field_is_array_like(field):
        return f"this.{field} = Array.isArray({field}) ? {field}.map(String) : [];"
    if _field_is_numeric_like(field):
        return f"this.{field} = Number.isFinite({field}) ? {field} : 0;"
    return f"this.{field} = {field};"


def _field_is_array_like(field: str) -> bool:
    lower = field.lower()
    return lower.endswith("s") or lower.endswith("ids")


def _field_is_numeric_like(field: str) -> bool:
    lower = field.lower()
    return any(token in lower for token in ("absurdity", "count", "score", "amount", "boost", "level", "intensity"))


def _missing_method_alias_replacement(
    *,
    missing_member: str,
    existing_member: str,
    alias_args: str,
    expected_fields: Sequence[str],
    existing_return_fields: Sequence[str],
) -> str:
    if not alias_args:
        return f"\n  {missing_member}(...args) {{\n    return this.{existing_member}(...args);\n  }}\n"
    if not expected_fields:
        return f"\n  {missing_member}({alias_args}) {{\n    return this.{existing_member}({alias_args});\n  }}\n"
    field_lines = _return_adapter_field_lines(expected_fields, existing_return_fields)
    if not field_lines:
        return f"\n  {missing_member}({alias_args}) {{\n    return this.{existing_member}({alias_args});\n  }}\n"
    return (
        f"\n  {missing_member}({alias_args}) {{\n"
        f"    const result = this.{existing_member}({alias_args});\n"
        "    return {\n" + "".join(f"      {line}\n" for line in field_lines) + "    };\n"
        "  }\n"
    )


def _return_adapter_field_lines(
    expected_fields: Sequence[str],
    existing_return_fields: Sequence[str],
) -> tuple[str, ...]:
    existing_fields = [field for field in dict.fromkeys(existing_return_fields) if _JS_IDENTIFIER_RE.match(field)]
    consumed_existing: set[str] = set()
    planned: list[tuple[str, list[str]]] = []
    for field in expected_fields:
        if not _JS_IDENTIFIER_RE.match(field):
            continue
        aliases = _field_alias_candidates(field, existing_fields, consumed_existing)
        consumed_existing.update(alias for alias in aliases if alias in existing_fields and alias != field)
        planned.append((field, aliases))
    lines: list[str] = []
    for field, aliases in planned:
        if _field_is_residual_collection(field):
            residual_existing = [item for item in existing_fields if item not in consumed_existing]
            aliases = [*aliases, *residual_existing]
            consumed_existing.update(residual_existing)
        deduped = list(dict.fromkeys(alias for alias in aliases if _JS_IDENTIFIER_RE.match(alias)))
        if not deduped or deduped[0] != field:
            deduped.insert(0, field)
        expression = " ?? ".join(f"result.{alias}" for alias in deduped)
        lines.append(f"{field}: {expression} ?? [],")
    return tuple(lines)


def _field_alias_candidates(field: str, existing_fields: Sequence[str], consumed_existing: set[str]) -> list[str]:
    aliases = [field]
    lower = field.lower()
    if lower.endswith("cards") and lower != "cards":
        aliases.append("cards")
    for existing in existing_fields:
        existing_lower = existing.lower()
        if existing in consumed_existing or existing == field:
            continue
        if (
            (lower == "cards" and existing_lower.endswith("cards"))
            or lower.endswith(existing_lower)
            or existing_lower.endswith(lower)
        ):
            aliases.append(existing)
    if _field_is_residual_collection(field):
        aliases.extend(["unmatched", "unconsumed"])
    return list(dict.fromkeys(aliases))


def _field_is_residual_collection(field: str) -> bool:
    return field.lower() in {"untouched", "unmatched", "unconsumed", "remaining", "unused", "leftover", "leftovers"}


def _alias_arguments_from_call_arguments(call_arguments: str, missing_member: str) -> str:
    args = _split_js_call_arguments(call_arguments)
    identifiers: list[str] = []
    for index, arg in enumerate(args):
        normalized = arg.strip()
        if _JS_IDENTIFIER_RE.match(normalized):
            identifiers.append(normalized)
            continue
        identifiers.append(_generic_alias_argument_name(index, missing_member))
    return ", ".join(identifiers)


def _split_js_call_arguments(call_arguments: str) -> list[str]:
    text = str(call_arguments or "").strip()
    if not text:
        return []
    args: list[str] = []
    start = 0
    depth = 0
    quote = ""
    escape = False
    for index, char in enumerate(text):
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"', "`"}:
            quote = char
            continue
        if char in "([{":
            depth += 1
            continue
        if char in ")]}":
            depth = max(0, depth - 1)
            continue
        if char == "," and depth == 0:
            args.append(text[start:index].strip())
            start = index + 1
    args.append(text[start:].strip())
    return [arg for arg in args if arg]


def _generic_alias_argument_name(index: int, missing_member: str) -> str:
    if index == 0:
        add_match = re.match(r"add(?P<name>[A-Z][A-Za-z0-9_$]*)$", str(missing_member or ""))
        if add_match:
            return _lower_camel_identifier(add_match.group("name"))
    common_names = ("value", "options", "item", "context")
    if index < len(common_names):
        return common_names[index]
    return f"arg{index + 1}"


def _lower_camel_identifier(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_$]", "", str(value or ""))
    if not token:
        return "value"
    return token[0].lower() + token[1:]
