"""Go-specific deterministic repairs for module/import consistency."""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_GO_MOD_MODULE_RE = re.compile(r"^module\s+(\S+)", re.MULTILINE)
_GO_IMPORT_RE = re.compile(r'"([^"]+)"')
_GO_STANDARD_PREFIXES = frozenset(
    {
        "bufio",
        "bytes",
        "compress",
        "context",
        "crypto",
        "database",
        "embed",
        "encoding",
        "errors",
        "expvar",
        "flag",
        "fmt",
        "go",
        "hash",
        "html",
        "image",
        "index",
        "io",
        "log",
        "math",
        "mime",
        "net",
        "os",
        "path",
        "plugin",
        "reflect",
        "regexp",
        "runtime",
        "sort",
        "strconv",
        "strings",
        "sync",
        "syscall",
        "testing",
        "text",
        "time",
        "unicode",
        "unsafe",
        "internal",
        "cmd",
    }
)


def _parse_go_mod_module(workspace: Path) -> str:
    go_mod = workspace / "go.mod"
    if not go_mod.is_file():
        return ""
    try:
        text = go_mod.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = _GO_MOD_MODULE_RE.search(text)
    return match.group(1).strip() if match else ""


def _collect_go_import_prefixes(workspace: Path) -> set[str]:
    prefixes: set[str] = set()
    for go_file in workspace.rglob("*.go"):
        try:
            text = go_file.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in _GO_IMPORT_RE.finditer(text):
            imp = match.group(1).strip()
            if imp and "/" in imp and not imp.startswith((".", "..")):
                prefixes.add(imp.split("/")[0])
    return prefixes


def _is_local_import(imp: str, module: str) -> bool:
    if not module:
        return False
    return imp == module or imp.startswith(f"{module}/")


def _looks_like_standard_library(prefix: str) -> bool:
    return prefix in _GO_STANDARD_PREFIXES or "." not in prefix


def detect_go_module_import_drift(workspace: Path) -> dict[str, str]:
    """Detect the mismatched module prefix used in Go import paths.

    Returns a mapping ``{wrong_prefix: module_name}`` when the workspace
    contains a ``go.mod`` whose module name differs from the prefix used
    in ``import`` statements.
    """
    module = _parse_go_mod_module(workspace)
    if not module:
        return {}
    module_prefix = module.split("/")[-1]
    prefixes = _collect_go_import_prefixes(workspace)
    drift: dict[str, str] = {}
    for prefix in prefixes:
        if _is_local_import(prefix, module) or _looks_like_standard_library(prefix):
            continue
        if prefix == module_prefix:
            continue
        # Check if this prefix's full imports look like they should belong to
        # the module — they reference local subdirectories like `prefix/src/...`
        for go_file in workspace.rglob("*.go"):
            try:
                text = go_file.read_text(encoding="utf-8")
            except OSError:
                continue
            for match in _GO_IMPORT_RE.finditer(text):
                imp = match.group(1).strip()
                if imp.startswith(f"{prefix}/"):
                    suffix = imp[len(prefix) :]
                    local_path = workspace / suffix.lstrip("/")
                    if local_path.exists():
                        drift[prefix] = module
                        return drift
    return drift


def repair_go_module_imports(workspace: Path) -> list[dict[str, str]]:
    """Repair Go import paths that use the wrong module prefix.

    Returns a list of repair records: ``[{file, before, after}]``.
    """
    drift = detect_go_module_import_drift(workspace)
    if not drift:
        return []
    repairs: list[dict[str, str]] = []
    for wrong_prefix, module in drift.items():
        for go_file in workspace.rglob("*.go"):
            try:
                original = go_file.read_text(encoding="utf-8")
            except OSError:
                continue
            repaired = original.replace(f'"{wrong_prefix}/', f'"{module}/')
            repaired = repaired.replace(f"'{wrong_prefix}/", f"'{module}/")
            if repaired != original:
                go_file.write_text(repaired, encoding="utf-8")
                repairs.append(
                    {
                        "file": str(go_file.relative_to(workspace)),
                        "before": wrong_prefix,
                        "after": module,
                    }
                )
    # Also fix go.mod if it references the wrong prefix in a require block
    go_mod = workspace / "go.mod"
    if go_mod.is_file():
        for wrong_prefix, module in drift.items():
            try:
                original = go_mod.read_text(encoding="utf-8")
            except OSError:
                continue
            repaired = original.replace(f"{wrong_prefix}/", f"{module}/")
            if repaired != original:
                go_mod.write_text(repaired, encoding="utf-8")
    logger.info("Go module import repair: %d file(s) fixed, drift=%s", len(repairs), drift)
    return repairs


