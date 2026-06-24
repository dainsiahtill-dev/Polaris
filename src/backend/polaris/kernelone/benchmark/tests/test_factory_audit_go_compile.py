from __future__ import annotations

import shutil

import pytest
from polaris.kernelone.benchmark.factory_audit import _check_go_compile


@pytest.mark.skipif(shutil.which("go") is None, reason="go toolchain is not available")
def test_go_compile_without_module_compiles_each_package_directory(tmp_path) -> None:
    engine_dir = tmp_path / "src" / "engine"
    model_dir = tmp_path / "src" / "models"
    engine_dir.mkdir(parents=True)
    model_dir.mkdir(parents=True)
    (engine_dir / "engine.go").write_text(
        "package engine\n\nfunc Tick() int { return 1 }\n",
        encoding="utf-8",
    )
    (model_dir / "pet.go").write_text(
        'package models\n\nfunc PetName() string { return "momo" }\n',
        encoding="utf-8",
    )

    ok, detail = _check_go_compile(str(tmp_path))

    assert ok is True
    assert "per-directory go test" in detail
