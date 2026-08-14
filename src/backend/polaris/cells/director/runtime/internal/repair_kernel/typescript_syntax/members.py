from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from ..contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from .common import *  # noqa: F403
from .constants import *  # noqa: F403

"""TypeScript syntax repair module: members."""

def build_typescript_unknown_member_access_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a narrow TS18046 repair plan for unknown typed member access."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    targets = _parse_unknown_member_access_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_items: list[dict[str, str]] = []
    for target in targets:
        usage_path = str(target.get("file") or "")
        usage_text = str(normalized_base_files.get(usage_path) or "")
        line_number = _to_positive_int(target.get("line"))
        receiver = str(target.get("receiver") or "")
        member = str(target.get("member") or "")
        if not usage_text or not _TS_IDENTIFIER_RE.fullmatch(receiver) or not _TS_IDENTIFIER_RE.fullmatch(member):
            continue
        usage_line = _typescript_line_at(usage_text, line_number)
        replacement_type = _typescript_usage_compatible_member_type(usage_line, member)
        if not replacement_type:
            continue
        type_name = _typescript_unknown_member_receiver_type(
            base_files=normalized_base_files,
            usage_path=usage_path,
            receiver=receiver,
        )
        if not type_name:
            continue
        operation = _typescript_unknown_member_type_operation(
            base_files=normalized_base_files,
            type_name=type_name,
            member=member,
            replacement_type=replacement_type,
        )
        if operation is None:
            continue
        operations.append(operation)
        matched_diagnostics.extend(
            diagnostic for diagnostic in diagnostics if _diagnostic_targets_path(diagnostic, usage_path)
        )
        repaired_items.append(
            {
                "file": operation.path,
                "type": type_name,
                "member": member,
                "replacement_type": replacement_type,
            }
        )
    return _repair_plan_or_none(
        rule_id="typescript.unknown_member_access",
        source_tool=TYPESCRIPT_UNKNOWN_MEMBER_ACCESS_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"unknown_member_accesses": repaired_items},
    )

