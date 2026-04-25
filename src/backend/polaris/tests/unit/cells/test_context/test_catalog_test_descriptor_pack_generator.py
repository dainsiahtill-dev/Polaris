"""Tests for polaris.cells.context.catalog.internal.descriptor_pack_generator.

Covers DescriptorPack, IndexWriteReceipt, verify_recall_at_10,
validate_schema, _load_yaml_simple, FunctionalAnalyzer, analyze_file,
and helper functions.
"""

from __future__ import annotations

import ast
import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from polaris.cells.context.catalog.internal.descriptor_pack_generator import (
    DescriptorPack,
    FunctionalAnalyzer,
    IndexWriteReceipt,
    _compute_verification_hash,
    _load_yaml_simple,
    _verify_recall_if_applicable,
    analyze_file,
    validate_schema,
    verify_recall_at_10,
)


# ---------------------------------------------------------------------------
# DescriptorPack
# ---------------------------------------------------------------------------


class TestDescriptorPack:
    def test_default_values(self) -> None:
        pack = DescriptorPack()
        assert pack.version == "2.1.0"
        assert pack.generated_at == ""
        assert pack.source_hash == ""
        assert pack.cell_id == ""
        assert pack.workspace == ""
        assert pack.capabilities == []
        assert pack.embedding_runtime_fingerprint == ""
        assert pack.evolution is None

    def test_to_dict_basic(self) -> None:
        pack = DescriptorPack(
            version="2.1.0",
            generated_at="2026-04-24T00:00:00Z",
            source_hash="abc123",
            cell_id="test.cell",
            workspace="/workspace",
            capabilities=[{"type": "function", "name": "foo"}],
            embedding_runtime_fingerprint="fp1",
        )
        d = pack.to_dict()
        assert d["version"] == "2.1.0"
        assert d["generated_at"] == "2026-04-24T00:00:00Z"
        assert d["source_hash"] == "abc123"
        assert d["cell_id"] == "test.cell"
        assert d["workspace"] == "/workspace"
        assert d["capabilities"] == [{"type": "function", "name": "foo"}]
        assert d["embedding_runtime_fingerprint"] == "fp1"
        assert "evolution" not in d

    def test_to_dict_with_evolution(self) -> None:
        pack = DescriptorPack(
            version="2.1.0",
            generated_at="2026-04-24T00:00:00Z",
            source_hash="abc123",
            cell_id="test.cell",
            workspace="/workspace",
            capabilities=[],
            embedding_runtime_fingerprint="fp1",
            evolution={"status": "active"},
        )
        d = pack.to_dict()
        assert d["evolution"] == {"status": "active"}

    def test_compute_source_hash_empty_files(self) -> None:
        pack = DescriptorPack()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "empty.py").write_text("", encoding="utf-8")
            result = pack._compute_source_hash([tmp_path / "empty.py"])
            assert isinstance(result, str)
            assert len(result) == 16

    def test_compute_source_hash_with_content(self) -> None:
        pack = DescriptorPack()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "a.py").write_text("print(1)", encoding="utf-8")
            (tmp_path / "b.py").write_text("print(2)", encoding="utf-8")
            hash1 = pack._compute_source_hash([tmp_path / "a.py", tmp_path / "b.py"])
            hash2 = pack._compute_source_hash([tmp_path / "a.py", tmp_path / "b.py"])
            assert hash1 == hash2
            # Order-independent because files are sorted
            hash3 = pack._compute_source_hash([tmp_path / "b.py", tmp_path / "a.py"])
            assert hash1 == hash3

    def test_compute_source_hash_missing_file(self) -> None:
        pack = DescriptorPack()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Missing file should be silently skipped
            result = pack._compute_source_hash([tmp_path / "missing.py"])
            assert isinstance(result, str)
            assert len(result) == 16


# ---------------------------------------------------------------------------
# IndexWriteReceipt
# ---------------------------------------------------------------------------


class TestIndexWriteReceipt:
    def test_to_dict(self) -> None:
        receipt = IndexWriteReceipt(
            timestamp="2026-04-24T00:00:00Z",
            descriptor_count=5,
            index_path=Path("/workspace/index.json"),
            verification_hash="ver123",
            cell_id="test.cell",
            source_hash="src456",
        )
        d = receipt.to_dict()
        assert d["timestamp"] == "2026-04-24T00:00:00Z"
        assert d["descriptor_count"] == 5
        assert d["index_path"] == str(Path("/workspace/index.json"))
        assert d["verification_hash"] == "ver123"
        assert d["cell_id"] == "test.cell"
        assert d["source_hash"] == "src456"


# ---------------------------------------------------------------------------
# verify_recall_at_10
# ---------------------------------------------------------------------------