# ---------------------------------------------------------------------------
# Bare local import prefix repair
# ---------------------------------------------------------------------------


def repair_go_bare_local_imports(workspace: Path) -> list[dict[str, str]]:
    """Add module prefix to bare local imports like ``"src/models"``.

    When the Director generates ``import "src/models"`` instead of
    ``import "module/src/models"``, this repair prepends the module name.
    """
    module = _parse_go_mod_module(workspace)
    if not module:
        return []
    pkg_dirs = _discover_go_package_dirs(workspace)
    if not pkg_dirs:
        return []

    repairs: list[dict[str, str]] = []
    for go_file in workspace.rglob("*.go"):
        try:
            original = go_file.read_text(encoding="utf-8")
        except OSError:
            continue
        repaired = original
        for match in _GO_IMPORT_RE.finditer(original):
            imp = match.group(1).strip()
            # Skip: empty, already has module prefix, standard library, external
            if not imp or imp.startswith(module) or "." in imp.split("/")[0]:
                continue
            # Check if this bare path matches a known package directory
            if imp in pkg_dirs or any(imp.startswith(d + "/") for d in pkg_dirs):
                new_imp = f"{module}/{imp}"
                repaired = repaired.replace(f'"{imp}"', f'"{new_imp}"')
            elif "/" not in imp:
                # Single-segment bare name (e.g., "engine") — match against
                # package directory basenames (e.g., src/engine → engine).
                for d in pkg_dirs:
                    if d.rsplit("/", 1)[-1] == imp:
                        new_imp = f"{module}/{d}"
                        repaired = repaired.replace(f'"{imp}"', f'"{new_imp}"')
                        break
        if repaired != original:
            go_file.write_text(repaired, encoding="utf-8")
            repairs.append(
                {
                    "file": str(go_file.relative_to(workspace)),
                    "before": "bare local import",
                    "after": f"prefixed with {module}",
                }
            )
    if repairs:
        logger.info("Go bare local import repair: %d file(s) fixed", len(repairs))
    return repairs


# ---------------------------------------------------------------------------
# Bare import string repair (missing "import" keyword)
# ---------------------------------------------------------------------------

_BARE_IMPORT_STRING_RE = re.compile(r'^(\s+)"([^"]+)"\s*$', re.MULTILINE)


def repair_go_bare_import_strings(workspace: Path) -> list[dict[str, str]]:
    """Fix bare quoted strings that should be import statements.

    When the Director generates:
        package models

        "fmt"
        type Foo struct { ... }

    This repairs it to:
        package models

        import "fmt"
        type Foo struct { ... }
    """
    repairs: list[dict[str, str]] = []
    for go_file in workspace.rglob("*.go"):
        if go_file.name.endswith("_test.go"):
            continue
        try:
            original = go_file.read_text(encoding="utf-8")
        except OSError:
            continue
        lines = original.split("\n")
        modified = False
        in_import_block = False
        past_package = False

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("package "):
                past_package = True
                continue
            if stripped == "import (" or stripped.startswith("import ("):
                in_import_block = True
                continue
            if in_import_block:
                if stripped == ")":
                    in_import_block = False
                continue
            if stripped.startswith("import "):
                continue
            # After package declaration and before any type/func/const/var,
            # a bare quoted string should be an import.
            if (
                past_package
                and stripped
                and stripped.startswith('"')
                and stripped.endswith('"')
                and not line.startswith("\t\t")
                and not line.startswith("    ")
            ):
                lines[i] = f"import {stripped}"
                modified = True

        if modified:
            repaired = "\n".join(lines)
            go_file.write_text(repaired, encoding="utf-8")
            repairs.append(
                {
                    "file": str(go_file.relative_to(workspace)),
                    "before": "bare import string",
                    "after": "import statement",
                }
            )
    if repairs:
        logger.info("Go bare import string repair: %d file(s) fixed", len(repairs))
    return repairs


# ---------------------------------------------------------------------------
# Nested import keyword repair
# ---------------------------------------------------------------------------

_NESTED_IMPORT_RE = re.compile(r"^(\s+)import\s+\"([^\"]+)\"\s*$", re.MULTILINE)