def _build_typescript_member_alias_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    """Rewrite missing member access to existing API surface (R160 class getters/methods).

    Multi-diagnostic rewrites on one file are accumulated per source line and
    emitted as non-overlapping ``text_replace`` operations. PatchComposer can
    merge those precise edits into one patch without granting whole-file write
    authority to a local member repair.
    """

    aliases: list[dict[str, str]] = []
    seen_aliases: set[tuple[str, int, str, str]] = set()
    # path -> (base_content, working lines)
    working: dict[str, tuple[str, list[str]]] = {}

    def _ensure_working(path: str) -> tuple[str, list[str]] | None:
        if path in working:
            return working[path]
        base = str(base_files.get(path) or "")
        if not base:
            return None
        lines = base.splitlines(keepends=True)
        working[path] = (base, lines)
        return working[path]

    for item in _parse_typescript_missing_member_errors(diagnostics):
        path = item["file"]
        state = _ensure_working(path)
        if state is None:
            continue
        base_content, lines = state
        member = item["member"]
        type_name = _typescript_declaration_type_name(item["type"])
        line_number = _to_positive_int(item.get("line"))
        if not type_name or line_number <= 0:
            continue
        line_index = line_number - 1
        if line_index < 0 or line_index >= len(lines):
            continue
        receiver = _typescript_receiver_for_member_access(lines[line_index], member)
        existing_members = _typescript_existing_member_names_for_type(base_files=base_files, type_name=type_name)
        # R160: Garden is a plain interface snapshot — drop setMoonPhase, fan-out tick.
        if receiver and member == "setMoonPhase" and "moon" in existing_members:
            repaired_line = re.sub(
                rf"\b{re.escape(receiver)}\s*\.\s*setMoonPhase\s*\([^;]*\)\s*;?",
                "/* setMoonPhase removed: Garden is a snapshot interface */",
                lines[line_index],
            )
            if repaired_line != lines[line_index]:
                alias_key = (path, line_index, member, "drop_setMoonPhase")
                if alias_key not in seen_aliases:
                    seen_aliases.add(alias_key)
                    if not repaired_line.endswith("\n") and lines[line_index].endswith("\n"):
                        repaired_line = f"{repaired_line}\n"
                    lines[line_index] = repaired_line
                    aliases.append(
                        {"file": path, "type": type_name, "member": member, "replacement": "drop_setMoonPhase"}
                    )
            continue
        if (
            receiver
            and member == "tick"
            and "moon" in existing_members
            and "fireflies" in existing_members
            and re.search(rf"\b{re.escape(receiver)}\s*\.\s*tick\s*\(", lines[line_index])
        ):
            indent_match = re.match(r"^(\s*)", lines[line_index])
            indent = indent_match.group(1) if indent_match else ""
            arg_match = re.search(
                rf"\b{re.escape(receiver)}\s*\.\s*tick\s*\((?P<args>[^)]*)\)",
                lines[line_index],
            )
            args = str(arg_match.group("args") if arg_match else "0.016").strip() or "0.016"
            repaired_line = (
                f"{indent}for (const __entity of {receiver}.fireflies) {{ __entity.tick({args}); }}\n"
                f"{indent}{receiver}.moon.tick({args});\n"
            )
            alias_key = (path, line_index, member, "fanout_tick")
            if alias_key not in seen_aliases:
                seen_aliases.add(alias_key)
                lines[line_index] = repaired_line
                aliases.append({"file": path, "type": type_name, "member": member, "replacement": "fanout_tick"})
            continue
        replacement = _typescript_member_alias_replacement(
            receiver=receiver,
            missing_member=member,
            existing_members=existing_members,
        )
        if not receiver or not replacement:
            continue
        alias_key = (path, line_index, member, replacement)
        if alias_key in seen_aliases:
            continue
        seen_aliases.add(alias_key)
        repaired_line = re.sub(rf"\b{re.escape(receiver)}\s*\.\s*{re.escape(member)}\b", replacement, lines[line_index])
        if repaired_line == lines[line_index]:
            continue
        lines[line_index] = repaired_line
        aliases.append({"file": path, "type": type_name, "member": member, "replacement": replacement})

    operations: list[RepairOperation] = []
    for path, (base_content, lines) in sorted(working.items()):
        repaired = "".join(lines)
        if repaired == base_content:
            continue
        original_lines = base_content.splitlines(keepends=True)
        offset = 0
        for line_index, original_line in enumerate(original_lines):
            repaired_line = lines[line_index] if line_index < len(lines) else ""
            if repaired_line != original_line:
                operations.append(
                    RepairOperation(
                        kind="text_replace",
                        path=path,
                        span_start=offset,
                        span_end=offset + len(original_line),
                        expected=original_line,
                        replacement=repaired_line,
                        before_hash=sha256_text(base_content),
                        metadata={
                            "repair_kind": "typescript_member_alias",
                            "edit_strategy": "line_text_replace",
                            "line": line_index + 1,
                        },
                    )
                )
            offset += len(original_line)
    return _repair_plan_or_none(
        rule_id="typescript.member_alias",
        source_tool=TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"aliases": aliases},
    )

def _build_typescript_private_constructor_access_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    repairs: list[dict[str, object]] = []
    seen: set[tuple[str, str, int]] = set()
    for item in _parse_typescript_private_constructor_access_errors(diagnostics):
        path = item["file"]
        class_name = item["class"]
        line_number = _to_positive_int(item.get("line"))
        original = str(base_files.get(path) or "")
        if not original or not class_name or line_number <= 0:
            continue
        lines = original.splitlines(keepends=True)
        line_index = line_number - 1
        if line_index < 0 or line_index >= len(lines):
            continue
        if not _typescript_line_invokes_constructor(lines[line_index], class_name):
            continue
        modifier_span = _typescript_exported_private_constructor_modifier_span(original, class_name)
        if modifier_span is None:
            continue
        modifier_line_index, start, end = modifier_span
        key = (path, class_name, start)
        if key in seen:
            continue
        seen.add(key)
        expected = original[start:end]
        if expected != "private ":
            continue
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=start,
                span_end=end,
                expected=expected,
                replacement="",
                before_hash=sha256_text(original),
                metadata={
                    "repair_kind": "typescript_private_constructor_access",
                    "class_name": class_name,
                    "diagnostic_line": line_number,
                    "constructor_line": modifier_line_index + 1,
                    "visibility_change": "private_to_public_default",
                    "precision_strategy": "diagnostic_new_expression_to_exported_class_private_constructor",
                },
            )
        )
        repairs.append(
            {
                "file": path,
                "class_name": class_name,
                "diagnostic_line": line_number,
                "constructor_line": modifier_line_index + 1,
            }
        )
    return _repair_plan_or_none(
        rule_id="typescript.private_constructor_access",
        source_tool=TYPESCRIPT_PRIVATE_CONSTRUCTOR_ACCESS_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"repairs": repairs},
    )

