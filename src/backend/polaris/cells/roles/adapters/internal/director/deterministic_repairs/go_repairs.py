"""Go-specific deterministic repairs for module/import consistency."""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_GO_MOD_MODULE_RE = re.compile(r"^module\s+(\S+)", re.MULTILINE)
_GO_IMPORT_RE = re.compile(r'"([^"]+)"')
_GO_STANDARD_PREFIXES = frozenset({
    "bufio", "bytes", "compress", "context", "crypto", "database",
    "embed", "encoding", "errors", "expvar", "flag", "fmt", "go",
    "hash", "html", "image", "index", "io", "log", "math", "mime",
    "net", "os", "path", "plugin", "reflect", "regexp", "runtime",
    "sort", "strconv", "strings", "sync", "syscall", "testing",
    "text", "time", "unicode", "unsafe", "internal", "cmd",
})


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
                    suffix = imp[len(prefix):]
                    candidate = f"{module}{suffix}"
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
                repairs.append({
                    "file": str(go_file.relative_to(workspace)),
                    "before": wrong_prefix,
                    "after": module,
                })
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
