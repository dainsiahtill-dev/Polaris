"""Tests for polaris.cells.context.catalog.internal.skill_validator.dependency_verifier.

Covers DependencyType, DependencyNode, DependencyValidationResult,
DependencyVerificationEngine initialization, import extraction,
dependency classification, verification, and circular import detection.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.context.catalog.internal.skill_validator.dependency_verifier import (
    DependencyNode,
    DependencyType,
    DependencyValidationResult,
    DependencyVerificationEngine,
)

# ---------------------------------------------------------------------------
# DependencyType Enum
# ---------------------------------------------------------------------------


class TestDependencyType:
    def test_enum_values(self) -> None:
        assert DependencyType.STDLIB.name == "STDLIB"
        assert DependencyType.THIRD_PARTY.name == "THIRD_PARTY"
        assert DependencyType.INTERNAL.name == "INTERNAL"
        assert DependencyType.RELATIVE.name == "RELATIVE"
        assert DependencyType.TYPE_ONLY.name == "TYPE_ONLY"
        assert DependencyType.UNKNOWN.name == "UNKNOWN"

    def test_auto_values_are_unique(self) -> None:
        values = [member.value for member in DependencyType]
        assert len(values) == len(set(values))


# ---------------------------------------------------------------------------
# DependencyNode
# ---------------------------------------------------------------------------


class TestDependencyNode:
    def test_frozen_dataclass(self) -> None:
        node = DependencyNode(
            module_name="os",
            import_name="path",
            dependency_type=DependencyType.STDLIB,
            source_file="test.py",
            line_number=1,
            column_offset=0,
        )
        assert node.module_name == "os"
        assert node.relative_level == 0  # default

    def test_with_relative_level(self) -> None:
        node = DependencyNode(
            module_name="helpers",
            import_name="foo",
            dependency_type=DependencyType.RELATIVE,
            source_file="test.py",
            line_number=5,
            column_offset=4,
            relative_level=1,
        )
        assert node.relative_level == 1

    def test_immutable(self) -> None:
        node = DependencyNode(
            module_name="os",
            import_name="path",
            dependency_type=DependencyType.STDLIB,
            source_file="test.py",
            line_number=1,
            column_offset=0,
        )
        with pytest.raises(AttributeError):
            node.module_name = "sys"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# DependencyValidationResult
# ---------------------------------------------------------------------------


class TestDependencyValidationResult:
    def test_creation(self) -> None:
        node = DependencyNode(
            module_name="os",
            import_name="path",
            dependency_type=DependencyType.STDLIB,
            source_file="test.py",
            line_number=1,
            column_offset=0,
        )
        result = DependencyValidationResult(
            node=node,
            exists=True,
            version="3.11",
            issues=[],
            suggestion=None,
        )
        assert result.exists is True
        assert result.version == "3.11"
        assert result.issues == []
        assert result.suggestion is None


# ---------------------------------------------------------------------------
# DependencyVerificationEngine init
# ---------------------------------------------------------------------------


class TestDependencyVerificationEngineInit:
    def test_default_init(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        assert engine.project_root == tmp_path
        assert engine.internal_modules == set()
        assert engine.allowed_third_party == set()

    def test_init_with_custom_values(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(
            project_root=tmp_path,
            internal_modules=["polaris", "kernelone"],
            allowed_third_party=["pytest", "numpy"],
        )
        assert engine.internal_modules == {"polaris", "kernelone"}
        assert engine.allowed_third_party == {"pytest", "numpy"}


# ---------------------------------------------------------------------------
# _extract_imports
# ---------------------------------------------------------------------------


class TestExtractImports:
    def test_simple_import(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        code = "import os\nimport sys\n"
        nodes = engine._extract_imports(code, "test.py")
        module_names = {n.module_name for n in nodes}
        assert "os" in module_names
        assert "sys" in module_names

    def test_from_import(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        code = "from pathlib import Path\n"
        nodes = engine._extract_imports(code, "test.py")
        assert len(nodes) == 1
        assert nodes[0].module_name == "pathlib"
        assert nodes[0].import_name == "Path"

    def test_type_checking_imports(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        code = """
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from polaris.cells.runtime import Runtime
"""
        nodes = engine._extract_imports(code, "test.py")
        type_only = [n for n in nodes if n.dependency_type == DependencyType.TYPE_ONLY]
        assert len(type_only) >= 1
        assert any(n.module_name == "polaris.cells.runtime" for n in type_only)

    def test_typing_type_checking_attribute(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        code = """
import typing
if typing.TYPE_CHECKING:
    from os import PathLike