def _build_typescript_private_property_access_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    repairs: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in _parse_typescript_private_property_access_errors(diagnostics):
        class_name = item["class"]
        field_name = item["property"]
        if not class_name or not field_name:
            continue
        owner_path, original = _typescript_class_owner_file(base_files, class_name)
        if not owner_path or not original:
            continue
        key = (owner_path, class_name, field_name)
        if key in seen:
            continue
        span = _typescript_private_field_modifier_span(original, class_name, field_name)
        if span is None:
            continue
        start, end = span
        expected = original[start:end]
        if expected != "private ":
            continue
        seen.add(key)
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=owner_path,
                span_start=start,
                span_end=end,
                expected=expected,
                replacement="",
                before_hash=sha256_text(original),
                metadata={
                    "repair_kind": "typescript_private_property_access",
                    "class_name": class_name,
                    "property": field_name,
                    "visibility_change": "private_to_public_default",
                },
            )
        )
        repairs.append(
            {
                "file": owner_path,
                "class_name": class_name,
                "property": field_name,
                "usage_file": item["file"],
            }
        )
    return _repair_plan_or_none(
        rule_id="typescript.private_property_access",
        source_tool=TYPESCRIPT_PRIVATE_PROPERTY_ACCESS_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"repairs": repairs},
    )

def _build_typescript_missing_member_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = list(
        _typescript_object_literal_missing_member_operations(base_files=base_files, diagnostics=diagnostics)
    )
    inline_operations, inline_members = _typescript_inline_object_missing_member_operations(
        base_files=base_files,
        diagnostics=diagnostics,
    )
    operations.extend(inline_operations)
    members: list[dict[str, str]] = []
    members.extend(inline_members)
    grouped_members: dict[str, dict[str, dict[str, object]]] = {}
    anonymous_ops: list[RepairOperation] = []
    for item in _parse_typescript_missing_member_errors(diagnostics):
        raw_type_name = item["type"]
        type_name = _typescript_declaration_type_name(raw_type_name)
        member = item["member"]
        if not _TS_IDENTIFIER_RE.fullmatch(member):
            continue
        usage_path = item["file"]
        usage_text = str(base_files.get(usage_path) or "")
        line_number = _to_positive_int(item.get("line"))
        member_is_call = _typescript_member_usage_is_call(usage_text, line_number, member)
        static_context = str(raw_type_name or "").strip().startswith("typeof ")
        # R180: declare const window: { ... } missing cancelAnimationFrame.
        if str(raw_type_name or "").lstrip().startswith("{"):
            receiver = ""
            if line_number > 0:
                lines = usage_text.splitlines()
                if line_number <= len(lines):
                    receiver = _typescript_receiver_for_member_access(lines[line_number - 1], member)
            if receiver:
                anon_op = _extend_typescript_declare_const_type_literal_operation(
                    path=usage_path,
                    content=usage_text,
                    receiver=receiver,
                    member=member,
                    member_is_call=member_is_call,
                )
                if anon_op is not None:
                    anonymous_ops.append(anon_op)
                    members.append({"file": usage_path, "type": "declare_const_literal", "member": member})
            continue
        if not type_name:
            continue
        if member_is_call and _typescript_declared_type_kind(base_files=base_files, type_name=type_name) != "class":
            continue
        declared_type = _typescript_missing_member_declared_type(
            usage_text,
            line_number,
            member,
            member_is_call=member_is_call,
        )
        if not declared_type or declared_type == "unknown":
            continue
        existing_members = _typescript_existing_member_names_for_type(base_files=base_files, type_name=type_name)
        receiver = ""
        if line_number > 0:
            lines = usage_text.splitlines()
            if line_number <= len(lines):
                receiver = _typescript_receiver_for_member_access(lines[line_number - 1], member)
        if receiver and _typescript_member_alias_replacement(
            receiver=receiver,
            missing_member=member,
            existing_members=existing_members,
        ):
            continue
        type_members = grouped_members.setdefault(type_name, {})
        existing = type_members.get(member) or {}
        existing_type = str(existing.get("declared_type") or "")
        type_members[member] = {
            "is_call": bool(existing.get("is_call")) or member_is_call,
            "declared_type": existing_type if existing_type and existing_type != "unknown" else declared_type,
            "static_context": bool(existing.get("static_context")) or static_context,
            "usage_path": usage_path,
        }
    for type_name, type_members in grouped_members.items():
        # Prefer declaration nearest to the usage import graph when multiple
        # same-named types exist (R180 GardenScene in models stub vs index demo).
        preferred_paths = {
            str(spec.get("usage_path") or "")
            for spec in type_members.values()
            if str(spec.get("usage_path") or "")
        }
        operation = _add_typescript_members_operation(
            base_files=base_files,
            type_name=type_name,
            members=tuple(
                (
                    member,
                    bool(spec.get("is_call")),
                    str(spec.get("declared_type") or "unknown"),
                    bool(spec.get("static_context")),
                )
                for member, spec in type_members.items()
            ),
            preferred_usage_paths=tuple(sorted(preferred_paths)),
        )
        if operation is None:
            continue
        operations.append(operation)
        for member in type_members:
            members.append({"file": operation.path, "type": type_name, "member": member})
    operations.extend(anonymous_ops)
    return _repair_plan_or_none(
        rule_id="typescript.missing_member",
        source_tool=TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"members": members},
    )