def repair_go_nested_import_keyword(workspace: Path) -> list[dict[str, str]]:
    """Fix ``import "pkg"`` inside ``import (...)`` blocks.

    When the Director generates:
        import (
            "errors"
            import "fmt"   // <-- wrong: extra import keyword
        )

    This repairs it to:
        import (
            "errors"
            "fmt"
        )

    Only fixes lines that are actually inside an import block (between
    ``import (`` and the matching ``)``). Standalone ``import "pkg"``
    statements are left untouched.
    """
    repairs: list[dict[str, str]] = []
    for go_file in workspace.rglob("*.go"):
        if go_file.name.endswith("_test.go"):
            continue
        try:
            original = go_file.read_text(encoding="utf-8")
        except OSError:
            continue
        # Only fix import "..." lines that are INSIDE an import (...) block.
        lines = original.split("\n")
        modified = False
        in_import_block = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("import (") or stripped == "import (":
                in_import_block = True
                continue
            if in_import_block and stripped == ")":
                in_import_block = False
                continue
            if in_import_block:
                m = re.match(r'^(\s+)import\s+"([^"]+)"\s*$', line)
                if m:
                    lines[i] = f'{m.group(1)}"{m.group(2)}"'
                    modified = True
        if modified:
            repaired = "\n".join(lines)
            go_file.write_text(repaired, encoding="utf-8")
            repairs.append(
                {
                    "file": str(go_file.relative_to(workspace)),
                    "before": "import inside import block",
                    "after": "bare string",
                }
            )
    if repairs:
        logger.info("Go nested import keyword repair: %d file(s) fixed", len(repairs))
    return repairs


# ---------------------------------------------------------------------------
# Sub-path hallucination repair
# ---------------------------------------------------------------------------


def _discover_go_package_dirs(workspace: Path) -> set[str]:
    """Return relative directory paths that contain ``.go`` files."""
    dirs: set[str] = set()
    try:
        for go_file in workspace.rglob("*.go"):
            rel_dir = str(go_file.parent.relative_to(workspace))
            if rel_dir == "." or "/." in rel_dir or rel_dir.startswith(".") or "/vendor/" in rel_dir:
                continue
            dirs.add(rel_dir)
    except OSError:
        pass
    return dirs


def repair_go_import_subpaths(workspace: Path) -> list[dict[str, str]]:
    """Repair hallucinated sub-paths in Go import statements.

    When the Director generates ``module/example/pet-ascii/src/engine`` but
    the actual package directory is ``src/engine``, rewrite to
    ``module/src/engine``.

    Returns a list of repair records: ``[{file, before, after}]``.
    """
    module = _parse_go_mod_module(workspace)
    if not module:
        return []
    pkg_dirs = _discover_go_package_dirs(workspace)
    if not pkg_dirs:
        return []

    prefix = f"{module}/"
    repairs: list[dict[str, str]] = []

    for go_file in workspace.rglob("*.go"):
        try:
            original = go_file.read_text(encoding="utf-8")
        except OSError:
            continue
        repaired = original
        for match in _GO_IMPORT_RE.finditer(original):
            imp = match.group(1).strip()
            if not imp.startswith(prefix):
                continue
            subpath = imp[len(prefix) :]
            if subpath in pkg_dirs:
                continue  # Already valid.
            # Suffix-match: find the longest actual dir that is a suffix of the subpath.
            best = ""
            for d in pkg_dirs:
                if (subpath.endswith("/" + d) or subpath.endswith(d)) and len(d) > len(best):
                    best = d
            if best:
                new_imp = f"{module}/{best}"
                repaired = repaired.replace(f'"{imp}"', f'"{new_imp}"')
        if repaired != original:
            go_file.write_text(repaired, encoding="utf-8")
            repairs.append(
                {
                    "file": str(go_file.relative_to(workspace)),
                    "before": "hallucinated_subpath",
                    "after": "corrected_subpath",
                }
            )
    if repairs:
        logger.info("Go sub-path repair: %d file(s) fixed", len(repairs))
    return repairs


# ---------------------------------------------------------------------------
# Duplicate declaration repair (merge files in the same package)
# ---------------------------------------------------------------------------


