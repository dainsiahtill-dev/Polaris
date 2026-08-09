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
from .misc_ops import *  # noqa: F403

"""Shared TypeScript repair helpers: member_text_ops."""

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

def _typescript_line_invokes_constructor(line: str, class_name: str) -> bool:
    if not _TS_IDENTIFIER_RE.fullmatch(class_name):
        return False
    return bool(re.search(rf"\bnew\s+{re.escape(class_name)}\s*\(", str(line or "")))

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

def _typescript_symbol_is_constructed(text: str, symbol: str) -> bool:
    return bool(re.search(rf"\bnew\s+{re.escape(symbol)}\s*\(", str(text or "")))

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


__all__ = (
    "_typescript_enum_has_runtime_member_access",
    "_parse_typescript_missing_member_errors",
    "_parse_typescript_object_missing_member_errors",
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
    "_typescript_line_invokes_constructor",
    "_typescript_safe_structural_member_type",
    "_typescript_default_value_for_required_property_type",
    "_typescript_symbol_is_constructed",
    "_typescript_symbol_has_named_constructor_binding",
    "_typescript_symbol_has_field_constructor_binding",
    "_typescript_methods_used_on_constructed_symbol",
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
    "_typescript_constructor_default_arguments",
    "_typescript_type_value_dot_member",
)