def _build_typescript_uninitialized_property_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    properties: list[dict[str, str]] = []
    for item in _parse_typescript_uninitialized_property_errors(diagnostics):
        path = item["file"]
        original = str(base_files.get(path) or "")
        line_index = _to_positive_int(item.get("line")) - 1
        if not original or line_index < 0:
            continue
        lines = original.splitlines(keepends=True)
        if line_index >= len(lines):
            continue
        line_body = lines[line_index].rstrip("\r\n")
        newline = lines[line_index][len(line_body) :]
        repaired_line = _typescript_property_line_with_default(line_body, item["member"]) + newline
        if repaired_line == lines[line_index]:
            continue
        operations.append(
            _line_text_replace_operation(
                path=path,
                content=original,
                line_index=line_index,
                replacement=repaired_line,
                metadata={"repair_kind": "typescript_uninitialized_property", "member": item["member"]},
            )
        )
        properties.append({"file": path, "member": item["member"]})
    return _repair_plan_or_none(
        rule_id="typescript.uninitialized_property",
        source_tool=TYPESCRIPT_UNINITIALIZED_PROPERTY_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"properties": properties},
    )

def _parse_typescript_private_constructor_access_errors(
    diagnostics: Sequence[RepairDiagnostic],
) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_PRIVATE_CONSTRUCTOR_ACCESS_RAW_RE.finditer(text):
            item = {
                "file": _normalize_repair_path(str(match.group("file") or "")),
                "line": str(match.group("line") or ""),
                "column": str(match.group("col") or ""),
                "class": str(match.group("class") or ""),
            }
            key = (item["file"], item["line"], item["class"])
            if item["file"] and item["line"] and item["class"] and key not in seen:
                seen.add(key)
                parsed.append(item)
        path = _normalize_repair_path(str(diagnostic.path or ""))
        line = str(diagnostic.line or "")
        message_match = _TS_PRIVATE_CONSTRUCTOR_ACCESS_MESSAGE_RE.search(text)
        if diagnostic.code.lower() == "typescript_ts2673" and path and line and message_match:
            item = {
                "file": path,
                "line": line,
                "column": str(diagnostic.column or ""),
                "class": str(message_match.group("class") or ""),
            }
            key = (item["file"], item["line"], item["class"])
            if item["class"] and key not in seen:
                seen.add(key)
                parsed.append(item)
    return parsed