def _dedup_within_single_file(go_file: Path) -> bool:
    """Remove duplicate type/func/const/var declarations within a single Go file.

    When the Director generates two conflicting definitions (e.g.
    ``type Mood int`` with iota constants AND ``type Mood string`` with
    string constants), this function keeps the first block and comments
    out the entire second block (type + related constants + methods).

    Returns True if the file was modified.
    """
    try:
        text = go_file.read_text(encoding="utf-8")
    except OSError:
        return False

    _decl_start_re = re.compile(
        r"^(type\s+\w+[\s{(]|func\s+(?:\([^)]+\)\s+)?\w+\s*[\[(]|const\s+\w+|var\s+\w+)",
        re.MULTILINE,
    )

    # Pass 1: Identify all declaration names and find duplicates.
    seen: dict[str, int] = {}  # key → first occurrence line
    duplicate_names: set[str] = set()  # names that appear more than once

    for line in text.split("\n"):
        m = _decl_start_re.match(line)
        if m:
            name_m = re.search(r"(type|func|const|var)\s+(?:\([^)]+\)\s+)?(\w+)", m.group(0))
            if name_m:
                key = f"{name_m.group(1)}_{name_m.group(2)}"
                if key in seen:
                    duplicate_names.add(name_m.group(2))
                else:
                    seen[key] = 0

    if not duplicate_names:
        return False

    # Pass 2: Comment out the SECOND occurrence of each duplicate,
    # plus all subsequent lines that reference the duplicate name
    # (constants, methods, etc.) until an unrelated declaration.
    lines = text.split("\n")
    modified = False
    skip_until_close = False
    brace_depth = 0
    skip_related_name: str | None = None  # When skipping, which name?
    seen_pass2: set[str] = set()

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Handle multi-line block comment-out.
        if skip_until_close:
            brace_depth += line.count("{") - line.count("}")
            lines[i] = f"// [dedup-intra] {stripped}"
            modified = True
            if brace_depth <= 0:
                skip_until_close = False
                skip_related_name = None
            continue

        # Check if this is a top-level declaration.
        m = _decl_start_re.match(line)
        if m:
            name_m = re.search(r"(type|func|const|var)\s+(?:\([^)]+\)\s+)?(\w+)", m.group(0))
            if name_m:
                kind = name_m.group(1)
                name = name_m.group(2)
                key = f"{kind}_{name}"

                # If we were skipping related lines and hit an unrelated decl, stop.
                if skip_related_name and name != skip_related_name:
                    # Check if it's a method on the duplicate type.
                    recv_m = re.search(r"func\s+\([^)]+\s+\*?(\w+)\)", line)
                    if not recv_m or recv_m.group(1) != skip_related_name:
                        skip_related_name = None

                if key in seen_pass2 and name in duplicate_names:
                    # Second occurrence — comment out this and related lines.
                    skip_related_name = name
                    if "{" in line:
                        skip_until_close = True
                        brace_depth = line.count("{") - line.count("}")
                        if brace_depth <= 0:
                            skip_until_close = False
                    lines[i] = f"// [dedup-intra] {stripped}"
                    modified = True
                else:
                    seen_pass2.add(key)
            continue

        # If we're skipping related lines, comment out lines that reference
        # the duplicate name (constants, string values, etc.).
        if skip_related_name and stripped and not stripped.startswith("//") and not stripped.startswith("package"):
            # Comment out const/var values and method bodies related to the dup.
            lines[i] = f"// [dedup-intra] {stripped}"
            modified = True

    if modified:
        go_file.write_text("\n".join(lines), encoding="utf-8")
    return modified


