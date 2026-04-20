from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from polaris.cells.context.catalog import ContextCatalogService

from .test_service import _write_catalog


def _load_script_module() -> Any:
    script_path = Path(__file__).resolve().parents[5] / "docs" / "scripts" / "check_context_catalog_descriptor_cache.py"
    spec = importlib.util.spec_from_file_location("check_context_catalog_descriptor_cache", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load descriptor cache script module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_descriptor_cache_script_returns_success_for_fresh_cache(tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    schema_dir = tmp_path / "docs" / "governance" / "schemas"
    schema_dir.mkdir(parents=True, exist_ok=True)
    repo_schema = (
        Path(__file__).resolve().parents[5] / "docs" / "governance" / "schemas" / "semantic-descriptor.schema.yaml"
    )
    schema_dir.joinpath("semantic-descriptor.schema.yaml").write_text(
        repo_schema.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    ContextCatalogService(str(tmp_path)).sync()

    module = _load_script_module()
    result = module.main(["--workspace", str(tmp_path)])

    assert result == 0
