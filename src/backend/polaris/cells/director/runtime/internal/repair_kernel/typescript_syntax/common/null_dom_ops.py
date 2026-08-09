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

"""Shared TypeScript repair helpers: null_dom_ops."""

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


__all__ = (
    "_typescript_global_guard_precedes",
    "_repair_html_entrypoint_quality_text_with_metadata",
    "_html_javascript_entrypoint_for_typescript_source",
    "_html_compiled_javascript_entrypoint_for_script",
    "_html_compiled_typescript_output_path",
    "_html_typescript_compiler_option",
    "_repair_typescript_multiline_dom_handle_declarations",
    "_typescript_nullable_guard_in_text_window",
    "_typescript_base_files_have_dom_lib",
    "_remove_typescript_local_dom_shims",
    "_typescript_local_dom_shim_start_symbol",
    "_typescript_block_end",
)
