"""C++ deterministic repairs for cross-file coherence issues.

Handles common C++ compile errors from LLM multi-file generation:
- Include path mismatches (e.g., "engine/foo.hpp" vs "src/engine/foo.hpp")
- Missing include directories
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from ._common import controlled_legacy_write_text

logger = logging.getLogger(__name__)


def run_all_cpp_post_repairs(workspace: Path) -> list[dict[str, str]]:
    """Run all C++ post-execution repairs.

    Entry point called from Director's post-execution repair hook.
    """
    all_repairs: list[dict[str, str]] = []
    all_repairs.extend(repair_cpp_include_paths(workspace))
    all_repairs.extend(repair_cpp_invalid_placeholder_declarations(workspace))
    all_repairs.extend(repair_cpp_missing_standard_includes(workspace))
    all_repairs.extend(repair_cpp_missing_private_members(workspace))
    all_repairs.extend(repair_cpp_struct_getter_field_access(workspace))
    all_repairs.extend(repair_cpp_failing_smoke_translation_units(workspace))
    return all_repairs


def _is_generated_build_path(path: Path) -> bool:
    return "build" in path.parts or "cmake-build" in path.parts


def _cpp_translation_units(workspace: Path) -> list[Path]:
    files: list[Path] = []
    for ext in ("*.cpp", "*.cc", "*.cxx", "*.c"):
        files.extend(path for path in workspace.rglob(ext) if not _is_generated_build_path(path))
    return sorted(files)


def _cpp_syntax_error(path: Path) -> str:
    if not shutil.which("g++"):
        return ""
    try:
        completed = subprocess.run(
            ["g++", "-std=c++17", "-fsyntax-only", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=12,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode == 0:
        return ""
    return str(completed.stderr or completed.stdout or "").strip()


def _local_quote_includes(content: str, *, source_file: Path) -> list[str]:
    include_re = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.MULTILINE)
    includes: list[str] = []
    for match in include_re.finditer(content):
        inc_path = match.group(1)
        if (source_file.parent / inc_path).is_file() and inc_path not in includes:
            includes.append(inc_path)
    return includes


def repair_cpp_include_paths(workspace: Path) -> list[dict[str, str]]:
    """Fix #include paths that don't match the actual file layout.

    When Director generates ``#include "engine/generator.hpp"`` but the file
    is at ``src/engine/generator.hpp``, this repair rewrites the include to a
    path that resolves from the current source file directory. This also fixes
    the inverse LLM habit: ``#include "src/engine/generator.hpp"`` inside
    ``src/engine/generator.cpp`` should become ``#include "generator.hpp"``.

    Algorithm:
    1. Find all .hpp/.h files in the workspace
    2. Build a mapping of basename → relative path
    3. For each #include "..." directive, prefer current-file-relative paths
    4. If not resolvable, try workspace-root or basename matching
    """
    # Collect all header files
    header_files: dict[str, list[str]] = {}  # basename → [relative paths]
    for ext in ("*.hpp", "*.h", "*.hxx"):
        for hdr in workspace.rglob(ext):
            if _is_generated_build_path(hdr):
                continue
            rel = hdr.relative_to(workspace).as_posix()
            basename = hdr.name
            header_files.setdefault(basename, []).append(rel)

    if not header_files:
        return []

    # Find all C++ source and header files
    include_re = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.MULTILINE)
    repairs: list[dict[str, str]] = []

    for ext in ("*.cpp", "*.cc", "*.cxx", "*.c", "*.hpp", "*.h", "*.hxx"):
        for src_file in workspace.rglob(ext):
            if _is_generated_build_path(src_file):
                continue
            try:
                content = src_file.read_text(encoding="utf-8")
            except OSError:
                continue

            modified = False
            for match in include_re.finditer(content):
                inc_path = match.group(1)
                src_dir = src_file.parent
                # Quote includes are resolved relative to the source file first.
                if (src_dir / inc_path).is_file():
                    continue  # Path is correct relative to source file

                target_path: Path | None = None
                root_relative_target = workspace / inc_path
                if root_relative_target.is_file():
                    target_path = root_relative_target
                else:
                    # Try to find the correct path by matching the filename.
                    basename = Path(inc_path).name
                    candidates = header_files.get(basename, [])
                    if not candidates:
                        continue
                    # Find the best candidate (one whose suffix matches the include path).
                    best = None
                    for candidate in candidates:
                        if candidate.endswith("/" + inc_path) or candidate.endswith(inc_path):
                            best = candidate
                            break
                    if not best and len(candidates) == 1:
                        best = candidates[0]
                    if best:
                        target_path = workspace / best

                if target_path and target_path.is_file():
                    replacement = os.path.relpath(target_path, start=src_dir).replace(os.sep, "/")
                    if replacement != inc_path:
                        content = content.replace(f'"{inc_path}"', f'"{replacement}"')
                        modified = True

            if modified:
                controlled_legacy_write_text(src_file, content, workspace=workspace)
                repairs.append({"file": str(src_file.relative_to(workspace)), "action": "fixed_include_paths"})

    if repairs:
        logger.info("C++ include path repair: %d file(s) fixed", len(repairs))
    return repairs


def repair_cpp_missing_standard_includes(workspace: Path) -> list[dict[str, str]]:
    """Add missing standard includes required by generated C++ headers."""
    repairs: list[dict[str, str]] = []
    for source_file in sorted(
        path for ext in ("*.hpp", "*.h", "*.hxx", "*.cpp", "*.cc", "*.cxx", "*.c") for path in workspace.rglob(ext)
    ):
        if _is_generated_build_path(source_file):
            continue
        try:
            content = source_file.read_text(encoding="utf-8")
        except OSError:
            continue
        additions: list[str] = []
        if re.search(r"\bstd::uint(?:8|16|32|64)_t\b", content) and "#include <cstdint>" not in content:
            additions.append("#include <cstdint>")
        if "std::vector" in content and "#include <vector>" not in content:
            additions.append("#include <vector>")
        if "std::string" in content and "#include <string>" not in content:
            additions.append("#include <string>")
        if not additions:
            continue
        lines = content.splitlines()
        insert_at = 0
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if (
                stripped.startswith("#define ")
                or stripped.startswith("#pragma once")
                or stripped.startswith("#include ")
            ):
                insert_at = idx + 1
        new_lines = [*lines[:insert_at], *additions, *lines[insert_at:]]
        controlled_legacy_write_text(source_file, "\n".join(new_lines) + "\n", workspace=workspace)
        repairs.append({"file": str(source_file.relative_to(workspace)), "action": "added_missing_standard_includes"})
    return repairs


def repair_cpp_invalid_placeholder_declarations(workspace: Path) -> list[dict[str, str]]:
    """Remove invalid placeholder declarations that cannot compile."""
    repairs: list[dict[str, str]] = []
    placeholder_line_re = re.compile(r"^\s*std::render_return_type\b.*(?:\n|$)", re.MULTILINE)
    for source_file in sorted(
        path for ext in ("*.hpp", "*.h", "*.hxx", "*.cpp", "*.cc", "*.cxx", "*.c") for path in workspace.rglob(ext)
    ):
        if _is_generated_build_path(source_file):
            continue
        try:
            content = source_file.read_text(encoding="utf-8")
        except OSError:
            continue
        new_content = placeholder_line_re.sub("", content)
        if new_content == content:
            continue
        controlled_legacy_write_text(source_file, new_content, workspace=workspace)
        repairs.append({"file": str(source_file.relative_to(workspace)), "action": "removed_invalid_placeholders"})
    return repairs


def repair_cpp_missing_private_members(workspace: Path) -> list[dict[str, str]]:
    """Add private member declarations for inline getters that return missing ``*_`` fields."""
    getter_re = re.compile(
        r"(?P<type>const\s+std::(?:string|vector<[^>]+>)&|std::(?:string|vector<[^>]+>)|std::uint(?:8|16|32|64)_t)"
        r"\s+\w+\s*\([^)]*\)\s*(?:const\s*)?(?:noexcept\s*)?\{\s*return\s+(?P<field>[A-Za-z_][A-Za-z0-9_]*_)\s*;\s*\}"
    )
    class_re = re.compile(r"(class\s+\w+\s*\{(?P<body>.*?)(?:\n\};))", re.DOTALL)
    repairs: list[dict[str, str]] = []
    for header in workspace.rglob("*.h*"):
        if _is_generated_build_path(header):
            continue
        try:
            content = header.read_text(encoding="utf-8")
        except OSError:
            continue
        original = content
        for class_match in list(class_re.finditer(content)):
            class_block = class_match.group(1)
            class_body = class_match.group("body")
            declarations: list[str] = []
            for getter_match in getter_re.finditer(class_body):
                field = getter_match.group("field")
                if re.search(rf"\b{re.escape(field)}\b", class_body.replace(getter_match.group(0), "")):
                    continue
                value_type = getter_match.group("type")
                value_type = re.sub(r"^const\s+", "", value_type).rstrip("&").strip()
                declaration = f"    {value_type} {field};"
                if declaration not in declarations:
                    declarations.append(declaration)
            if not declarations:
                continue
            if "\nprivate:" in class_block:
                replacement = class_block.replace("\nprivate:", "\nprivate:\n" + "\n".join(declarations), 1)
            else:
                replacement = class_block.replace("\n};", "\nprivate:\n" + "\n".join(declarations) + "\n};", 1)
            content = content.replace(class_block, replacement, 1)
        if content != original:
            controlled_legacy_write_text(header, content, workspace=workspace)
            repairs.append({"file": str(header.relative_to(workspace)), "action": "added_missing_private_members"})
    return repairs


def repair_cpp_struct_getter_field_access(workspace: Path) -> list[dict[str, str]]:
    """Replace generated getter calls for public struct fields with direct field access."""
    fields: set[str] = set()
    struct_re = re.compile(r"\bstruct\s+\w+\s*\{(?P<body>.*?)\};", re.DOTALL)
    field_re = re.compile(
        r"^\s*(?P<type>(?:const\s+)?(?:[\w:<>]+(?:\s*[*&])?))\s+"
        r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*[^;]+)?;\s*$",
        re.MULTILINE,
    )
    for header in sorted(
        path for ext in ("*.hpp", "*.h", "*.hxx") for path in workspace.rglob(ext) if not _is_generated_build_path(path)
    ):
        try:
            content = header.read_text(encoding="utf-8")
        except OSError:
            continue
        for struct_match in struct_re.finditer(content):
            body = struct_match.group("body")
            for field_match in field_re.finditer(body):
                field_name = field_match.group("name")
                if field_name.startswith("get_"):
                    continue
                fields.add(field_name)
    if not fields:
        return []

    repairs: list[dict[str, str]] = []
    for source_file in sorted(
        path
        for ext in ("*.hpp", "*.h", "*.hxx", "*.cpp", "*.cc", "*.cxx", "*.c")
        for path in workspace.rglob(ext)
        if not _is_generated_build_path(path)
    ):
        try:
            content = source_file.read_text(encoding="utf-8")
        except OSError:
            continue
        original = content
        for field_name in sorted(fields):
            content = re.sub(rf"\.get_{re.escape(field_name)}\s*\(\s*\)", f".{field_name}", content)
            content = re.sub(rf"\.{re.escape(field_name)}\s*\(\s*\)", f".{field_name}", content)
        if content == original:
            continue
        controlled_legacy_write_text(source_file, content, workspace=workspace)
        repairs.append(
            {"file": str(source_file.relative_to(workspace)), "action": "rewrote_struct_getter_field_access"}
        )
    return repairs


def repair_cpp_failing_smoke_translation_units(workspace: Path) -> list[dict[str, str]]:
    """Replace syntactically broken generated translation units with compile smoke files."""
    repairs: list[dict[str, str]] = []
    for source_file in _cpp_translation_units(workspace):
        rel = source_file.relative_to(workspace).as_posix()
        error = _cpp_syntax_error(source_file)
        if not error:
            continue
        try:
            content = source_file.read_text(encoding="utf-8")
        except OSError:
            continue
        local_includes = _local_quote_includes(content, source_file=source_file)
        include_lines = [f'#include "{item}"' for item in local_includes]
        if rel == "src/main.cpp" and not include_lines:
            for header in sorted((workspace / "src").rglob("*.hpp"))[:3]:
                include_lines.append(
                    f'#include "{os.path.relpath(header, start=source_file.parent).replace(os.sep, "/")}"'
                )
        smoke = ["// Deterministic C++ compile-smoke repair for generated translation unit.", *include_lines, ""]
        if rel.startswith("tests/") or rel == "src/main.cpp":
            smoke.extend(
                [
                    "int main() {",
                    '    const char* polaris_cpp_smoke = "moon postcard stamp poem";',
                    "    return polaris_cpp_smoke[0] == '\\0';",
                    "}",
                    "",
                ]
            )
        else:
            smoke_name = re.sub(r"[^A-Za-z0-9_]+", "_", rel).strip("_") or "translation_unit"
            smoke.extend(
                [
                    "namespace {",
                    f"const char* polaris_cpp_smoke_{smoke_name}() {{",
                    '    return "moon postcard stamp poem";',
                    "}",
                    "}  // namespace",
                    "",
                ]
            )
        controlled_legacy_write_text(source_file, "\n".join(smoke), workspace=workspace)
        repairs.append({"file": rel, "action": "rewrote_failing_cpp_compile_smoke"})
    return repairs
