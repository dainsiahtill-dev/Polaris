"""Architecture fence for platform failure-class taxonomy ownership.

The cross-layer execution-control taxonomy is
``control_plane.run_ledger.public.failure_evidence.FailureClassV1``. Domain-local
failure enums may exist only when their ownership boundary is explicit and they
must not become a second QA/Factory verdict vocabulary.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"

CANONICAL_FAILURE_TAXONOMY = (
    POLARIS_ROOT / "cells" / "control_plane" / "run_ledger" / "public" / "failure_evidence.py"
)
QA_VERDICT_ENGINE = POLARIS_ROOT / "cells" / "qa" / "audit_verdict" / "internal" / "verdict_engine.py"

OWNED_FAILURE_CLASS_DEFINITIONS = {
    "FailureClassV1": "polaris/cells/control_plane/run_ledger/public/failure_evidence.py",
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


def _failure_class_definitions(root: Path) -> list[ClassDefinition]:
    definitions: list[ClassDefinition] = []
    for path in _production_python_files(root):
        tree = _parse_python(path)
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name == "FailureClassV1" or node.name.endswith("FailureClass"):
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