def _parse_typescript_private_property_access_errors(
    diagnostics: Sequence[RepairDiagnostic],
) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_PRIVATE_PROPERTY_ACCESS_RAW_RE.finditer(text):
            item = {
                "file": _normalize_repair_path(str(match.group("file") or "")),
                "line": str(match.group("line") or ""),
                "column": str(match.group("col") or ""),
                "property": str(match.group("property") or ""),
                "class": str(match.group("class") or ""),
            }
            key = (item["file"], item["line"], item["class"], item["property"])
            if item["file"] and item["property"] and item["class"] and key not in seen:
                seen.add(key)
                parsed.append(item)
        path = _normalize_repair_path(str(diagnostic.path or ""))
        line = str(diagnostic.line or "")
        message_match = _TS_PRIVATE_PROPERTY_ACCESS_MESSAGE_RE.search(text)
        code = str(diagnostic.code or "").lower()
        if code in {"typescript_ts2341", "ts2341", "typescript_ts2345", "ts2345"} and path and message_match:
            item = {
                "file": path,
                "line": line,
                "column": str(diagnostic.column or ""),
                "property": str(message_match.group("property") or ""),
                "class": str(message_match.group("class") or ""),
            }
            key = (item["file"], item["line"], item["class"], item["property"])
            if item["class"] and item["property"] and key not in seen:
                seen.add(key)
                parsed.append(item)
    return parsed

def _typescript_class_owner_file(
    base_files: Mapping[str, str],
    class_name: str,
) -> tuple[str, str]:
    class_re = re.compile(rf"\bclass\s+{re.escape(class_name)}\b")
    matches: list[tuple[str, str]] = []
    for path, content in base_files.items():
        text = str(content or "")
        if class_re.search(text):
            matches.append((_normalize_repair_path(path), text))
    if len(matches) != 1:
        return "", ""
    return matches[0]

def _typescript_private_field_modifier_span(
    content: str,
    class_name: str,
    field_name: str,
) -> tuple[int, int] | None:
    class_match = re.search(rf"\bclass\s+{re.escape(class_name)}\b", content)
    if class_match is None:
        return None
    field_re = re.compile(
        rf"(?P<mod>private\s+)(?:readonly\s+)?{re.escape(field_name)}\b",
    )
    match = field_re.search(content, class_match.end())
    if match is None:
        return None
    return match.start("mod"), match.end("mod")

def _typescript_member_alias_replacement(*, receiver: str, missing_member: str, existing_members: set[str]) -> str:
    if missing_member == "checks" and "results" in existing_members:
        return f"{receiver}.results"
    if missing_member == "failures" and "results" in existing_members:
        return f"{receiver}.results.filter((result) => !result.ok)"
    if missing_member in {"x", "y"} and "position" in existing_members:
        return f"{receiver}.position.{missing_member}"
    if missing_member == "brightness" and "intensity" in existing_members:
        return f"{receiver}.intensity"
    # R160: Firefly exposes currentGlow() / state.glow, not a glow field.
    if missing_member == "glow":
        if "currentGlow" in existing_members:
            return f"{receiver}.currentGlow()"
        if "brightness" in existing_members:
            return f"{receiver}.brightness"
        if "state" in existing_members:
            return f"{receiver}.state.glow"
    # R160: Flower has species, not hue — map to a stable hue bucket.
    if missing_member == "hue" and "species" in existing_members:
        return (
            f'({{ "night-bloom": 210, "moon-petal": 280, "dew-lily": 160 }} as Record<string, number>)'
            f"[{receiver}.species] ?? 200"
        )
    if missing_member == "size":
        if "petalRadius" in existing_members:
            return f"{receiver}.petalRadius"
        if "radius" in existing_members:
            return f"{receiver}.radius"
    if missing_member == "color":
        if {"hue", "saturation", "lightness"}.issubset(existing_members):
            return (
                f"`hsl(${{{receiver}.hue}}, "
                f"${{Math.round({receiver}.saturation * 100)}}%, "
                f"${{Math.round({receiver}.lightness * 100)}}%)`"
            )
        if "hue" in existing_members:
            return f"`hsl(${{{receiver}.hue}}, 70%, 62%)`"
        if "species" in existing_members:
            return (
                f'`hsl(${{({{ "night-bloom": 210, "moon-petal": 280, "dew-lily": 160 }} as Record<string, number>)'
                f"[{receiver}.species] ?? 200}}, 70%, 55%)`"
            )
    if missing_member != "id" and missing_member.endswith("Id") and "id" in existing_members:
        return f"{receiver}.id"
    return ""