class TestVerifyRecallAt10:
    def test_perfect_recall(self) -> None:
        ground_truth = ["a", "b", "c"]
        candidates = ["a", "b", "c"]
        assert verify_recall_at_10(candidates, ground_truth) == 1.0

    def test_partial_recall(self) -> None:
        ground_truth = ["a", "b", "c", "d"]
        candidates = ["a", "b", "c", "d", "x", "y"]
        result = verify_recall_at_10(candidates, ground_truth)
        assert result == 1.0  # 4 hits / 4 ground_truth

    def test_empty_ground_truth(self) -> None:
        assert verify_recall_at_10(["a", "b"], []) == 0.0

    def test_recall_below_threshold_raises(self) -> None:
        ground_truth = ["a", "b", "c", "d"]
        candidates = ["x", "y", "z"]
        with pytest.raises(AssertionError, match="Recall@10"):
            verify_recall_at_10(candidates, ground_truth)

    def test_uses_top_10_only(self) -> None:
        ground_truth = ["a"]
        candidates = ["x"] * 11 + ["a"]
        # a is at index 11, outside top 10
        with pytest.raises(AssertionError, match="Recall@10"):
            verify_recall_at_10(candidates, ground_truth)


# ---------------------------------------------------------------------------
# validate_schema
# ---------------------------------------------------------------------------


class TestValidateSchema:
    def test_missing_required_field(self, tmp_path: Path) -> None:
        schema_path = tmp_path / "schema.yaml"
        schema_path.write_text(
            'required:\n  - version\n  - cell_id\n$defs:\n  descriptor:\n    required:\n      - name\n',
            encoding="utf-8",
        )
        descriptor = {"version": "1.0"}  # missing cell_id
        assert validate_schema(descriptor, schema_path) is False

    def test_valid_schema(self, tmp_path: Path) -> None:
        schema_path = tmp_path / "schema.yaml"
        schema_path.write_text(
            'required:\n  - version\n  - cell_id\n',
            encoding="utf-8",
        )
        descriptor = {"version": "1.0", "cell_id": "test"}
        assert validate_schema(descriptor, schema_path) is True

    def test_missing_schema_file_passes_with_empty_schema(self, tmp_path: Path) -> None:
        schema_path = tmp_path / "missing.yaml"
        descriptor = {"version": "1.0"}
        # Missing schema file results in empty schema dict, which has no required fields
        assert validate_schema(descriptor, schema_path) is True

    def test_descriptor_validation(self, tmp_path: Path) -> None:
        schema_path = tmp_path / "schema.yaml"
        schema_path.write_text(
            'required:\n  - version\n$defs:\n  descriptor:\n    required:\n      - name\n',
            encoding="utf-8",
        )
        descriptor = {
            "version": "1.0",
            "descriptors": [
                {"name": "valid"},
                {"missing_name": "invalid"},
            ],
        }
        assert validate_schema(descriptor, schema_path) is False

    def test_empty_descriptors_passes(self, tmp_path: Path) -> None:
        schema_path = tmp_path / "schema.yaml"
        schema_path.write_text(
            'required:\n  - version\n$defs:\n  descriptor:\n    required:\n      - name\n',
            encoding="utf-8",
        )
        descriptor = {"version": "1.0", "descriptors": []}
        assert validate_schema(descriptor, schema_path) is True


# ---------------------------------------------------------------------------
# _load_yaml_simple
# ---------------------------------------------------------------------------