"""
        nodes = engine._extract_imports(code, "test.py")
        type_only = [n for n in nodes if n.dependency_type == DependencyType.TYPE_ONLY]
        assert any(n.module_name == "os" for n in type_only)

    def test_relative_import(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        # from . import helpers has node.module=None, so it is not captured by ImportFrom handler
        # from ..models import User has node.module="models" and level=2
        code = "from . import helpers\nfrom ..models import User\n"
        nodes = engine._extract_imports(code, "test.py")
        relative = [n for n in nodes if n.dependency_type == DependencyType.RELATIVE]
        # Only the second import is captured (module is not None)
        assert len(relative) == 1
        assert relative[0].module_name == "models"
        assert relative[0].relative_level == 2

    def test_syntax_error_returns_empty(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        code = "class Foo(\n"
        nodes = engine._extract_imports(code, "test.py")
        assert nodes == []

    def test_line_and_column_info(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        code = "import os\n"
        nodes = engine._extract_imports(code, "test.py")
        assert len(nodes) == 1
        assert nodes[0].line_number == 1
        assert nodes[0].column_offset == 0


# ---------------------------------------------------------------------------
# _classify_dependency
# ---------------------------------------------------------------------------


class TestClassifyDependency:
    def test_relative_import(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        assert engine._classify_dependency("helpers", relative_level=1) == DependencyType.RELATIVE

    def test_internal_module(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(
            project_root=tmp_path,
            internal_modules=["polaris"],
        )
        assert engine._classify_dependency("polaris.cells.runtime") == DependencyType.INTERNAL

    def test_stdlib_module(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        result = engine._classify_dependency("os")
        assert result == DependencyType.STDLIB

    def test_third_party_module(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        result = engine._classify_dependency("nonexistent_package_xyz")
        assert result == DependencyType.THIRD_PARTY


# ---------------------------------------------------------------------------
# _is_stdlib
# ---------------------------------------------------------------------------


class TestIsStdlib:
    def test_known_stdlib(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        assert engine._is_stdlib("os") is True
        assert engine._is_stdlib("sys") is True
        assert engine._is_stdlib("pathlib") is True

    def test_non_stdlib(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        assert engine._is_stdlib("nonexistent_package_xyz") is False


# ---------------------------------------------------------------------------
# verify_dependencies
# ---------------------------------------------------------------------------


class TestVerifyDependencies:
    def test_stdlib_imports_always_exist(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        code = "import os\nimport sys\n"
        results = engine.verify_dependencies(code, "test.py")
        assert all(r.exists for r in results)
        assert all(r.node.dependency_type == DependencyType.STDLIB for r in results)

    def test_type_only_skipped(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        code = """
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from nonexistent_fake_module import XYZ
"""
        results = engine.verify_dependencies(code, "test.py")
        type_only_results = [r for r in results if r.node.dependency_type == DependencyType.TYPE_ONLY]
        assert all(r.exists for r in type_only_results)

    def test_hallucinated_internal_module(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(
            project_root=tmp_path,
            internal_modules=["polaris"],
        )
        code = "from polaris.nonexistent.module import Thing\n"
        results = engine.verify_dependencies(code, "test.py")
        internal_results = [r for r in results if r.node.dependency_type == DependencyType.INTERNAL]
        assert len(internal_results) == 1
        assert internal_results[0].exists is False
        assert any("not found" in issue for issue in internal_results[0].issues)

    def test_allowed_third_party_filter(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(
            project_root=tmp_path,
            allowed_third_party=["pytest"],
        )
        code = "import numpy\n"
        results = engine.verify_dependencies(code, "test.py")
        tp_results = [r for r in results if r.node.dependency_type == DependencyType.THIRD_PARTY]
        if tp_results:
            assert any("not in allowed list" in issue for r in tp_results for issue in r.issues)


# ---------------------------------------------------------------------------
# _check_relative_import_exists
# ---------------------------------------------------------------------------


class TestCheckRelativeImportExists:
    def test_relative_import_exists(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        # Create a file structure: pkg/__init__.py
        pkg_dir = tmp_path / "pkg"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text("", encoding="utf-8")

        node = DependencyNode(
            module_name="pkg",
            import_name="pkg",
            dependency_type=DependencyType.RELATIVE,
            source_file=str(tmp_path / "test.py"),
            line_number=1,
            column_offset=0,
            relative_level=1,
        )
        assert engine._check_relative_import_exists(node) is True

    def test_relative_import_missing(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        node = DependencyNode(
            module_name="missing",
            import_name="missing",
            dependency_type=DependencyType.RELATIVE,
            source_file=str(tmp_path / "test.py"),
            line_number=1,
            column_offset=0,
            relative_level=1,
        )
        assert engine._check_relative_import_exists(node) is False

    def test_level_2_relative(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        # Create: a/b/__init__.py
        b_dir = tmp_path / "a" / "b"
        b_dir.mkdir(parents=True)
        (b_dir / "__init__.py").write_text("", encoding="utf-8")

        source_file = tmp_path / "a" / "c" / "test.py"
        source_file.parent.mkdir(parents=True, exist_ok=True)

        node = DependencyNode(
            module_name="b",
            import_name="b",
            dependency_type=DependencyType.RELATIVE,
            source_file=str(source_file),
            line_number=1,
            column_offset=0,
            relative_level=2,
        )
        assert engine._check_relative_import_exists(node) is True


# ---------------------------------------------------------------------------
# _check_internal_module_exists
# ---------------------------------------------------------------------------


class TestCheckInternalModuleExists:
    def test_internal_module_as_file(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        mod_dir = tmp_path / "mymod"
        mod_dir.mkdir()
        (mod_dir / "__init__.py").write_text("", encoding="utf-8")

        node = DependencyNode(
            module_name="mymod",
            import_name="mymod",
            dependency_type=DependencyType.INTERNAL,
            source_file="test.py",
            line_number=1,
            column_offset=0,
        )
        assert engine._check_internal_module_exists(node) is True

    def test_internal_module_as_directory(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        mod_dir = tmp_path / "pkg" / "sub"
        mod_dir.mkdir(parents=True)
        (mod_dir / "__init__.py").write_text("", encoding="utf-8")

        node = DependencyNode(
            module_name="pkg.sub",
            import_name="sub",
            dependency_type=DependencyType.INTERNAL,
            source_file="test.py",
            line_number=1,
            column_offset=0,
        )
        assert engine._check_internal_module_exists(node) is True

    def test_internal_module_missing(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        node = DependencyNode(
            module_name="nonexistent",
            import_name="nonexistent",
            dependency_type=DependencyType.INTERNAL,
            source_file="test.py",
            line_number=1,
            column_offset=0,
        )
        assert engine._check_internal_module_exists(node) is False


# ---------------------------------------------------------------------------
# _get_installed_packages
# ---------------------------------------------------------------------------


class TestGetInstalledPackages:
    def test_returns_dict(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        packages = engine._get_installed_packages()
        assert isinstance(packages, dict)
        # pytest should be installed since we're running tests
        assert "pytest" in packages or "pip" in packages or "setuptools" in packages


# ---------------------------------------------------------------------------
# detect_circular_imports
# ---------------------------------------------------------------------------


class TestDetectCircularImports:
    def test_no_cycles(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(
            project_root=tmp_path,
            internal_modules=["mypkg"],
        )
        pkg_dir = tmp_path / "mypkg"
        pkg_dir.mkdir()
        # Create two files with no circular imports
        a_file = pkg_dir / "a.py"
        a_file.write_text("from mypkg.b import thing\n", encoding="utf-8")
        b_file = pkg_dir / "b.py"
        b_file.write_text("# no imports\n", encoding="utf-8")

        cycles = engine.detect_circular_imports([a_file, b_file])
        assert cycles == []

    def test_detects_simple_cycle(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(
            project_root=tmp_path,
            internal_modules=["mypkg"],
        )
        pkg_dir = tmp_path / "mypkg"
        pkg_dir.mkdir()
        a_file = pkg_dir / "a.py"
        a_file.write_text("from mypkg.b import thing\n", encoding="utf-8")
        b_file = pkg_dir / "b.py"
        b_file.write_text("from mypkg.a import thing\n", encoding="utf-8")

        cycles = engine.detect_circular_imports([a_file, b_file])
        assert len(cycles) > 0
        # Each cycle should contain the files involved
        flat_cycles = [item for cycle in cycles for item in cycle]
        assert any("a" in str(item) for item in flat_cycles)
        assert any("b" in str(item) for item in flat_cycles)

    def test_ignores_type_only_imports(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(
            project_root=tmp_path,
            internal_modules=["mypkg"],
        )
        pkg_dir = tmp_path / "mypkg"
        pkg_dir.mkdir()
        a_file = pkg_dir / "a.py"
        a_file.write_text(
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from mypkg.b import thing\n",
            encoding="utf-8",
        )
        b_file = pkg_dir / "b.py"
        b_file.write_text("from mypkg.a import thing\n", encoding="utf-8")

        cycles = engine.detect_circular_imports([a_file, b_file])
        # TYPE_ONLY import from a to b should be ignored, so no cycle
        assert cycles == []

    def test_non_py_files_ignored(self, tmp_path: Path) -> None:
        engine = DependencyVerificationEngine(project_root=tmp_path)
        txt_file = tmp_path / "readme.txt"
        txt_file.write_text("import a\n", encoding="utf-8")

        cycles = engine.detect_circular_imports([txt_file])
        assert cycles == []