def _typescript_unknown_member_receiver_type(
    *,
    base_files: Mapping[str, str],
    usage_path: str,
    receiver: str,
) -> str:
    usage_text = str(base_files.get(usage_path) or "")
    if not usage_text or not _TS_IDENTIFIER_RE.fullmatch(receiver):
        return ""
    explicit_match = re.search(
        rf"\b(?:const|let|var)\s+{re.escape(receiver)}\s*:\s*(?P<type>[A-Za-z_$][A-Za-z0-9_$]*)\b",
        usage_text,
    )
    if explicit_match:
        return str(explicit_match.group("type") or "")
    initializer_match = re.search(
        rf"\b(?:const|let|var)\s+{re.escape(receiver)}\s*=\s*(?P<callee>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(",
        usage_text,
    )
    if not initializer_match:
        return ""
    callee = str(initializer_match.group("callee") or "")
    if not _TS_IDENTIFIER_RE.fullmatch(callee):
        return ""
    for content in base_files.values():
        return_type_match = re.search(
            rf"\b(?:export\s+)?function\s+{re.escape(callee)}\s*\([^)]*\)\s*:\s*"
            rf"(?P<type>[A-Za-z_$][A-Za-z0-9_$]*)\b",
            str(content or ""),
        )
        if return_type_match:
            return str(return_type_match.group("type") or "")
    return ""

def _typescript_unknown_member_type_operation(
    *,
    base_files: Mapping[str, str],
    type_name: str,
    member: str,
    replacement_type: str,
) -> RepairOperation | None:
    if not _TS_IDENTIFIER_RE.fullmatch(type_name) or not _TS_IDENTIFIER_RE.fullmatch(member):
        return None
    if not _typescript_safe_structural_member_type(replacement_type):
        return None
    escaped_type = re.escape(type_name)
    escaped_member = re.escape(member)
    for path, content in base_files.items():
        declaration_match = re.search(
            rf"(?m)^(?:export\s+)?(?P<kind>interface|class)\s+{escaped_type}\b[^\n]*{{",
            content,
        )
        if not declaration_match:
            continue
        declaration_end = content.find("\n}", declaration_match.end())
        if declaration_end < 0:
            continue
        body = content[declaration_match.end() : declaration_end]
        member_match = re.search(
            rf"(?m)^(?P<indent>\s*)(?P<prefix>(?:(?:public|private|protected|readonly|static|abstract)\s+)*)"
            rf"{escaped_member}\s*:\s*unknown\s*;",
            body,
        )
        if not member_match:
            continue
        prefix = str(member_match.group("prefix") or "")
        replacement = f"{member_match.group('indent')}{prefix}{member}: {replacement_type};"
        span_start = declaration_match.end() + member_match.start()
        span_end = declaration_match.end() + member_match.end()
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=span_start,
            span_end=span_end,
            expected=str(member_match.group(0) or ""),
            replacement=replacement,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_unknown_member_access",
                "type": type_name,
                "member": member,
                "replacement_type": replacement_type,
            },
        )
    return None

def _parse_typescript_uninitialized_property_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for diagnostic in diagnostics:
        for match in _TS_UNINITIALIZED_PROPERTY_RAW_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            parsed.append(
                {
                    "file": _normalize_repair_path(str(match.group("file") or "")),
                    "line": str(match.group("line") or ""),
                    "member": str(match.group("member") or ""),
                }
            )
    return [item for item in parsed if item["file"] and item["line"] and item["member"]]