def repair_go_duplicate_declarations(workspace: Path) -> list[dict[str, str]]:
    """Merge Go files when ``go vet`` reports redeclaration errors.

    When the Director generates overlapping type/func/const declarations
    across multiple files in the same package, merge non-test ``.go`` files
    in the offending package into the largest file.

    Returns a list of repair records: ``[{file, action}]``.
    """
    import subprocess

    go_binary = _resolve_go_binary()
    if not go_binary:
        return []

    try:
        result = subprocess.run(
            [go_binary, "vet", "./..."],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []

    stderr = result.stderr or ""
    if "redeclared" not in stderr and "already declared" not in stderr:
        return []

    repairs: list[dict[str, str]] = []

    # Pass 1: Intra-file dedup (same declaration twice in one file).
    for go_file in workspace.rglob("*.go"):
        if go_file.name.endswith("_test.go"):
            continue
        if _dedup_within_single_file(go_file):
            repairs.append({"file": str(go_file.relative_to(workspace)), "action": "intra_file_dedup"})

    # Re-run vet after intra-file dedup to see if cross-file issues remain.
    if repairs:
        try:
            result = subprocess.run(
                [go_binary, "vet", "./..."],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=30,
            )
            stderr = result.stderr or ""
            if "redeclared" not in stderr and "already declared" not in stderr:
                logger.info("Go intra-file dedup resolved all redeclarations: %d file(s)", len(repairs))
                return repairs
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass

    # Pass 2: Cross-file merge (same declaration in different files).
    module = _parse_go_mod_module(workspace)
    pkg_error_re = re.compile(r"^#\s+(\S+)", re.MULTILINE)

    # Parse which packages have redeclaration errors.
    error_pkgs: set[str] = set()
    lines = stderr.split("\n")
    current_pkg = ""
    for line in lines:
        pkg_match = pkg_error_re.match(line)
        if pkg_match:
            current_pkg = pkg_match.group(1)
        if ("redeclared" in line or "already declared" in line) and current_pkg:
            error_pkgs.add(current_pkg)

    if not error_pkgs:
        return repairs

    for pkg_path in error_pkgs:
        # Convert module path to directory.
        if module and pkg_path.startswith(f"{module}/"):
            rel_dir = pkg_path[len(module) + 1 :]
        elif "/" in pkg_path:
            rel_dir = "/".join(pkg_path.split("/")[1:])
        else:
            rel_dir = "."
        pkg_dir = workspace / rel_dir
        if not pkg_dir.is_dir():
            continue

        src_files = sorted(
            (p for p in pkg_dir.glob("*.go") if not p.name.endswith("_test.go")),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
        if len(src_files) < 2:
            continue

        owner = src_files[0]
        owner_text = owner.read_text(encoding="utf-8", errors="replace")
        owner_imports: set[str] = set()
        for m in re.finditer(r'^import\s*\((.*?)\)|^import\s+"[^"]+"\s*$', owner_text, re.MULTILINE | re.DOTALL):
            if m.group(1):
                for line in m.group(1).split("\n"):
                    stripped = line.strip()
                    if stripped and not stripped.startswith("//"):
                        owner_imports.add(stripped)
            else:
                owner_imports.add(m.group(0))

        merged_sections: list[str] = []
        extra_imports: set[str] = set()
        files_to_remove: list[Path] = []

        for src in src_files[1:]:
            text = src.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'^import\s*\((.*?)\)|^import\s+"[^"]+"\s*$', text, re.MULTILINE | re.DOTALL):
                if m.group(1):
                    for line in m.group(1).split("\n"):
                        stripped = line.strip()
                        if stripped and not stripped.startswith("//") and stripped not in owner_imports:
                            extra_imports.add(stripped)
                else:
                    imp_line = m.group(0).strip()
                    if imp_line not in owner_imports:
                        extra_imports.add(imp_line)
            section = re.sub(r"^package\s+\w+\s*$", "", text, flags=re.MULTILINE)
            section = re.sub(r'^import\s*\((.*?)\)|^import\s+"[^"]+"\s*$', "", section, flags=re.MULTILINE | re.DOTALL)
            section = re.sub(r"\n{3,}", "\n\n", section).strip()
            if section:
                merged_sections.append(f"// --- merged from {src.name} ---\n{section}")
            files_to_remove.append(src)

        if not merged_sections:
            continue

        owner_body = re.sub(
            r'^import\s*\((.*?)\)|^import\s+"[^"]+"\s*$', "", owner_text, flags=re.MULTILINE | re.DOTALL
        )
        owner_body = re.sub(r"^package\s+\w+\s*$", "", owner_body, flags=re.MULTILINE)
        owner_body = re.sub(r"\n{3,}", "\n\n", owner_body).strip()

        pkg_match = re.search(r"^package\s+(\w+)", owner_text, re.MULTILINE)
        pkg_decl = pkg_match.group(0) if pkg_match else "package main"

        all_imports = sorted(owner_imports | extra_imports)
        import_block = ""
        if all_imports:
            import_lines = "\n".join(f"\t{imp}" for imp in all_imports)
            import_block = f"\nimport (\n{import_lines}\n)\n"

        new_text = f"{pkg_decl}\n{import_block}\n{owner_body}\n\n"
        new_text += "\n\n".join(merged_sections) + "\n"

        owner.write_text(new_text, encoding="utf-8")
        for src in files_to_remove:
            src.unlink()
            repairs.append({"file": str(src.relative_to(workspace)), "action": "merged_into_owner"})

    if repairs:
        logger.info("Go duplicate declaration repair: %d file(s) merged", len(repairs))
    return repairs


def _resolve_go_binary() -> str | None:
    """Locate the ``go`` binary, preferring >= 1.23."""
    import os
    import shutil

    found = shutil.which("go")
    if found:
        return found
    home = Path(os.path.expanduser("~"))
    for candidate in (
        home / ".local" / "go123" / "bin" / "go",
        home / ".local" / "go124" / "bin" / "go",
        home / ".local" / "go" / "bin" / "go",
        home / "go" / "bin" / "go",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None
