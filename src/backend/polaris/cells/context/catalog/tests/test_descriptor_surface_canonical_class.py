"""Declared descriptor classes resolve source statically; no imported mutants."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from polaris.cells.context.catalog.internal import descriptor_pack_generator as generator
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs import KernelFileSystem


def write(root: Path, relative: str, text: str) -> None:
    filesystem = KernelFileSystem(str(root), LocalFileSystemAdapter())
    filesystem.workspace_write_text(relative, text, encoding="utf-8")


@pytest.fixture
def sample(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    filesystem = KernelFileSystem(str(tmp_path), LocalFileSystemAdapter())
    for name in ("source", "owner"):
        filesystem.workspace_mkdir(name)
    source, owner = tmp_path / "source", tmp_path / "owner"
    cell = source / "polaris/cells/sample/runtime"
    write(
        source,
        "polaris/cells/sample/runtime/cell.yaml",
        yaml.safe_dump(
            {
                "id": "sample.runtime",
                "owned_paths": ["polaris/cells/sample/runtime/**"],
                "descriptor_surface": {
                    "include_public_contracts": False,
                    "classes": {"Service": {"module": "polaris.cells.sample.runtime.public.api", "methods": ["work"]}},
                },
            }
        ),
    )
    for key in ("KERNELONE_RUNTIME_ROOT", "KERNELONE_RUNTIME_CACHE_ROOT", "KERNELONE_RAMDISK_ROOT"):
        monkeypatch.delenv(key, raising=False)
    return source, owner, cell


async def generated(sample: tuple[Path, Path, Path]) -> dict[str, Any]:
    source, owner, cell = sample
    result = await generator.generate_pack(
        cell, source, evolve=False, skip_schema_validation=False, runtime_owner_workspace=owner
    )
    assert result is not None
    return json.loads(
        KernelFileSystem(str(source), LocalFileSystemAdapter()).workspace_read_text(str(result), encoding="utf-8")
    )


def test_actual_task_runtime_manifest_requires_service_source_instead_of_empty_surface() -> None:
    backend = Path(__file__).resolve().parents[5]
    cell = backend / "polaris/cells/runtime/task_runtime"
    data = generator._load_yaml_simple(cell / "cell.yaml")
    selected = generator._declared_descriptor_surface(cell_data=data, cell_dir=cell, repo_root=backend)
    assert selected is not None
    assert "TaskRuntimeService" in selected[1], "declared runtime service cannot silently disappear"


@pytest.mark.asyncio
async def test_package_alias_and_inherited_method_publish_only_declared_surface(
    sample: tuple[Path, Path, Path],
) -> None:
    source, _owner, _cell = sample
    prefix = "polaris/cells/sample/runtime/"
    write(source, prefix + "public/api.py", "class Service:\n    def wrong_shadow(self): pass\n")
    write(
        source,
        prefix + "public/api/__init__.py",
        "raise RuntimeError('never import this mutant')\nfrom ...internal.core import Concrete as Service\n",
    )
    write(
        source,
        prefix + "internal/core.py",
        "from .mixins import Base\nclass Concrete(Base):\n    def __init__(self): pass\n    def reset_records(self): pass\n    def utility(self): pass\n",
    )
    write(
        source,
        prefix + "internal/mixins.py",
        'class Base:\n    def work(self):\n        """Actual inherited operation."""\n        return 1\n    def hidden(self): pass\n',
    )
    pack = await generated(sample)
    assert len(pack["capabilities"]) == 1
    service = pack["capabilities"][0]
    assert service["name"] == "Service"
    assert service["defined_in"] == prefix + "internal/core.py"
    assert service["methods"] == [{"name": "work", "doc": "Actual inherited operation."}]


@pytest.mark.asyncio
async def test_unresolved_required_method_rejects_before_pack_or_receipt_writes(
    sample: tuple[Path, Path, Path],
) -> None:
    source, owner, cell = sample
    write(source, "polaris/cells/sample/runtime/public/api.py", "class Service:\n    def other(self): pass\n")
    write(source, "polaris/cells/sample/runtime/generated/descriptor.pack.json", "UNCHANGED EXISTING PACK\n")
    with pytest.raises(ValueError, match=r"(required|method|resolve)"):
        await generated(sample)
    filesystem = KernelFileSystem(str(source), LocalFileSystemAdapter())
    assert (
        filesystem.workspace_read_text(str(cell / "generated/descriptor.pack.json"), encoding="utf-8")
        == "UNCHANGED EXISTING PACK\n"
    )
    assert not (owner / ".polaris/runtime/index_receipts/sample.runtime_receipt.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("style", ["import_alias", "attribute_alias", "assignment_alias"])
async def test_multilevel_literal_aliases_resolve_without_importing_source(
    sample: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, style: str
) -> None:
    import builtins

    source, _owner, _cell = sample
    prefix = "polaris/cells/sample/runtime/"
    statement = {
        "import_alias": "from ..internal.bridge import Export as Service\n",
        "attribute_alias": "import polaris.cells.sample.runtime.internal.core as impl\nService = impl.Concrete\n",
        "assignment_alias": "from ..internal.core import Concrete\nSelected = Concrete\nService = Selected\n",
    }[style]
    write(source, prefix + "public/api.py", "raise RuntimeError('audited mutant cannot execute')\n" + statement)
    write(source, prefix + "internal/bridge.py", "from . import core as impl\nExport = impl.Concrete\n")
    write(
        source,
        prefix + "internal/core.py",
        'class Concrete:\n    def work(self):\n        """Canonical alias operation."""\n        return 1\n',
    )
    imports = []
    original = builtins.__import__

    def observe(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("polaris.cells.sample"):
            imports.append(name)
            raise AssertionError("audited source import is forbidden")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", observe)
    pack = await generated(sample)
    assert pack["capabilities"][0]["defined_in"] == prefix + "internal/core.py"
    assert pack["capabilities"][0]["methods"] == [{"name": "work", "doc": "Canonical alias operation."}]
    assert imports == []


@pytest.mark.asyncio
async def test_c3_mro_and_definition_time_alias_binding_follow_python_source_order(
    sample: tuple[Path, Path, Path],
) -> None:
    source, _owner, _cell = sample
    relative = "polaris/cells/sample/runtime/public/api.py"
    write(
        source,
        relative,
        'class Root:\n    def work(self):\n        """Root operation."""\n        return 0\nclass Left(Root): pass\nclass Right(Root):\n    def work(self):\n        """Right operation wins C3."""\n        return 1\nclass Service(Left, Right): pass\n',
    )
    pack = await generated(sample)
    assert pack["capabilities"][0]["methods"][0]["doc"] == "Right operation wins C3."
    write(
        source,
        relative,
        'class Base:\n    def work(self):\n        """Original captured base."""\n        return 1\nclass Service(Base): pass\nclass Replacement:\n    def work(self):\n        """Rebound later."""\n        return 2\nBase = Replacement\n',
    )
    captured = await generated(sample)
    assert captured["capabilities"][0]["methods"][0]["doc"] == "Original captured base."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    [
        "missing_module",
        "missing_class",
        "alias_cycle",
        "inheritance_cycle",
        "typing_stub",
        "unknown_branch",
        "shadowed_method",
        "inconsistent_mro",
        "module_shadows_namespace",
    ],
)
async def test_unresolved_declared_class_or_mro_is_explicit_failure_without_publication(
    sample: tuple[Path, Path, Path], defect: str
) -> None:
    source, owner, cell = sample
    prefix = "polaris/cells/sample/runtime/"
    statements = {
        "missing_module": None,
        "missing_class": "class Other:\n    def work(self): pass\n",
        "alias_cycle": "from ..internal.loop import Service\n",
        "inheritance_cycle": "from ..internal.loop import Base\nclass Service(Base):\n    def work(self): pass\n",
        "typing_stub": "from typing import TYPE_CHECKING as TC\nif TC:\n    class Service:\n        def work(self): ...\nelse:\n    class Service: pass\n",
        "unknown_branch": "if opaque_condition:\n    class Service:\n        def work(self): pass\n",
        "shadowed_method": "class Base:\n    def work(self): return 1\nclass Service(Base):\n    work = None\n",
        "inconsistent_mro": "class A: pass\nclass B: pass\nclass X(A,B): pass\nclass Y(B,A): pass\nclass Service(X,Y):\n    def work(self): pass\n",
        "module_shadows_namespace": "class Service:\n    def wrong(self): pass\n",
    }
    statement = statements[defect]
    if statement is not None:
        write(source, prefix + "public/api.py", statement)
    if defect == "alias_cycle":
        write(source, prefix + "internal/loop.py", "from ..public.api import Service\n")
    if defect == "inheritance_cycle":
        write(source, prefix + "internal/loop.py", "from ..public.api import Service\nclass Base(Service): pass\n")
    if defect == "module_shadows_namespace":
        write(source, prefix + "public/api/hidden.py", "class Service:\n    def work(self): pass\n")
        data = generator._load_yaml_simple(cell / "cell.yaml")
        data["descriptor_surface"]["classes"]["Service"]["module"] += ".hidden"
        write(source, prefix + "cell.yaml", yaml.safe_dump(data))
    with pytest.raises(ValueError, match=r"(required|resolve|cycle|shadow|MRO)"):
        await generated(sample)
    assert not (cell / "generated").exists()
    assert not (owner / ".polaris/runtime/index_receipts/sample.runtime_receipt.json").exists()


@pytest.mark.asyncio
async def test_runtime_empty_typing_base_contributes_no_stub_methods_and_sources_affect_hash(
    sample: tuple[Path, Path, Path],
) -> None:
    source, _owner, _cell = sample
    prefix = "polaris/cells/sample/runtime/"
    write(
        source,
        prefix + "public/api.py",
        "from ..internal.base import Empty\nfrom ..internal.ops import Operations\nclass Service(Operations, Empty): pass\n",
    )
    write(
        source,
        prefix + "internal/base.py",
        "import typing as t\nif t.TYPE_CHECKING:\n    class Empty:\n        def work(self): ...\nelse:\n    class Empty: pass\n",
    )
    write(
        source,
        prefix + "internal/ops.py",
        'class Operations:\n    async def work(self):\n        """Real async method."""\n        return 1\n',
    )
    first = await generated(sample)
    assert first["capabilities"][0]["methods"] == [{"name": "work", "doc": "Real async method."}]
    write(
        source,
        prefix + "internal/ops.py",
        'class Operations:\n    async def work(self):\n        """Changed actual definition."""\n        return 2\n',
    )
    second = await generated(sample)
    assert second["source_hash"] != first["source_hash"]
    assert second["capabilities"][0]["methods"][0]["doc"] == "Changed actual definition."


def test_actual_task_runtime_nine_method_sources_match_current_mro_locators() -> None:
    from polaris.cells.context.catalog.internal.canonical_python_sources import CanonicalPythonSources

    backend = Path(__file__).resolve().parents[5]
    cell = backend / "polaris/cells/runtime/task_runtime"
    data = generator._load_yaml_simple(cell / "cell.yaml")
    policy = data["descriptor_surface"]["classes"]["TaskRuntimeService"]
    resolved = CanonicalPythonSources(backend).resolve_class(
        policy["module"], "TaskRuntimeService", tuple(policy["methods"])
    )
    expected = {
        "create_task_row": "_mixin_recovery_reexec.py",
        "update_task_row": "_mixin_recovery_reexec.py",
        "reopen_task_row": "_mixin_task_rows.py",
        "get_task": "_mixin_recovery_reexec.py",
        "list_ready_task_rows": "_mixin_dependency_session.py",
        "list_observable_task_rows": "_mixin_task_rows.py",
        "query_same_task_local_rework_authorization": "_mixin_task_rows.py",
        "get_task_row_stats": "_mixin_dependency_session.py",
        "claim_next_execution": "_mixin_execution.py",
    }
    assert resolved.path.name == "_service.py"
    assert {method.name: method.path.name for method in resolved.methods} == expected
    assert len(resolved.methods) == len(policy["methods"]) == 9
    assert "__init__" not in {method.name for method in resolved.methods}


def test_adjacent_engineering_service_surface_keeps_declared_getters_without_regenerating_its_pack() -> None:
    from polaris.cells.context.catalog.internal.canonical_python_sources import CanonicalPythonSources

    backend = Path(__file__).resolve().parents[5]
    cell = backend / "polaris/cells/roles/runtime"
    data = generator._load_yaml_simple(cell / "cell.yaml")
    policy = data["descriptor_surface"]["classes"]["EngineeringRunService"]
    resolved = CanonicalPythonSources(backend).resolve_class(
        policy["module"], "EngineeringRunService", tuple(policy["methods"])
    )
    assert [method.name for method in resolved.methods] == policy["methods"]
    assert resolved.path == backend / "polaris/cells/roles/runtime/public/engineering_service.py"


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["surface_type", "classes_type"])
async def test_malformed_explicit_surface_cannot_fall_back_to_owned_internals(
    sample: tuple[Path, Path, Path], defect: str
) -> None:
    source, owner, cell = sample
    write(
        source,
        "polaris/cells/sample/runtime/public/api.py",
        "class Service:\n    def work(self): pass\n    def reset_records(self): pass\n",
    )
    data = generator._load_yaml_simple(cell / "cell.yaml")
    if defect == "surface_type":
        data["descriptor_surface"] = []
    else:
        data["descriptor_surface"]["classes"] = []
    write(source, "polaris/cells/sample/runtime/cell.yaml", yaml.safe_dump(data))
    with pytest.raises(ValueError, match="descriptor"):
        await generated(sample)
    assert not (cell / "generated").exists()
    assert not (owner / ".polaris").exists()


_BINDING_RUNTIME_CASES = {
    "valid": ("class Service:\n    def work(self): return 1\n", True),
    "deleted_member": ("class Service:\n    def work(self): return 1\n    del work\n", False),
    "tuple_rebind": ("class Service:\n    def work(self): return 1\n    work, other = None, None\n", False),
    "nested_unpack": (
        "class Service:\n    def work(self): return 1\n    (work, other), tail = (None, None), None\n",
        False,
    ),
    "starred_unpack": ("class Service:\n    def work(self): return 1\n    *others, work = None, None\n", False),
    "unknown_unpack": (
        "def values(): return (None, None)\nclass Service:\n    def work(self): return 1\n    work, other = values()\n",
        False,
    ),
    "deleted_module_alias": ("class Original:\n    def work(self): return 1\nService = Original\ndel Service\n", False),
    "module_tuple_rebind": ("class Service:\n    def work(self): return 1\nService, other = None, None\n", False),
    "module_unknown_unpack": (
        "def values(): return (None, None)\nclass Service:\n    def work(self): return 1\nService, other = values()\n",
        False,
    ),
    "deleted_override_uses_base": (
        "class Base:\n    def work(self): return 1\nclass Service(Base):\n    def work(self): return 2\n    del work\n",
        True,
    ),
    "delete_then_rebind_alias": (
        "class Service:\n    def work(self): return 1\n    saved = work\n    del work\n    work = saved\n",
        True,
    ),
    "known_unpack_alias": (
        "class Service:\n    def original(self): return 1\n    work, other = original, None\n",
        True,
    ),
    "module_unpack_alias": ("class Original:\n    def work(self): return 1\nService, other = Original, None\n", True),
    "annotation_only": ("class Service:\n    def work(self): return 1\n    work: object\n", True),
    "captured_alias_survives_delete": (
        "class Original:\n    def work(self): return 1\nService = Original\ndel Original\n",
        True,
    ),
    "unknown_if_delete": (
        "def condition(): return True\nclass Service:\n    def work(self): return 1\n    if condition():\n        del work\n",
        False,
    ),
}


@pytest.mark.parametrize("case", list(_BINDING_RUNTIME_CASES))
def test_static_binding_updates_match_controlled_runtime_without_executing_audited_modules(
    sample: tuple[Path, Path, Path], case: str
) -> None:
    from polaris.cells.context.catalog.internal.canonical_python_sources import CanonicalPythonSources

    source, _owner, _cell = sample
    text, expected = _BINDING_RUNTIME_CASES[case]
    # Only authored I/O-free class/assignment controls execute here. The source
    # resolver never imports or executes any inspected module or mutant fixture.
    namespace: dict[str, Any] = {}
    exec(text, namespace)
    current = namespace.get("Service")
    assert callable(getattr(current, "work", None)) is expected
    write(source, "polaris/cells/sample/runtime/public/api.py", text)
    resolver = CanonicalPythonSources(source)
    if not expected:
        with pytest.raises(ValueError, match=r"required|resolve|binding|method"):
            resolver.resolve_class("polaris.cells.sample.runtime.public.api", "Service", ("work",))
    else:
        resolved = resolver.resolve_class("polaris.cells.sample.runtime.public.api", "Service", ("work",))
        assert [method.name for method in resolved.methods] == ["work"]
        if case == "deleted_override_uses_base":
            assert resolved.methods[0].node.lineno == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["deleted_member", "tuple_rebind", "unknown_unpack"])
async def test_invalidated_required_binding_never_publishes_a_partial_descriptor(
    sample: tuple[Path, Path, Path], case: str
) -> None:
    source, owner, cell = sample
    text, _expected = _BINDING_RUNTIME_CASES[case]
    write(source, "polaris/cells/sample/runtime/public/api.py", text)
    with pytest.raises(ValueError, match=r"required|resolve|binding|method"):
        await generated(sample)
    assert not (cell / "generated").exists()
    assert not (owner / ".polaris/runtime/index_receipts/sample.runtime_receipt.json").exists()


@pytest.mark.parametrize("deletions", ["del work", "del work\n    del work"])
def test_unbound_class_local_delete_cannot_borrow_a_global_or_inherited_method(
    sample: tuple[Path, Path, Path], deletions: str
) -> None:
    from polaris.cells.context.catalog.internal.canonical_python_sources import CanonicalPythonSources

    source, _owner, _cell = sample
    text = (
        "def work(): return 0\nclass Base:\n    def work(self): return 1\nclass Service(Base):\n    " + deletions + "\n"
    )
    with pytest.raises(NameError):
        exec(text, {})
    write(source, "polaris/cells/sample/runtime/public/api.py", text)
    with pytest.raises(ValueError, match=r"required|resolve|binding|method|deletion"):
        CanonicalPythonSources(source).resolve_class("polaris.cells.sample.runtime.public.api", "Service", ("work",))
