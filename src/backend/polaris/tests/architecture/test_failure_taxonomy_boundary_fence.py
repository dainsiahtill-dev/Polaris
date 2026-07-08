"""Architecture fence for platform failure-class taxonomy ownership.

The cross-layer execution-control taxonomy is
``control_plane.run_ledger.public.failure_evidence.FailureClassV1``. Domain-local
failure enums may exist only when their ownership boundary is explicit and they
must not become a second QA/Factory verdict vocabulary.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"

CANONICAL_FAILURE_TAXONOMY = POLARIS_ROOT / "cells" / "control_plane" / "run_ledger" / "public" / "failure_evidence.py"
QA_VERDICT_ENGINE = POLARIS_ROOT / "cells" / "qa" / "audit_verdict" / "internal" / "verdict_engine.py"

OWNED_FAILURE_CLASS_DEFINITIONS = {
    "FailureClassV1": "polaris/cells/control_plane/run_ledger/public/failure_evidence.py",
    "TaskBoundaryFailureClassV1": "polaris/cells/control_plane/run_ledger/public/task_boundary.py",
    "QaFailureClassV1": "polaris/cells/qa/audit_verdict/public/contracts.py",
    "AuditFailureClass": "polaris/kernelone/audit/error_correlator.py",
    "TurnFailureClass": "polaris/cells/roles/kernel/public/turn_contracts.py",
    "SequentialFailureClass": "polaris/cells/roles/runtime/internal/sequential_engine.py",
}

LOCAL_FAILURE_CLASS_NAMES = {
    "AuditFailureClass",
    "TurnFailureClass",
    "SequentialFailureClass",
}

LOCAL_FAILURE_CLASS_BOUNDARY_DECISIONS = {
    "AuditFailureClass": {
        "decision": "retain_local_enum",
        "boundary": "kernelone.audit.error_correlator",
        "allowed_imports": (),
    },
    "TurnFailureClass": {
        "decision": "retain_local_enum",
        "boundary": "roles.kernel.turn_continuation",
        "allowed_imports": (
            "polaris/cells/roles/kernel/internal/transaction/ledger.py",
            "polaris/cells/roles/runtime/internal/continuation_policy.py",
            "polaris/cells/roles/runtime/internal/session_orchestrator.py",
        ),
    },
    "SequentialFailureClass": {
        "decision": "retain_local_enum",
        "boundary": "roles.runtime.sequential_engine",
        "allowed_imports": (
            "polaris/cells/roles/adapters/internal/director/adapter_sequential.py",
            "polaris/cells/roles/runtime/public/service.py",
        ),
    },
}

LOCAL_FAILURE_CLASS_FIELD_USAGE_ALLOWLIST = {
    "polaris/cells/roles/adapters/internal/director/adapter_sequential.py",
    "polaris/cells/roles/kernel/internal/transaction/ledger.py",
    "polaris/cells/roles/kernel/public/turn_contracts.py",
    "polaris/cells/roles/runtime/internal/continuation_policy.py",
    "polaris/cells/roles/runtime/internal/session_orchestrator.py",
}


@dataclass(frozen=True)
class ClassDefinition:
    """Class definition discovered by the AST scanner."""

    name: str
    path: str
    line: int
    docstring: str


def _production_python_files(root: Path) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if not any(part in {"tests", "generated", "__pycache__"} for part in path.parts)
    ]


def _parse_python(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _failure_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_").casefold()


def _enum_string_values(path: Path, class_name: str) -> tuple[str, ...]:
    values: list[str] = []
    tree = _parse_python(path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for statement in node.body:
            if not isinstance(statement, ast.Assign):
                continue
            if isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
                values.append(statement.value.value)
        break
    return tuple(values)


def _failure_class_definitions(root: Path) -> list[ClassDefinition]:
    definitions: list[ClassDefinition] = []
    for path in _production_python_files(root):
        tree = _parse_python(path)
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name == "FailureClassV1" or node.name.endswith(("FailureClass", "FailureClassV1")):
                definitions.append(
                    ClassDefinition(
                        name=node.name,
                        path=relative,
                        line=node.lineno,
                        docstring=ast.get_docstring(node) or "",
                    )
                )
    return definitions


def _imported_names(path: Path) -> set[str]:
    names: set[str] = set()
    tree = _parse_python(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.rsplit(".", 1)[-1])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _contains_failure_class_field_usage(path: Path) -> bool:
    return "failure_class" in path.read_text(encoding="utf-8")


def _local_failure_class_imports(root: Path) -> dict[str, tuple[str, ...]]:
    imports: dict[str, list[str]] = {name: [] for name in LOCAL_FAILURE_CLASS_NAMES}
    for path in _production_python_files(root):
        imported = _imported_names(path)
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        for name in LOCAL_FAILURE_CLASS_NAMES:
            if name in imported:
                imports[name].append(relative)
    return {name: tuple(sorted(paths)) for name, paths in imports.items()}


def test_failure_class_definitions_are_explicitly_owned() -> None:
    """Any production failure-class enum must be either canonical or boundary-owned."""

    actual = {
        definition.name: definition.path
        for definition in _failure_class_definitions(POLARIS_ROOT)
        if not definition.name.startswith("Test")
    }

    assert actual == OWNED_FAILURE_CLASS_DEFINITIONS


def test_local_failure_class_boundary_decisions_are_complete() -> None:
    """Each local enum has an explicit retain/rename decision and boundary."""

    assert set(LOCAL_FAILURE_CLASS_BOUNDARY_DECISIONS) == LOCAL_FAILURE_CLASS_NAMES
    for name, decision in LOCAL_FAILURE_CLASS_BOUNDARY_DECISIONS.items():
        assert name in LOCAL_FAILURE_CLASS_NAMES
        assert decision["decision"] == "retain_local_enum"
        assert str(decision["boundary"]).strip()


def test_local_failure_class_imports_stay_inside_boundary_decisions() -> None:
    """Local failure enums may only be imported by their declared owners."""

    actual = _local_failure_class_imports(POLARIS_ROOT)
    expected = {
        name: tuple(sorted(str(path) for path in decision["allowed_imports"]))
        for name, decision in LOCAL_FAILURE_CLASS_BOUNDARY_DECISIONS.items()
    }

    assert actual == expected


def test_local_failure_class_field_usage_stays_inside_turn_boundaries() -> None:
    """Local failure enums may not silently become cross-layer failure_class fields."""

    offenders: list[str] = []
    for path in _production_python_files(POLARIS_ROOT):
        imported = _imported_names(path)
        if not imported.intersection(LOCAL_FAILURE_CLASS_NAMES):
            continue
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        if _contains_failure_class_field_usage(path) and relative not in LOCAL_FAILURE_CLASS_FIELD_USAGE_ALLOWLIST:
            offenders.append(relative)

    assert sorted(offenders) == []


def test_canonical_failure_taxonomy_remains_run_ledger_owned() -> None:
    """Run Ledger remains the canonical cross-layer failure evidence owner."""

    source = CANONICAL_FAILURE_TAXONOMY.read_text(encoding="utf-8")

    assert "class FailureClassV1(str, Enum)" in source
    assert "class FailureEvidenceV1" in source
    assert "def normalize_failure_class" in source
    assert "def is_failure_class" in source


def test_qa_verdict_engine_uses_run_ledger_failure_taxonomy() -> None:
    """QA may classify failures through Run Ledger terms, not local role enums."""

    imported = _imported_names(QA_VERDICT_ENGINE)

    assert "FailureClassV1" in imported
    assert "is_failure_class" in imported
    assert imported.isdisjoint(LOCAL_FAILURE_CLASS_NAMES)


def test_local_failure_classes_do_not_leak_into_qa_or_factory() -> None:
    """QA and Factory projections must not consume local role/audit failure enums."""

    offenders: list[str] = []
    scoped_roots = (
        POLARIS_ROOT / "cells" / "qa",
        POLARIS_ROOT / "cells" / "factory",
        POLARIS_ROOT / "delivery" / "http" / "routers",
    )
    for root in scoped_roots:
        for path in _production_python_files(root):
            imported = _imported_names(path)
            leaked = sorted(imported.intersection(LOCAL_FAILURE_CLASS_NAMES))
            if leaked:
                relative = path.relative_to(BACKEND_ROOT).as_posix()
                offenders.append(f"{relative}: {', '.join(leaked)}")

    assert offenders == []


def test_local_failure_classes_document_run_ledger_boundary() -> None:
    """Local failure enums must be visibly fenced from Run Ledger taxonomy."""

    missing: list[str] = []
    for definition in _failure_class_definitions(POLARIS_ROOT):
        if definition.name not in LOCAL_FAILURE_CLASS_NAMES:
            continue
        doc = definition.docstring.lower()
        if "local" not in doc or "run ledger" not in doc or "failure taxonomy" not in doc:
            missing.append(f"{definition.path}:{definition.line}:{definition.name}")

    assert missing == []


def test_local_failure_class_values_do_not_shadow_run_ledger_taxonomy() -> None:
    """Local enum values must not normalize to canonical Run Ledger failures."""

    canonical_values = _enum_string_values(CANONICAL_FAILURE_TAXONOMY, "FailureClassV1")
    canonical_keys = {_failure_key(value) for value in canonical_values}
    collisions: list[str] = []

    for class_name in sorted(LOCAL_FAILURE_CLASS_NAMES):
        relative_path = OWNED_FAILURE_CLASS_DEFINITIONS[class_name]
        enum_values = _enum_string_values(BACKEND_ROOT / relative_path, class_name)
        for value in enum_values:
            if _failure_key(value) in canonical_keys:
                collisions.append(f"{relative_path}:{class_name}.{value}")

    assert collisions == []


# ---------------------------------------------------------------------------
# Broad-scope regression fence: no roles.* production file outside Run Ledger
# public + task_boundary may hand-write failure_class string literals.
# ---------------------------------------------------------------------------

_ROLES_ECOSYSTEM_ROOTS = (
    POLARIS_ROOT / "cells" / "roles" / "runtime",
    POLARIS_ROOT / "cells" / "roles" / "adapters",
    POLARIS_ROOT / "cells" / "roles" / "kernel",
)
_RUN_LEDGER_AND_TASK_BOUNDARY = {
    "polaris/cells/control_plane/run_ledger/public/failure_evidence.py",
    "polaris/cells/control_plane/run_ledger/public/task_boundary.py",
    "polaris/cells/control_plane/run_ledger/public/tool_lifecycle.py",
}
# Known WS6 gaps — hand-written failure_class string literals that have not
# yet been migrated to Run Ledger public enums.  New entries must not be added;
# each entry represents a WS6 debt item.
# 2026-07-08: WS6 pm_adapter.py gaps resolved — enum members
# FailureClassV1.QUALITY_GATE_BLOCKED and FailureClassV1.ROLE_ADAPTER_EXCEPTION
# now consumed directly.
_KNOWN_FAILURE_CLASS_STRING_LITERAL_GAPS: frozenset[str] = frozenset()


def _roles_ecosystem_production_python_files() -> list[Path]:
    files: list[Path] = []
    for root in _ROLES_ECOSYSTEM_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if any(part in {"tests", "generated", "__pycache__"} for part in path.parts):
                continue
            files.append(path)
    return files


def _failure_class_string_literal_hits_in_file(path: Path) -> list[tuple[int, str]]:
    """Return (line, description) for bare ``failure_class`` string literal usage."""
    hits: list[tuple[int, str]] = []
    tree = _parse_python(path)
    for node in ast.walk(tree):
        # Pattern 1: failure_class = "literal_string" assignment
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "failure_class"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    hits.append((node.lineno, f'failure_class = "{node.value.value}"'))
        # Pattern 2: keyword arg failure_class="literal_string"
        if (
            isinstance(node, ast.keyword)
            and node.arg == "failure_class"
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            hits.append((node.lineno, f'failure_class="{node.value.value}"'))
        # Pattern 3: dict literal {"failure_class": "literal_string"}
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=False):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "failure_class"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    hits.append((node.lineno, f'"failure_class": "{value.value}"'))
    return hits


def test_roles_ecosystem_does_not_hand_write_failure_class_string_literals() -> None:
    """Roles ecosystem production files must not hand-write failure_class
    string literals.

    All failure classifications must route through Run Ledger public
    ``FailureClassV1``, ``TaskBoundaryFailureClassV1``, or ``QaFailureClassV1``
    enum values.  Bare ``failure_class = "..."`` or ``{"failure_class": "..."}``
    literals are a reclassification bypass that escapes the canonical taxonomy.
    """

    offenders: list[str] = []
    for path in _roles_ecosystem_production_python_files():
        hits = _failure_class_string_literal_hits_in_file(path)
        if not hits:
            continue
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        for line, desc in hits:
            key = f"{rel}:{line}"
            if key in _KNOWN_FAILURE_CLASS_STRING_LITERAL_GAPS:
                continue
            offenders.append(f"{key}: {desc}")

    assert offenders == [], (
        "roles.* production files must use Run Ledger public enum values for "
        "failure_class instead of hand-writing bare string literals. "
        f"Known gaps ({len(_KNOWN_FAILURE_CLASS_STRING_LITERAL_GAPS)}): "
        + "; ".join(sorted(_KNOWN_FAILURE_CLASS_STRING_LITERAL_GAPS))
        + ". New offenders: "
        + "; ".join(offenders)
    )


def test_failure_evidence_summary_not_locally_constructed_outside_run_ledger() -> None:
    """No roles.adapters production file may locally construct a
    ``failure_evidence_summary`` metadata key.

    ``failure_evidence_summary`` generation is owned by Run Ledger public
    ``append_failure_evidence_to_metadata`` / ``merge_failure_evidence_payload``.
    Consumers may *forward* the already-generated summary, but must not build
    summary shapes locally or through domain-specific summarizers.
    """

    adapters_root = POLARIS_ROOT / "cells" / "roles" / "adapters" / "internal"
    offenders: list[str] = []

    for path in sorted(adapters_root.rglob("*.py")):
        if any(part in {"tests", "generated", "__pycache__"} for part in path.parts):
            continue
        tree = _parse_python(path)
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        for node in ast.walk(tree):
            # Detect metadata["failure_evidence_summary"] = expr assignments
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if not isinstance(target, ast.Subscript):
                        continue
                    if not isinstance(target.value, ast.Name):
                        continue
                    if target.value.id != "metadata":
                        continue
                    key = target.slice
                    if isinstance(key, ast.Constant) and key.value == "failure_evidence_summary":
                        # Must be reading from metadata.get(...) (forwarding),
                        # not constructing a new dict/summary
                        is_forward = (
                            isinstance(node.value, ast.Call)
                            and isinstance(node.value.func, ast.Attribute)
                            and node.value.func.attr == "get"
                        )
                        if not is_forward:
                            offenders.append(
                                f"{rel}:{node.lineno}: metadata['failure_evidence_summary'] local construct"
                            )

    assert offenders == [], (
        "roles.adapters production files must not locally construct "
        "failure_evidence_summary metadata; that projection is owned by "
        "Run Ledger public helpers.  Consumers may forward "
        "completion_metadata.get('failure_evidence_summary') but must not "
        "build the shape locally. Offenders: " + "; ".join(offenders)
    )
