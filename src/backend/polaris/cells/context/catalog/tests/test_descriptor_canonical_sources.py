"""Descriptor selection follows Python imports and receipts use the bound runtime owner."""

from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest
from polaris.cells.context.catalog.internal import descriptor_pack_generator as generator
from polaris.kernelone.fs import KernelFileSystem


@pytest.fixture
def source_and_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    source, owner = tmp_path / "source", tmp_path / "owner"
    source.mkdir()
    owner.mkdir()
    cell = source / "polaris/cells/sample/runtime"
    cell.mkdir(parents=True)
    (cell / "cell.yaml").write_text(
        "id: sample.runtime\nowned_paths:\n  - polaris/cells/sample/runtime/**\n", encoding="utf-8"
    )
    monkeypatch.setenv("KERNELONE_WORKSPACE", str(owner))
    for name in ("KERNELONE_RUNTIME_ROOT", "KERNELONE_RUNTIME_CACHE_ROOT", "KERNELONE_RAMDISK_ROOT"):
        monkeypatch.delenv(name, raising=False)
    return source, owner, cell


@pytest.mark.asyncio
async def test_descriptor_uses_package_body_instead_of_shadowed_module(
    source_and_owner: tuple[Path, Path, Path],
) -> None:
    source, _owner, cell = source_and_owner
    (cell / "choice.py").write_text("def retired_shadow_only(): pass\n", encoding="utf-8")
    package = cell / "choice"
    package.mkdir()
    (package / "__init__.py").write_text("from ._core import current_operation\n", encoding="utf-8")
    (package / "_core.py").write_text("def current_operation(): return 1\n", encoding="utf-8")

    output = await generator.generate_pack(cell, source)

    assert output is not None
    pack = json.loads(output.read_text(encoding="utf-8"))
    assert [(item["name"], item["defined_in"]) for item in pack["capabilities"]] == [
        ("current_operation", "polaris/cells/sample/runtime/choice/_core.py")
    ]


@pytest.mark.asyncio
async def test_descriptor_does_not_index_namespace_hidden_by_a_real_module(
    source_and_owner: tuple[Path, Path, Path],
) -> None:
    source, _owner, cell = source_and_owner
    (cell / "choice.py").write_text("def current_operation(): return 1\n", encoding="utf-8")
    package = cell / "choice"
    package.mkdir()
    (package / "_core.py").write_text("def unreachable_namespace_child(): pass\n", encoding="utf-8")

    output = await generator.generate_pack(cell, source)

    assert output is not None
    pack = json.loads(output.read_text(encoding="utf-8"))
    assert [item["name"] for item in pack["capabilities"]] == ["current_operation"]


@pytest.mark.asyncio
async def test_descriptor_receipt_uses_configured_runtime_owner_and_kfs(
    source_and_owner: tuple[Path, Path, Path],
) -> None:
    source, owner, cell = source_and_owner
    (cell / "live.py").write_text("def current_operation(): pass\n", encoding="utf-8")

    output = await generator.generate_pack(cell, source)

    assert output is not None
    receipt = owner / ".polaris/runtime/index_receipts/sample.runtime_receipt.json"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["index_path"] == str(output)
    assert payload["descriptor_count"] == 1
    assert payload["verification_hash"] == generator._compute_verification_hash(output.read_text(encoding="utf-8"))
    assert not (source / ".polaris").exists()


@pytest.mark.asyncio
async def test_descriptor_without_runtime_owner_rejects_before_writes(
    source_and_owner: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _owner, cell = source_and_owner
    monkeypatch.delenv("KERNELONE_WORKSPACE")
    with pytest.raises(ValueError, match="runtime_owner_workspace"):
        await generator.generate_pack(cell, source)
    assert not (cell / "generated").exists()
    assert not (source / ".polaris").exists()


@pytest.mark.asyncio
async def test_receipt_write_failure_is_not_reported_as_generation_success(
    source_and_owner: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _owner, cell = source_and_owner

    def denied(*args, **kwargs):
        raise OSError("receipt denied")

    monkeypatch.setattr(KernelFileSystem, "write_text_atomic", denied)
    with pytest.raises(OSError, match="receipt denied"):
        await generator.generate_pack(cell, source)


@pytest.mark.asyncio
async def test_explicit_runtime_owner_takes_priority_over_configured_workspace(
    source_and_owner: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    source, configured_owner, cell = source_and_owner
    explicit_owner = tmp_path / "explicit-owner"
    explicit_owner.mkdir()
    result = await generator.generate_pack(cell, source, runtime_owner_workspace=explicit_owner)
    assert result is not None
    assert (explicit_owner / ".polaris/runtime/index_receipts/sample.runtime_receipt.json").is_file()
    assert not (configured_owner / ".polaris").exists()
    assert not (source / ".polaris").exists()


@pytest.mark.asyncio
async def test_receipt_is_committed_atomically_through_kfs_not_direct_open(
    source_and_owner: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, owner, cell = source_and_owner
    open_file = builtins.open

    def reject_direct_receipt_write(file, mode="r", *args, **kwargs):
        if str(file).endswith("_receipt.json") and any(flag in str(mode) for flag in "wax+"):
            raise AssertionError("direct receipt write bypasses KFS atomic commit")
        return open_file(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", reject_direct_receipt_write)
    result = await generator.generate_pack(cell, source)
    assert result is not None
    assert (owner / ".polaris/runtime/index_receipts/sample.runtime_receipt.json").is_file()


@pytest.mark.asyncio
async def test_rejected_storage_owner_does_not_publish_pack_or_receipt(
    source_and_owner: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, owner, cell = source_and_owner

    def deny_owner(_workspace):
        raise PermissionError("host storage owner denied")

    monkeypatch.setattr(generator, "resolve_storage_roots", deny_owner)
    with pytest.raises(PermissionError, match="host storage owner denied"):
        await generator.generate_pack(cell, source)
    assert not (cell / "generated").exists()
    assert not (owner / ".polaris").exists()