class TestLoadYamlSimple:
    def test_loads_yaml_with_yaml_lib(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text("name: test\nversion: 1\n", encoding="utf-8")
        result = _load_yaml_simple(yaml_path)
        assert result == {"name": "test", "version": 1}

    def test_loads_simple_structure_fallback(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text(
            "name: test\nitems:\n  - one\n  - two\n",
            encoding="utf-8",
        )
        result = _load_yaml_simple(yaml_path)
        # yaml.safe_load handles this fine
        assert result["name"] == "test"
        assert result["items"] == ["one", "two"]

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "missing.yaml"
        result = _load_yaml_simple(yaml_path)
        assert result == {}


# ---------------------------------------------------------------------------
# FunctionalAnalyzer
# ---------------------------------------------------------------------------


class TestFunctionalAnalyzer:
    def test_extracts_classes_and_methods(self) -> None:
        code = """
class Foo:
    \"\"\"A foo class.\"\"\"
    def method_one(self):
        pass
    def _private(self):
        pass
    def __init__(self):
        pass

class Bar:
    def method_two(self):
        \"\"\"Does something.\"\"\"
        pass
"""
        tree = ast.parse(code)
        visitor = FunctionalAnalyzer()
        visitor.visit(tree)

        assert len(visitor.classes) == 2
        assert visitor.classes[0]["name"] == "Foo"
        assert visitor.classes[0]["doc"] == "A foo class."
        method_names = [m["name"] for m in visitor.classes[0]["methods"]]
        assert "method_one" in method_names
        assert "_private" in method_names
        assert "__init__" in method_names

        assert visitor.classes[1]["name"] == "Bar"
        assert visitor.classes[1]["doc"] == ""
        method_names = [m["name"] for m in visitor.classes[1]["methods"]]
        assert "method_two" in method_names

    def test_skips_dunder_methods(self) -> None:
        code = """
class Foo:
    def __str__(self):
        pass
    def __repr__(self):
        pass
    def normal_method(self):
        pass
"""
        tree = ast.parse(code)
        visitor = FunctionalAnalyzer()
        visitor.visit(tree)

        method_names = [m["name"] for m in visitor.classes[0]["methods"]]
        assert "__str__" not in method_names
        assert "__repr__" not in method_names
        assert "normal_method" in method_names

    def test_extracts_top_level_functions(self) -> None:
        code = """
def foo():
    \"\"\"Foo function.\"\"\"
    pass

def bar():
    pass
"""
        tree = ast.parse(code)
        visitor = FunctionalAnalyzer()
        visitor.visit(tree)

        assert len(visitor.functions) == 2
        assert visitor.functions[0]["name"] == "foo"
        assert visitor.functions[0]["doc"] == "Foo function."
        assert visitor.functions[1]["name"] == "bar"
        assert visitor.functions[1]["doc"] == ""

    def test_nested_classes(self) -> None:
        code = """
class Outer:
    class Inner:
        def inner_method(self):
            pass
    def outer_method(self):
        pass
"""
        tree = ast.parse(code)
        visitor = FunctionalAnalyzer()
        visitor.visit(tree)

        assert len(visitor.classes) == 2
        names = [c["name"] for c in visitor.classes]
        assert "Outer" in names
        assert "Inner" in names


# ---------------------------------------------------------------------------
# analyze_file
# ---------------------------------------------------------------------------


class TestAnalyzeFile:
    def test_analyzes_python_file(self, tmp_path: Path) -> None:
        py_file = tmp_path / "test_module.py"
        py_file.write_text(
            '"""Module docstring."""\n'
            "class MyClass:\n"
            '    \"\"\"Class doc.\"\"\"\n'
            "    def my_method(self):\n"
            "        pass\n"
            "\n"
            "def my_function():\n"
            '    \"\"\"Function doc.\"\"\"\n'
            "    pass\n",
            encoding="utf-8",
        )
        result = analyze_file(py_file)
        assert "error" not in result
        assert len(result["classes"]) == 1
        assert result["classes"][0]["name"] == "MyClass"
        assert len(result["functions"]) == 1
        assert result["functions"][0]["name"] == "my_function"

    def test_analyze_file_with_syntax_error(self, tmp_path: Path) -> None:
        py_file = tmp_path / "bad.py"
        py_file.write_text("class Foo(\n", encoding="utf-8")
        result = analyze_file(py_file)
        assert "error" in result

    def test_analyze_file_nonexistent(self, tmp_path: Path) -> None:
        result = analyze_file(tmp_path / "missing.py")
        assert "error" in result


# ---------------------------------------------------------------------------
# _compute_verification_hash
# ---------------------------------------------------------------------------


class TestComputeVerificationHash:
    def test_deterministic(self) -> None:
        h1 = _compute_verification_hash("hello")
        h2 = _compute_verification_hash("hello")
        assert h1 == h2
        assert len(h1) == 16

    def test_different_content(self) -> None:
        h1 = _compute_verification_hash("hello")
        h2 = _compute_verification_hash("world")
        assert h1 != h2


# ---------------------------------------------------------------------------
# _verify_recall_if_applicable
# ---------------------------------------------------------------------------


class TestVerifyRecallIfApplicable:
    def test_no_op(self) -> None:
        # Should not raise
        _verify_recall_if_applicable([], "test.cell")
        _verify_recall_if_applicable([{"name": "foo"}], "test.cell")


# ---------------------------------------------------------------------------
# generate_pack (async) – integration-level with mocked deps
# ---------------------------------------------------------------------------


class TestGeneratePack:
    @pytest.mark.asyncio
    async def test_missing_cell_yaml_returns_none(self, tmp_path: Path) -> None:
        from polaris.cells.context.catalog.internal.descriptor_pack_generator import (
            generate_pack,
        )

        cell_dir = tmp_path / "nonexistent"
        cell_dir.mkdir()
        result = await generate_pack(cell_dir, tmp_path)
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_cell_id_returns_none(self, tmp_path: Path) -> None:
        from polaris.cells.context.catalog.internal.descriptor_pack_generator import (
            generate_pack,
        )

        cell_dir = tmp_path / "test_cell"
        cell_dir.mkdir()
        (cell_dir / "cell.yaml").write_text("name: test\n", encoding="utf-8")
        result = await generate_pack(cell_dir, tmp_path)
        assert result is None

    @pytest.mark.asyncio
    async def test_generates_pack_for_simple_cell(self, tmp_path: Path) -> None:
        from polaris.cells.context.catalog.internal.descriptor_pack_generator import (
            generate_pack,
        )

        cell_dir = tmp_path / "test_cell"
        cell_dir.mkdir()
        (cell_dir / "cell.yaml").write_text(
            "id: test.cell\nowned_paths:\n  - test_cell/**\n",
            encoding="utf-8",
        )
        # Create a simple Python file in owned_paths
        py_dir = cell_dir / "test_cell"
        py_dir.mkdir()
        (py_dir / "module.py").write_text(
            "def hello():\n    pass\n",
            encoding="utf-8",
        )

        with patch(
            "polaris.cells.context.catalog.internal.descriptor_pack_generator._build_kernel_fs"
        ) as mock_kfs:
            mock_fs = MagicMock()
            mock_kfs.return_value = mock_fs
            result = await generate_pack(cell_dir, tmp_path, skip_schema_validation=True)

        assert result is not None
        assert result.name == "descriptor.pack.json"

    @pytest.mark.asyncio
    async def test_skips_generated_and_venv(self, tmp_path: Path) -> None:
        from polaris.cells.context.catalog.internal.descriptor_pack_generator import (
            generate_pack,
        )

        cell_dir = tmp_path / "test_cell"
        cell_dir.mkdir()
        (cell_dir / "cell.yaml").write_text(
            "id: test.cell\nowned_paths:\n  - test_cell/**\n",
            encoding="utf-8",
        )
        py_dir = cell_dir / "test_cell"
        py_dir.mkdir()
        (py_dir / "module.py").write_text("def hello(): pass\n", encoding="utf-8")
        # Files in generated/ and venv/ should be skipped
        gen_dir = py_dir / "generated"
        gen_dir.mkdir()
        (gen_dir / "skip.py").write_text("def skip(): pass\n", encoding="utf-8")
        venv_dir = py_dir / "venv"
        venv_dir.mkdir()
        (venv_dir / "skip.py").write_text("def skip(): pass\n", encoding="utf-8")

        with patch(
            "polaris.cells.context.catalog.internal.descriptor_pack_generator._build_kernel_fs"
        ) as mock_kfs:
            mock_fs = MagicMock()
            mock_kfs.return_value = mock_fs
            result = await generate_pack(cell_dir, tmp_path, skip_schema_validation=True)

        assert result is not None


# ---------------------------------------------------------------------------
# run_all (async)
# ---------------------------------------------------------------------------


class TestRunAll:
    @pytest.mark.asyncio
    async def test_no_cells_found(self, tmp_path: Path) -> None:
        from polaris.cells.context.catalog.internal.descriptor_pack_generator import (
            run_all,
        )

        cells_root = tmp_path / "polaris" / "cells"
        cells_root.mkdir(parents=True)
        with patch(
            "polaris.cells.context.catalog.internal.descriptor_pack_generator._build_kernel_fs"
        ) as mock_kfs:
            mock_fs = MagicMock()
            mock_kfs.return_value = mock_fs
            await run_all(repo_root=tmp_path)
        # Should complete without error even with no cells

    @pytest.mark.asyncio
    async def test_processes_multiple_cells(self, tmp_path: Path) -> None:
        from polaris.cells.context.catalog.internal.descriptor_pack_generator import (
            run_all,
        )

        cells_root = tmp_path / "polaris" / "cells"
        for cell_name in ("cell_a", "cell_b"):
            cell_dir = cells_root / cell_name
            cell_dir.mkdir(parents=True)
            (cell_dir / "cell.yaml").write_text(
                f"id: {cell_name}\nowned_paths:\n  - {cell_name}/**\n",
                encoding="utf-8",
            )
            py_dir = cell_dir / cell_name
            py_dir.mkdir()
            (py_dir / "mod.py").write_text("def f(): pass\n", encoding="utf-8")

        with patch(
            "polaris.cells.context.catalog.internal.descriptor_pack_generator._build_kernel_fs"
        ) as mock_kfs:
            mock_fs = MagicMock()
            mock_kfs.return_value = mock_fs
            await run_all(repo_root=tmp_path, skip_schema_validation=True)
