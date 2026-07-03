"""Architecture fence: KERNELONE_* environment flag reads must be registered.

Blueprint: docs/blueprints/GOVERNANCE_MECHANIZATION_BLUEPRINT_20260703.md

Every production read of a ``KERNELONE_*`` environment variable with a literal
name must appear in ``KERNELONE_FLAG_REGISTRY``
(``polaris/kernelone/config/flag_registry.py``). Env reads whose name is not a
string literal (f-strings, variables, helper parameters) must be registered in
``DYNAMIC_ENV_READ_ALLOWLIST`` keyed by ``(relative file path, enclosing
function)`` — line numbers drift, function names do not.

The scanner is AST-based (not grep) so string constants that are never passed
to an environment read do not count as reads.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
KERNELONE_FLAG_PREFIX = "KERNELONE_"
REGISTRY_MODULE_PATH = "polaris/kernelone/config/flag_registry.py"
MODULE_LEVEL_MARKER = "<module>"

_HOW_TO_REGISTER_FLAG = (
    f"To register a new flag, add an entry to KERNELONE_FLAG_REGISTRY in {REGISTRY_MODULE_PATH}, e.g.:\n"
    '    "KERNELONE_MY_FLAG": FlagSpec(\n'
    '        name="KERNELONE_MY_FLAG",\n'
    "        default=None,\n"
    '        owner="<owning cell or subsystem>",\n'
    '        purpose="<what behavior the flag controls>",\n'
    '        registered_at="<YYYY-MM-DD>",\n'
    "        expiry=None,\n"
    "    ),\n"
    'Do not use owner="legacy_unowned" for new flags; that owner is reserved for the '
    "2026-07-03 governance sweep backfill."
)
_HOW_TO_ALLOWLIST_DYNAMIC_READ = (
    "To allowlist a dynamic environment read, add the "
    '("<relative file path>", "<enclosing function>") pair to DYNAMIC_ENV_READ_ALLOWLIST in '
    f"{REGISTRY_MODULE_PATH} with a comment naming the owner. Prefer literal KERNELONE_* names "
    "over dynamically composed ones so the flag itself stays registrable."
)


@dataclass(frozen=True)
class EnvReadScan:
    """Environment reads found in one Python source file.

    Attributes:
        literal_flag_reads: ``(flag_name, enclosing_function)`` pairs for reads
            whose name is a string literal starting with ``KERNELONE_``.
        dynamic_read_sites: enclosing-function markers for env reads whose name
            is not a string literal.
    """

    literal_flag_reads: tuple[tuple[str, str], ...]
    dynamic_read_sites: tuple[str, ...]


@dataclass
class _ScanState:
    environ_aliases: frozenset[str]
    getenv_aliases: frozenset[str]
    os_module_aliases: frozenset[str]
    literal_flag_reads: list[tuple[str, str]] = field(default_factory=list)
    dynamic_read_sites: list[str] = field(default_factory=list)


def _collect_import_aliases(tree: ast.AST) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    environ_aliases: set[str] = set()
    getenv_aliases: set[str] = set()
    os_module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os" or alias.name.startswith("os."):
                    os_module_aliases.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module == "os" and node.level == 0:
            for alias in node.names:
                if alias.name == "environ":
                    environ_aliases.add(alias.asname or alias.name)
                elif alias.name == "getenv":
                    getenv_aliases.add(alias.asname or alias.name)
    return frozenset(environ_aliases), frozenset(getenv_aliases), frozenset(os_module_aliases)


def _is_environ_expression(node: ast.expr, state: _ScanState) -> bool:
    if isinstance(node, ast.Attribute) and node.attr == "environ":
        return isinstance(node.value, ast.Name) and node.value.id in state.os_module_aliases
    return isinstance(node, ast.Name) and node.id in state.environ_aliases


def _env_key_expression(node: ast.Call) -> ast.expr | None:
    if node.args:
        return node.args[0]
    for keyword in node.keywords:
        if keyword.arg == "key":
            return keyword.value
    return None


def _record_env_read(key: ast.expr | None, scope: str, state: _ScanState) -> None:
    if key is None:
        return
    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        if key.value.startswith(KERNELONE_FLAG_PREFIX):
            state.literal_flag_reads.append((key.value, scope))
        return
    state.dynamic_read_sites.append(scope)


def _classify_call(node: ast.Call, scope: str, state: _ScanState) -> None:
    func = node.func
    if isinstance(func, ast.Attribute):
        if (func.attr == "get" and _is_environ_expression(func.value, state)) or (
            func.attr == "getenv" and isinstance(func.value, ast.Name) and func.value.id in state.os_module_aliases
        ):
            _record_env_read(_env_key_expression(node), scope, state)
    elif isinstance(func, ast.Name) and func.id in state.getenv_aliases:
        _record_env_read(_env_key_expression(node), scope, state)


def _scan_node(node: ast.AST, scope: tuple[str, ...], state: _ScanState) -> None:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        scope = (*scope, node.name)
    elif isinstance(node, ast.Call):
        _classify_call(node, _scope_marker(scope), state)
    elif isinstance(node, ast.Subscript) and _is_environ_expression(node.value, state):
        _record_env_read(node.slice, _scope_marker(scope), state)
    for child in ast.iter_child_nodes(node):
        _scan_node(child, scope, state)


def _scope_marker(scope: tuple[str, ...]) -> str:
    return ".".join(scope) if scope else MODULE_LEVEL_MARKER


def scan_env_reads(source: str, filename: str) -> EnvReadScan:
    """Scan Python *source* for environment reads (pure function).

    Detected read forms: ``os.environ[...]``, ``os.environ.get(...)``,
    ``os.getenv(...)`` and the ``from os import environ, getenv`` spellings
    (aliases included).
    """
    tree = ast.parse(source, filename=filename)
    environ_aliases, getenv_aliases, os_module_aliases = _collect_import_aliases(tree)
    state = _ScanState(
        environ_aliases=environ_aliases,
        getenv_aliases=getenv_aliases,
        os_module_aliases=os_module_aliases,
    )
    _scan_node(tree, (), state)
    return EnvReadScan(
        literal_flag_reads=tuple(state.literal_flag_reads),
        dynamic_read_sites=tuple(state.dynamic_read_sites),
    )


def classify_scan(
    scan: EnvReadScan,
    rel_path: str,
    registered: frozenset[str],
    dynamic_allowlist: frozenset[tuple[str, str]],
) -> tuple[list[str], list[str]]:
    """Classify one file's scan into fence violations (pure function).

    Returns:
        ``(unregistered_literal_reads, unallowlisted_dynamic_sites)`` as
        human-readable violation descriptions.
    """
    unregistered = [
        f"{flag_name} (read in {rel_path}, function {scope})"
        for flag_name, scope in scan.literal_flag_reads
        if flag_name not in registered
    ]
    dynamic_violations = [
        f'("{rel_path}", "{scope}")'
        for scope in dict.fromkeys(scan.dynamic_read_sites)
        if (rel_path, scope) not in dynamic_allowlist
    ]
    return unregistered, dynamic_violations


def iter_production_python_files() -> Iterator[Path]:
    """Yield the production (non-test) Python files under ``polaris/``."""
    for path in sorted(POLARIS_ROOT.rglob("*.py")):
        parts = path.relative_to(BACKEND_ROOT).parts
        if "__pycache__" in parts or "tests" in parts:
            continue
        if path.name.startswith("test_") or path.name == "conftest.py":
            continue
        yield path


def _scan_production_tree() -> tuple[list[str], list[str]]:
    from polaris.kernelone.config.flag_registry import (
        DYNAMIC_ENV_READ_ALLOWLIST,
        registered_flag_names,
    )

    registered = registered_flag_names()
    dynamic_allowlist = frozenset(DYNAMIC_ENV_READ_ALLOWLIST)
    unregistered: list[str] = []
    dynamic_violations: list[str] = []
    for path in iter_production_python_files():
        rel_path = path.relative_to(BACKEND_ROOT).as_posix()
        scan = scan_env_reads(path.read_text(encoding="utf-8"), filename=rel_path)
        file_unregistered, file_dynamic = classify_scan(scan, rel_path, registered, dynamic_allowlist)
        unregistered.extend(file_unregistered)
        dynamic_violations.extend(file_dynamic)
    return unregistered, dynamic_violations


def test_every_literal_kernelone_env_read_is_registered() -> None:
    unregistered, _ = _scan_production_tree()
    assert unregistered == [], (
        "Unregistered KERNELONE_* environment flag reads found in production code:\n"
        + "\n".join(sorted(unregistered))
        + "\n\n"
        + _HOW_TO_REGISTER_FLAG
    )


def test_every_dynamic_env_read_site_is_allowlisted() -> None:
    _, dynamic_violations = _scan_production_tree()
    assert dynamic_violations == [], (
        "Environment reads with dynamically composed names found outside DYNAMIC_ENV_READ_ALLOWLIST:\n"
        + "\n".join(sorted(dynamic_violations))
        + "\n\n"
        + _HOW_TO_ALLOWLIST_DYNAMIC_READ
    )


def test_registry_entries_are_canonical() -> None:
    from polaris.kernelone.config.flag_registry import KERNELONE_FLAG_REGISTRY

    for name, spec in KERNELONE_FLAG_REGISTRY.items():
        assert name.startswith(KERNELONE_FLAG_PREFIX), f"Registry key {name!r} lacks the KERNELONE_ prefix."
        assert spec.name == name, f"Registry key {name!r} does not match FlagSpec.name {spec.name!r}."
        assert spec.owner, f"Registry entry {name!r} must declare an owner."
        assert spec.purpose, f"Registry entry {name!r} must declare a purpose."
        assert spec.registered_at, f"Registry entry {name!r} must declare registered_at."


# ---------------------------------------------------------------------------
# Scanner self-tests: prove the fence actually detects unregistered reads.
# The synthetic source is scanned as a string; no fixture module is committed.
# ---------------------------------------------------------------------------

_SYNTHETIC_UNREGISTERED_FLAG = "KERNELONE_FENCE_SELFTEST_UNREGISTERED_FLAG"
_SYNTHETIC_REL_PATH = "polaris/fence_selftest/synthetic_module.py"
_SYNTHETIC_SOURCE = f'''
import os
from os import environ, getenv

MODULE_LEVEL = os.environ.get("{_SYNTHETIC_UNREGISTERED_FLAG}")


class Widget:
    def literal_reads(self) -> None:
        os.environ["KERNELONE_FENCE_SELFTEST_SUBSCRIPT"]
        os.getenv("KERNELONE_FENCE_SELFTEST_GETENV")
        environ.get("KERNELONE_FENCE_SELFTEST_BARE_ENVIRON")
        getenv("KERNELONE_FENCE_SELFTEST_BARE_GETENV")


def dynamic_read(name: str) -> str | None:
    return os.environ.get(name)


NOT_A_READ = "KERNELONE_FENCE_SELFTEST_STRING_CONSTANT_ONLY"
OTHER_PREFIX = os.environ.get("OTHERPREFIX_FENCE_SELFTEST")
'''


def _synthetic_scan() -> EnvReadScan:
    return scan_env_reads(_SYNTHETIC_SOURCE, filename=_SYNTHETIC_REL_PATH)


def test_scanner_detects_all_literal_read_forms_with_stable_scope_markers() -> None:
    scan = _synthetic_scan()
    assert scan.literal_flag_reads == (
        (_SYNTHETIC_UNREGISTERED_FLAG, MODULE_LEVEL_MARKER),
        ("KERNELONE_FENCE_SELFTEST_SUBSCRIPT", "Widget.literal_reads"),
        ("KERNELONE_FENCE_SELFTEST_GETENV", "Widget.literal_reads"),
        ("KERNELONE_FENCE_SELFTEST_BARE_ENVIRON", "Widget.literal_reads"),
        ("KERNELONE_FENCE_SELFTEST_BARE_GETENV", "Widget.literal_reads"),
    )


def test_scanner_records_dynamic_read_sites_by_enclosing_function() -> None:
    scan = _synthetic_scan()
    assert scan.dynamic_read_sites == ("dynamic_read",)


def test_scanner_ignores_plain_string_constants_and_foreign_prefixes() -> None:
    scan = _synthetic_scan()
    seen_names = {name for name, _ in scan.literal_flag_reads}
    assert "KERNELONE_FENCE_SELFTEST_STRING_CONSTANT_ONLY" not in seen_names
    assert not any("OTHERPREFIX" in name for name in seen_names)


def test_fence_flags_unregistered_flag_and_unallowlisted_dynamic_site() -> None:
    from polaris.kernelone.config.flag_registry import (
        DYNAMIC_ENV_READ_ALLOWLIST,
        is_registered,
        registered_flag_names,
    )

    assert not is_registered(_SYNTHETIC_UNREGISTERED_FLAG), (
        f"{_SYNTHETIC_UNREGISTERED_FLAG} must never be registered; it exists to prove the fence detects "
        "unregistered flags."
    )

    unregistered, dynamic_violations = classify_scan(
        _synthetic_scan(),
        rel_path=_SYNTHETIC_REL_PATH,
        registered=registered_flag_names(),
        dynamic_allowlist=frozenset(DYNAMIC_ENV_READ_ALLOWLIST),
    )
    assert any(_SYNTHETIC_UNREGISTERED_FLAG in violation for violation in unregistered)
    assert dynamic_violations == [f'("{_SYNTHETIC_REL_PATH}", "dynamic_read")']


def test_fence_passes_when_flag_registered_and_dynamic_site_allowlisted() -> None:
    unregistered, dynamic_violations = classify_scan(
        _synthetic_scan(),
        rel_path=_SYNTHETIC_REL_PATH,
        registered=frozenset(name for name, _ in _synthetic_scan().literal_flag_reads),
        dynamic_allowlist=frozenset({(_SYNTHETIC_REL_PATH, "dynamic_read")}),
    )
    assert unregistered == []
    assert dynamic_violations == []
