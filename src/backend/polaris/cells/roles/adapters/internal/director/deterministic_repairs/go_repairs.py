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
        return []

    repairs: list[dict[str, str]] = []
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