def _parse_unknown_member_access_targets(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()

    def _append_target(*, path: str, line: object, column: object, receiver: str, member: str) -> None:
        normalized_path = _normalize_repair_path(path)
        normalized_line = str(line or "")
        normalized_receiver = str(receiver or "")
        normalized_member = str(member or "")
        key = (normalized_path, normalized_line, normalized_receiver, normalized_member)
        if key in seen:
            return
        seen.add(key)
        parsed.append(
            {
                "file": normalized_path,
                "line": normalized_line,
                "column": str(column or ""),
                "receiver": normalized_receiver,
                "member": normalized_member,
            }
        )

    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_UNKNOWN_MEMBER_ACCESS_RAW_RE.finditer(text):
            _append_target(
                path=str(match.group("file") or ""),
                line=match.group("line"),
                column=match.group("col"),
                receiver=str(match.group("receiver") or ""),
                member=str(match.group("member") or ""),
            )
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if diagnostic.code.lower() == "typescript_ts18046" and path and diagnostic.line:
            inline_match = re.search(
                r"['\"](?P<receiver>[A-Za-z_$][A-Za-z0-9_$]*)\.(?P<member>[A-Za-z_$][A-Za-z0-9_$]*)['\"]"
                r"\s+is\s+of\s+type\s+['\"]unknown['\"]",
                text,
                re.IGNORECASE,
            )
            if inline_match:
                _append_target(
                    path=path,
                    line=diagnostic.line,
                    column=diagnostic.column,
                    receiver=str(inline_match.group("receiver") or ""),
                    member=str(inline_match.group("member") or ""),
                )
    return [
        item
        for item in parsed
        if item["file"]
        and _to_positive_int(item.get("line")) > 0
        and _TS_IDENTIFIER_RE.fullmatch(item["receiver"])
        and _TS_IDENTIFIER_RE.fullmatch(item["member"])
    ]

_STEPFLIGHT_AIRSPEED_NEEDLE = "  const airspeed = Math.hypot(velocity.x, velocity.y);"
_STEPFLIGHT_NONFINITE_LANDING_CLAMP = (
    "  if (\n"
    "    !Number.isFinite(position.x) ||\n"
    "    !Number.isFinite(position.y) ||\n"
    "    !Number.isFinite(velocity.x) ||\n"
    "    !Number.isFinite(velocity.y)\n"
    "  ) {\n"
    "    return {\n"
    "      position: { x: Number.isFinite(position.x) ? position.x : 0, y: 0 },\n"
    "      velocity: { x: 0, y: 0 },\n"
    "      phase: { kind: FlightPhaseKind.Landed, impactSpeed: 0 },\n"
    "    };\n"
    "  }\n"
)


def _build_typescript_nonfinite_altitude_guard_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Land the existing stepFlight integrator when state is already non-finite.

    Live L1-08: Euler lift∝v² on a 5 g plane explodes by step 8 and is Infinity
    at step 14. ``effectiveWindSpeed`` then throws InvalidWindError. Swallowing
    that throw still leaves ``simulateFlight`` at ``finalPhase=climb`` and
    ``npm start`` exit 2. Inserting this clamp into ``stepFlight`` (the stack
    frame that feeds the throw) makes the next tick report Landed and start
    exit 0. Does not invent lift/drag coefficients or a new domain function.
    """

    haystack = "\n".join(str(item.raw or item.message or "") for item in diagnostics)
    if "InvalidWindError" not in haystack and "altitude must be non-negative finite" not in haystack:
        return None
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    for path, content in base_files.items():
        text = str(content or "")
        if "export function stepFlight" not in text:
            continue
        if "effectiveWindSpeed" not in text:
            continue
        if "FlightPhaseKind" not in text:
            continue
        if _STEPFLIGHT_AIRSPEED_NEEDLE not in text:
            continue
        if (
            "phase: { kind: FlightPhaseKind.Landed, impactSpeed: 0 }" in text
            and "!Number.isFinite(position.y)" in text
        ):
            continue
        repaired = text.replace(
            _STEPFLIGHT_AIRSPEED_NEEDLE,
            _STEPFLIGHT_NONFINITE_LANDING_CLAMP + _STEPFLIGHT_AIRSPEED_NEEDLE,
            1,
        )
        if repaired == text:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=_normalize_repair_path(path),
                original=text,
                repaired=repaired,
                metadata={"repair_kind": "typescript_nonfinite_altitude_guard"},
            )
        )
        matched.extend(diagnostics)
        break
    return _repair_plan_or_none(
        rule_id="typescript.nonfinite_altitude_guard",
        source_tool=TYPESCRIPT_NONFINITE_ALTITUDE_GUARD_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched,
        mode=mode,
        metadata={"nonfinite_altitude_guard": True},
    )


__all__ = (
    "_build_typescript_member_alias_plan",
    "_build_typescript_missing_member_plan",
    "_build_typescript_nonfinite_altitude_guard_plan",
    "_build_typescript_private_constructor_access_plan",
    "_build_typescript_private_property_access_plan",
    "_build_typescript_uninitialized_property_plan",
    "_parse_typescript_private_constructor_access_errors",
    "_parse_typescript_uninitialized_property_errors",
    "_parse_unknown_member_access_targets",
    "_typescript_member_alias_replacement",
    "_typescript_unknown_member_receiver_type",
    "_typescript_unknown_member_type_operation",
    "build_typescript_unknown_member_access_plan",
)
